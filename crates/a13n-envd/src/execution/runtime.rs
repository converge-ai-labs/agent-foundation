//! One outer broker supervises one disposable Session worker and its network sockets.
#[cfg(target_os = "linux")]
use super::namespace::{self, Mounts};
use super::worker::{WorkerConfig, client::Client};
#[cfg(target_os = "linux")]
use crate::egress::{dns::Routes, policy::Policy, proxy::Proxy, tls::Authority};
use std::{
    io,
    os::{fd::OwnedFd, unix::net::UnixStream},
    process::Stdio,
    sync::Arc,
    time::Duration,
};
#[cfg(target_os = "linux")]
use std::{
    io::Write,
    os::unix::process::CommandExt,
    process::{Child, Command},
};
use tokio::sync::{oneshot, watch};
#[cfg(target_os = "linux")]
use tokio::{io::unix::AsyncFd, task::JoinSet};

pub(crate) struct Runtime {
    pub client: Client,
    #[cfg(target_os = "linux")]
    policy: Option<Arc<Policy>>,
    clean: Arc<std::sync::atomic::AtomicBool>,
    stop: Option<oneshot::Sender<()>>,
    dead: watch::Receiver<bool>,
}

// Own the process across every bootstrap error and cancelled spawn_blocking future.
#[cfg(target_os = "linux")]
struct ChildGuard {
    child: Child,
    _state: Option<crate::execution::StateDirectory>,
}
#[cfg(target_os = "linux")]
impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Runtime {
    #[cfg(target_os = "linux")]
    pub async fn start(
        mut config: WorkerConfig,
        input: Option<Policy>,
        hide: Vec<std::path::PathBuf>,
        mut reservation: crate::daemon::execution::Reservation,
    ) -> io::Result<Self> {
        if (input.is_some() || config.sandbox.restricted())
            && let Some(command) = &mut config.command
        {
            sanitize_environment(command);
            command
                .base_environment
                .insert("HOME".into(), config.working_directory.clone());
            command
                .base_environment
                .insert("TMPDIR".into(), "/tmp".into());
        }
        namespace::protect_process()?;
        let policy = input.map(Arc::new);
        let authority = policy.as_ref().map(|_| Authority::new()).transpose()?;
        let plan = config.managed.then(|| Mounts {
            workspace: config.working_directory.clone().into(),
            state: None,
            hide,
            network: authority.is_some(),
            ca_pem: authority.as_ref().map(Authority::pem).unwrap_or_default(),
        });
        if policy.is_some() && plan.is_none() {
            return Err(io::Error::other(
                "controlled Session requires a managed broker",
            ));
        }
        let limits = config.limits.clone();
        let (stop_child, stopped_child) = std::sync::mpsc::channel::<()>();
        let (booted, boot) = oneshot::channel();
        // Linux PDEATHSIG follows the spawning thread, not merely its process.
        // Keep that thread alive for the complete child lifetime.
        let child = tokio::task::spawn_blocking(move || match bootstrap(plan, config) {
            Ok((child, socket, network, death)) => {
                if booted.send(Ok((socket, network, death))).is_ok() {
                    let _ = stopped_child.recv();
                }
                drop(child);
            }
            Err(error) => {
                let _ = booted.send(Err(error));
            }
        });
        let (socket, network, death) = boot.await.map_err(io::Error::other)??;
        socket.set_nonblocking(true)?;
        let death = AsyncFd::new(death)?;
        let socket = tokio::net::UnixStream::from_std(socket)?;
        let client =
            tokio::time::timeout(Duration::from_secs(15), Client::connect(socket, &limits))
                .await
                .map_err(io::Error::other)??;
        let mut closed = client.closed();
        let mut tasks = JoinSet::new();
        if let Some([tcp, udp, dns]) = network {
            let policy = policy.as_ref().expect("network policy");
            let authority = authority.expect("network authority");
            let tcp = std::net::TcpListener::from(tcp);
            let dns = std::net::UdpSocket::from(dns);
            tcp.set_nonblocking(true)?;
            dns.set_nonblocking(true)?;
            let tcp = tokio::net::TcpListener::from_std(tcp)?;
            let dns = tokio::net::UdpSocket::from_std(dns)?;
            let routes = Arc::new(Routes::default());
            let proxy = Proxy::new(policy.clone(), authority);
            tasks.spawn(crate::egress::namespace::tcp::serve(
                tcp,
                routes.clone(),
                proxy,
            ));
            tasks.spawn(crate::egress::namespace::udp::serve(
                udp,
                routes.clone(),
                policy.clone(),
            ));
            tasks.spawn(routes.serve(dns, policy.clone()));
        }
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
                _ = tasks.join_next(), if !tasks.is_empty() => {},
            }
            if let Some(policy) = owned_policy {
                policy.close();
            }
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

    #[cfg(target_os = "macos")]
    pub async fn start(
        mut config: WorkerConfig,
        mut reservation: crate::daemon::execution::Reservation,
    ) -> io::Result<Self> {
        use tokio::io::AsyncWriteExt;
        let state = super::StateDirectory::new(None)?;
        config.runtime_directory = state.0.join("state");
        let home = state.0.join("home");
        let temporary = state.0.join("tmp");
        std::fs::create_dir(&home)?;
        std::fs::create_dir(&temporary)?;
        if let Some(command) = &mut config.command {
            sanitize_environment(command);
            command
                .base_environment
                .insert("HOME".into(), home.to_string_lossy().into_owned());
            command
                .base_environment
                .insert("TMPDIR".into(), temporary.to_string_lossy().into_owned());
        }
        let (socket, input) = UnixStream::pair()?;
        socket.set_nonblocking(true)?;
        let mut socket = tokio::net::UnixStream::from_std(socket)?;
        let mut command = tokio::process::Command::from(super::macos::command(
            &config.sandbox,
            &config.grant_sources,
            config.egress,
            &state.0,
        )?);
        let mut child = command
            .arg("--internal-session-worker")
            .env_clear()
            .stdin(Stdio::from(OwnedFd::from(input)))
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()?;
        let bytes = serde_json::to_vec(&config).map_err(io::Error::other)?;
        let connected = tokio::time::timeout(Duration::from_secs(15), async {
            socket
                .write_all(&(bytes.len() as u32).to_be_bytes())
                .await?;
            socket.write_all(&bytes).await?;
            Client::connect(socket, &config.limits).await
        })
        .await
        .map_err(io::Error::other)
        .and_then(|result| result);
        let client = match connected {
            Ok(client) => client,
            Err(error) => {
                let _ = child.kill().await;
                return Err(error);
            }
        };
        let mut closed = client.closed();
        let (stop, stopped) = oneshot::channel();
        let (dead_sender, dead) = watch::channel(false);
        let clean = reservation.clean.clone();
        reservation.started = true;
        tokio::spawn(async move {
            tokio::select! {
                _ = stopped => {},
                _ = closed.changed() => {},
                _ = child.wait() => {},
            }
            // EOF lets the worker drain its supervisors first. Unlike a Linux PID
            // namespace, child exit alone is not evidence of command-tree cleanup.
            if tokio::time::timeout(Duration::from_secs(4), child.wait())
                .await
                .is_err()
            {
                let _ = child.kill().await;
            }
            drop(state);
            drop(reservation);
            dead_sender.send_replace(true);
        });
        Ok(Self {
            client,
            clean,
            stop: Some(stop),
            dead,
        })
    }

    #[cfg(target_os = "linux")]
    pub fn status(&self) -> io::Result<crate::eip::EgressStatus> {
        self.policy
            .as_ref()
            .ok_or_else(|| io::Error::other("Session has no egress policy"))?
            .snapshot()
            .map(|snapshot| snapshot.status())
            .map_err(|_| io::Error::other("closed policy"))
    }
    #[cfg(target_os = "linux")]
    pub fn update(
        &self,
        params: crate::eip::EgressUpdateParams,
    ) -> Result<crate::eip::EgressStatus, crate::egress::policy::PolicyError> {
        self.policy
            .as_ref()
            .ok_or(crate::egress::policy::PolicyError::Closed)?
            .update(params.into())
            .map(|snapshot| snapshot.status())
    }
    #[cfg(target_os = "macos")]
    pub fn environment(&self) -> io::Result<std::collections::BTreeMap<String, String>> {
        Ok(Default::default())
    }

    #[cfg(target_os = "linux")]
    pub fn environment(&self) -> io::Result<std::collections::BTreeMap<String, String>> {
        let Some(policy) = &self.policy else {
            return Ok(Default::default());
        };
        let mut env = policy
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
        #[cfg(target_os = "linux")]
        if let Some(policy) = &self.policy {
            policy.close();
        }
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
        #[cfg(target_os = "linux")]
        if let Some(policy) = &self.policy {
            policy.close();
        }
        self.stop.take();
    }
}

