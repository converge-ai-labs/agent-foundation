//! A Session worker has no real secrets and owns the complete filesystem/execution engine.
use super::namespace::{self, Mounts, RUNTIME};
use crate::{
    config::{CommandConfig, Config, DaemonLimits, TransportConfig},
    runtime::RuntimeState,
};
use serde::{Deserialize, Serialize};
use std::{
    io::{self, Read},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::{net::UnixStream, process::CommandExt},
    },
    process::Command,
    time::Duration,
};

pub(crate) mod client;
mod server;
mod wire;

const MAX_BOOTSTRAP_BYTES: usize = 16 * 1024 * 1024;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct WorkerConfig {
    pub managed: bool,
    pub execution: Option<crate::execution::Identity>,
    pub allow_sudo: bool,
    pub runtime_directory: std::path::PathBuf,
    pub device_id: String,
    pub generation: u64,
    pub session_id: String,
    pub working_directory: String,
    pub limits: DaemonLimits,
    pub command: Option<CommandConfig>,
    pub idle_timeout_ms: u64,
    pub disconnect_grace_ms: u64,
}

impl WorkerConfig {
    fn config(&self) -> io::Result<Config> {
        Ok(Config {
            managed: false,
            execution: None,
            allow_sudo: self.allow_sudo,
            egress: Default::default(),
            bootstrap_files: Vec::new(),
            device_id: self.device_id.clone(),
            default_working_directory: self.working_directory.clone(),
            directory_discovery: false,
            display_name: None,
            description: None,
            transport: TransportConfig::Stdio,
            limits: self.limits.clone(),
            initialization_timeout: Duration::from_secs(30),
            session_idle_timeout: Duration::from_millis(self.idle_timeout_ms),
            disconnect_grace: Duration::from_millis(self.disconnect_grace_ms),
            command: self.command.clone(),
            runtime: Some(
                RuntimeState::prepare(&self.runtime_directory).map_err(io::Error::other)?,
            ),
        })
    }
}

/// Called by main before any runtime or thread exists. Stage one re-execs the
/// private immutable copy so every later supervisor inherits a stable executable.
pub(crate) fn run_internal(ready: bool) -> io::Result<()> {
    let mut socket = unsafe { UnixStream::from_raw_fd(libc::STDIN_FILENO) };
    if !ready {
        let mounts: Mounts = read_bootstrap(&mut socket)?;
        let mut bootstrap: WorkerConfig = read_bootstrap(&mut socket)?;
        let listeners = namespace::prepare(&mounts)?;
        let identity = bootstrap
            .execution
            .ok_or_else(|| io::Error::other("missing execution identity"))?;
        std::fs::create_dir(&bootstrap.runtime_directory)?;
        let directory = std::fs::File::open(&bootstrap.runtime_directory)?;
        if unsafe { libc::fchown(directory.as_raw_fd(), identity.uid, identity.gid) } != 0 {
            return Err(io::Error::last_os_error());
        }
        drop(directory);
        let death = unsafe { libc::syscall(libc::SYS_pidfd_open, libc::getpid(), 0) };
        if death < 0 {
            return Err(io::Error::last_os_error());
        }
        let death = unsafe { std::os::fd::OwnedFd::from_raw_fd(death as _) };
        if let Some(listeners) = &listeners {
            namespace::fds::send(
                &socket,
                &[
                    listeners.tcp.as_raw_fd(),
                    listeners.udp.as_raw_fd(),
                    listeners.dns.as_raw_fd(),
                    death.as_raw_fd(),
                ],
            )?;
        } else {
            namespace::fds::send(&socket, &[death.as_raw_fd()])?;
        }
        drop(listeners);
        drop(death);
        // The parent must own genuine kernel handles before native NSS can
        // load code from the intentionally mutable original system tree.
        enter(&mut bootstrap)?;
        return Err(Command::new(format!("{RUNTIME}/envd"))
            .arg("--internal-egress-ready")
            .env_clear()
            .exec());
    }
    // Executing resets dumpability; restore it before any payload can start.
    namespace::protect_process()?;
    let mut bootstrap: WorkerConfig = read_bootstrap(&mut socket)?;
    enter(&mut bootstrap)?;
    // Managed workers already switched from their required root launcher before
    // re-exec. The fresh bootstrap must retain that supervisor-only provenance.
    if bootstrap
        .execution
        .is_some_and(|identity| identity.uid != 0)
        && let Some(command) = &mut bootstrap.command
    {
        command.drop_supervisor_capabilities = true;
    }
    serve(socket, bootstrap)
}

