//! One outer broker supervises one disposable Session worker and its network sockets.
use super::{
    dns::Routes,
    namespace::{self, Mounts},
    policy::Policy,
    proxy::Proxy,
    tls::Authority,
    worker::{WorkerConfig, client::Client},
};
use std::{
    io::{self, Write},
    os::{
        fd::OwnedFd,
        unix::{net::UnixStream, process::CommandExt},
    },
    process::{Child, Command, Stdio},
    sync::Arc,
    time::Duration,
};
use tokio::{
    io::unix::AsyncFd,
    sync::{oneshot, watch},
    task::JoinSet,
};

pub(crate) struct Runtime {
    pub client: Client,
    policy: Arc<Policy>,
    clean: Arc<std::sync::atomic::AtomicBool>,
    stop: Option<oneshot::Sender<()>>,
    dead: watch::Receiver<bool>,
}

// Own the process across every bootstrap error and cancelled spawn_blocking future.
struct ChildGuard(Child);
impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

impl Runtime {
    pub async fn start(
        mut config: WorkerConfig,
        input: Policy,
        hide: Vec<std::path::PathBuf>,
        mut reservation: crate::daemon::egress::Reservation,
    ) -> io::Result<Self> {
        if let Some(command) = &mut config.command {
            let safe = |name: &String, _: &mut String| {
                matches!(
                    name.as_str(),
                    "PATH" | "LANG" | "LC_ALL" | "LC_CTYPE" | "TERM" | "COLORTERM"
                )
            };
            command.base_environment.retain(safe);
            command
                .base_environment
                .insert("HOME".into(), config.working_directory.clone());
            command
                .base_environment
                .insert("TMPDIR".into(), "/tmp".into());
            for profile in &mut command.shell_profiles {
                profile.safe_base_environment.retain(safe);
            }
        }
        namespace::protect_process()?;
        let policy = Arc::new(input);
        let authority = Authority::new()?;
        let plan = Mounts {
            workspace: config.working_directory.clone().into(),
            hide,
            ca_pem: authority.pem(),
        };
        let limits = config.limits.clone();
        let (stop_child, stopped_child) = std::sync::mpsc::channel::<()>();
        let (booted, boot) = oneshot::channel();
        // Linux PDEATHSIG follows the spawning thread, not merely its process.
        // Keep that thread alive for the complete child lifetime.
        let child = tokio::task::spawn_blocking(move || match bootstrap(plan, config) {
            Ok((child, socket, fds)) => {
                if booted.send(Ok((socket, fds))).is_ok() {
                    let _ = stopped_child.recv();
                }
                drop(child);
            }
            Err(error) => {
                let _ = booted.send(Err(error));
            }
        });
        let (socket, [tcp, udp, dns, death]) = boot.await.map_err(io::Error::other)??;
        let tcp = std::net::TcpListener::from(tcp);
        let dns = std::net::UdpSocket::from(dns);
        tcp.set_nonblocking(true)?;
        dns.set_nonblocking(true)?;
        socket.set_nonblocking(true)?;
        let tcp = tokio::net::TcpListener::from_std(tcp)?;
        let dns = tokio::net::UdpSocket::from_std(dns)?;
        let death = AsyncFd::new(death)?;
        let socket = tokio::net::UnixStream::from_std(socket)?;
        let client =
            tokio::time::timeout(Duration::from_secs(15), Client::connect(socket, &limits))
                .await
                .map_err(io::Error::other)??;
        let mut closed = client.closed();
        let routes = Arc::new(Routes::default());
        let proxy = Proxy::new(policy.clone(), authority);
        let mut tasks = JoinSet::new();
        tasks.spawn(namespace::tcp::serve(tcp, routes.clone(), proxy));
        tasks.spawn(namespace::udp::serve(udp, routes.clone(), policy.clone()));
        tasks.spawn(routes.serve(dns, policy.clone()));
        let (stop, stopped) = oneshot::channel();
        let (dead_sender, dead) = watch::channel(false);
        let owned_policy = policy.clone();
        let clean = reservation.clean.clone();
        reservation.started = true;
        tokio::spawn(async move {
            tokio::select! {
                _ = stopped => {},
                _ = closed.changed() => {},
                _ = death.readable() => {},
                _ = tasks.join_next() => {},
            }
            owned_policy.close();
            tasks.abort_all();
            // unshare --kill-child kills namespace PID 1 when its supervisor dies.
            // The pidfd is the definitive worker-death fence, not the wrapper's exit.
            let _ = tokio::time::timeout(Duration::from_secs(4), death.readable()).await;
            drop(stop_child);
            let _ = child.await;
            let _ = death.readable().await;
            drop(reservation);
            dead_sender.send_replace(true);
        });
        Ok(Self {
            clean,
            client,
            policy,
            stop: Some(stop),
            dead,
        })
    }