fn sanitize_environment(command: &mut crate::config::CommandConfig) {
    let safe = |name: &String, _: &mut String| {
        matches!(
            name.as_str(),
            "PATH" | "LANG" | "LC_ALL" | "LC_CTYPE" | "TERM" | "COLORTERM"
        )
    };
    command.base_environment.retain(safe);
    for profile in &mut command.shell_profiles {
        profile.safe_base_environment.retain(safe);
    }
}

#[cfg(target_os = "linux")]
fn bootstrap(
    mut plan: Option<Mounts>,
    mut config: WorkerConfig,
) -> io::Result<(ChildGuard, UnixStream, Option<[OwnedFd; 3]>, OwnedFd)> {
    let identity = config
        .execution
        .ok_or_else(|| io::Error::other("missing execution identity"))?;
    if config.sandbox.restricted() {
        config.groups = super::restricted_groups(identity)?;
    }
    let state = if plan.is_none() || config.sandbox.restricted() {
        let identity = config
            .execution
            .ok_or_else(|| io::Error::other("missing execution identity"))?;
        let state = crate::execution::StateDirectory::new(Some(identity))?;
        config.runtime_directory = if config.sandbox.restricted() {
            super::sandbox::STATE.into()
        } else {
            state.0.clone()
        };
        if let Some(plan) = &mut plan {
            plan.state = Some(state.0.clone());
        }
        Some(state)
    } else {
        None
    };
    let (mut socket, input) = UnixStream::pair()?;
    socket.set_read_timeout(Some(Duration::from_secs(20)))?;
    socket.set_write_timeout(Some(Duration::from_secs(20)))?;
    let parent = unsafe { libc::getpid() };
    let mut command = if config.sandbox.restricted() && plan.is_none() {
        super::sandbox::command(
            &config.sandbox,
            &config.grant_sources,
            config.egress,
            state.as_ref().map(|state| state.0.as_path()),
            identity,
            false,
            None,
        )?
    } else if plan.is_some() {
        Command::new("/usr/bin/unshare")
    } else {
        Command::new(std::env::current_exe()?)
    };
    if plan.is_some() {
        if !config.sandbox.restricted() {
            command.args([
                "--mount",
                "--pid",
                "--ipc",
                "--fork",
                "--kill-child",
                "--mount-proc",
            ]);
        }
        if config.egress != (super::boundary::Egress::Inherit {}) {
            command.arg("--net");
        }
        command
            .arg(std::env::current_exe()?)
            .arg("--internal-egress-worker");
    } else {
        command.arg("--internal-session-worker");
    }
    command
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
    let child = ChildGuard {
        child: command.spawn()?,
        _state: state,
    };
    let (network, death) = if let Some(plan) = plan {
        write(&mut socket, &plan)?;
        write(&mut socket, &config)?;
        let result = if config.sandbox.restricted() {
            let network = if plan.network {
                Some(namespace::fds::receive(&socket)?)
            } else {
                None
            };
            write(&mut socket, &config)?;
            let [death] = namespace::fds::receive(&socket)?;
            (network, death)
        } else if plan.network {
            let [tcp, udp, dns, death] = namespace::fds::receive(&socket)?;
            (Some([tcp, udp, dns]), death)
        } else {
            let [death] = namespace::fds::receive(&socket)?;
            (None, death)
        };
        write(&mut socket, &config)?;
        result
    } else {
        write(&mut socket, &config)?;
        let [death] = namespace::fds::receive(&socket)?;
        (None, death)
    };
    socket.set_read_timeout(None)?;
    socket.set_write_timeout(None)?;
    Ok((child, socket, network, death))
}
#[cfg(target_os = "linux")]
fn write(socket: &mut UnixStream, value: &impl serde::Serialize) -> io::Result<()> {
    let bytes = serde_json::to_vec(value).map_err(io::Error::other)?;
    socket.write_all(&(bytes.len() as u32).to_be_bytes())?;
    socket.write_all(&bytes)
}