/// Ordinary identity workers use the same filesystem and command engine, without
/// silently opting into a network policy or changing the native system tree.
pub(crate) fn run_session() -> io::Result<()> {
    let mut socket = unsafe { UnixStream::from_raw_fd(libc::STDIN_FILENO) };
    let mut bootstrap: WorkerConfig = read_bootstrap(&mut socket)?;
    enter(&mut bootstrap)?;
    let death = unsafe { libc::syscall(libc::SYS_pidfd_open, libc::getpid(), 0) };
    if death < 0 {
        return Err(io::Error::last_os_error());
    }
    let death = unsafe { std::os::fd::OwnedFd::from_raw_fd(death as _) };
    namespace::fds::send(&socket, &[death.as_raw_fd()])?;
    drop(death);
    serve(socket, bootstrap)
}

fn enter(bootstrap: &mut WorkerConfig) -> io::Result<()> {
    let parent = unsafe { libc::getppid() };
    let identity = bootstrap
        .execution
        .ok_or_else(|| io::Error::other("missing execution identity"))?;
    crate::execution::enter(identity, &mut bootstrap.command)?;
    if !bootstrap.allow_sudo {
        crate::execution::disable_privilege_gain()?;
    }
    // setuid clears PDEATHSIG. Restore it while the dedicated parent thread is
    // still alive, then verify that the broker did not exit during the change.
    if unsafe { libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) } != 0
        || unsafe { libc::getppid() } != parent
    {
        return Err(io::Error::other("Session parent exited"));
    }
    namespace::protect_process()?;
    let cwd =
        crate::device_path::resolve_directory(&bootstrap.working_directory).map_err(|_| {
            io::Error::new(
                io::ErrorKind::PermissionDenied,
                "working directory is unavailable",
            )
        })?;
    std::env::set_current_dir(&cwd)?;
    bootstrap.working_directory = crate::device_path::from_native(&cwd)
        .map_err(|_| io::Error::other("invalid working directory"))?;
    Ok(())
}

fn serve(socket: UnixStream, bootstrap: WorkerConfig) -> io::Result<()> {
    let config = bootstrap.config()?;
    socket.set_nonblocking(true)?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let result = runtime.block_on(async {
        let socket = tokio::net::UnixStream::from_std(socket)?;
        let (reader, writer) = socket.into_split();
        server::serve(
            reader,
            writer,
            config,
            bootstrap.generation,
            bootstrap.session_id,
        )
        .await
    });
    runtime.shutdown_timeout(Duration::from_secs(1));
    result
}

fn read_bootstrap<T: serde::de::DeserializeOwned>(reader: &mut impl Read) -> io::Result<T> {
    let mut length = [0; 4];
    reader.read_exact(&mut length)?;
    let length = u32::from_be_bytes(length) as usize;
    if length > MAX_BOOTSTRAP_BYTES {
        return Err(io::Error::other("worker bootstrap exceeds limit"));
    }
    let mut bytes = vec![0; length];
    reader.read_exact(&mut bytes)?;
    serde_json::from_slice(&bytes).map_err(|_| io::Error::other("invalid worker bootstrap"))
}

// Request-local broker metadata is applied after ledger admission, so retries retain
// the caller's original fingerprint even when secrets have been added or removed.
tokio::task_local! { static ENVIRONMENT: std::collections::BTreeMap<String, String>; }
pub(crate) fn request_environment(
    request: &crate::eip::CommandRequest,
) -> Result<std::borrow::Cow<'_, crate::eip::CommandRequest>, ()> {
    ENVIRONMENT
        .try_with(|environment| {
            if request
                .environment
                .set
                .keys()
                .chain(&request.environment.unset)
                .any(|name| environment.contains_key(name))
            {
                return Err(());
            }
            let mut request = request.clone();
            request.environment.set.extend(environment.clone());
            Ok(std::borrow::Cow::Owned(request))
        })
        .unwrap_or(Ok(std::borrow::Cow::Borrowed(request)))
}