    pub fn status(&self) -> io::Result<crate::eip::EgressStatus> {
        self.policy
            .snapshot()
            .map(|snapshot| snapshot.status())
            .map_err(|_| io::Error::other("closed policy"))
    }
    pub fn update(
        &self,
        params: crate::eip::EgressUpdateParams,
    ) -> Result<crate::eip::EgressStatus, super::policy::PolicyError> {
        self.policy
            .update(params.into())
            .map(|snapshot| snapshot.status())
    }
    pub fn environment(&self) -> io::Result<std::collections::BTreeMap<String, String>> {
        let mut env = self
            .policy
            .snapshot()
            .map_err(|_| io::Error::other("closed policy"))?
            .environment();
        for name in [
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "NODE_EXTRA_CA_CERTS",
        ] {
            env.insert(name.into(), format!("{}/ca.pem", namespace::RUNTIME));
        }
        Ok(env)
    }
    pub async fn close(&self) -> bool {
        self.policy.close();
        if let Ok(Ok(true)) =
            tokio::time::timeout(Duration::from_secs(4), self.client.close_worker()).await
        {
            self.clean.store(true, std::sync::atomic::Ordering::Release);
        }

        self.client.shutdown();
        let mut dead = self.dead.clone();
        while !*dead.borrow_and_update() {
            if dead.changed().await.is_err() {
                return false;
            }
        }
        // Worker death fences execution, but cannot prove persistent staging cleanup.
        self.clean.load(std::sync::atomic::Ordering::Acquire)
    }
}
impl Drop for Runtime {
    fn drop(&mut self) {
        self.policy.close();
        self.stop.take();
    }
}

fn bootstrap(
    plan: Mounts,
    config: WorkerConfig,
) -> io::Result<(ChildGuard, UnixStream, [OwnedFd; 4])> {
    let (mut socket, input) = UnixStream::pair()?;
    socket.set_read_timeout(Some(Duration::from_secs(20)))?;
    socket.set_write_timeout(Some(Duration::from_secs(20)))?;
    let parent = unsafe { libc::getpid() };
    let mut command = Command::new("/usr/bin/unshare");
    if unsafe { libc::geteuid() } == 0 {
        // Container runtimes can reject procfs mounted by a nested user namespace.
        // Mount procfs in the new PID namespace first; the inner unshare still
        // gives the worker its own user, mount, and network namespaces.
        command.args([
            "--mount",
            "--pid",
            "--fork",
            "--kill-child",
            "--mount-proc",
            "/usr/bin/unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--net",
        ]);
    } else {
        command.args([
            "--user",
            "--map-root-user",
            "--net",
            "--mount",
            "--pid",
            "--fork",
            "--kill-child",
            "--mount-proc",
        ]);
    }
    command
        .arg(std::env::current_exe()?)
        .arg("--internal-egress-worker")
        .stdin(Stdio::from(OwnedFd::from(input)))
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin");
    unsafe {
        command.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 || libc::getppid() != parent
            {
                return Err(io::Error::other("broker exited"));
            }
            Ok(())
        });
    }
    let child = ChildGuard(command.spawn()?);
    write(&mut socket, &plan)?;
    let fds = namespace::fds::receive(&socket)?;
    write(&mut socket, &config)?;
    socket.set_read_timeout(None)?;
    socket.set_write_timeout(None)?;
    Ok((child, socket, fds))
}
fn write(socket: &mut UnixStream, value: &impl serde::Serialize) -> io::Result<()> {
    let bytes = serde_json::to_vec(value).map_err(io::Error::other)?;
    socket.write_all(&(bytes.len() as u32).to_be_bytes())?;
    socket.write_all(&bytes)
}
