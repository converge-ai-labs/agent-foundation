//! Device-level directory observations use the same native identity as Sessions,
//! without changing the multithreaded broker's credentials or opening a Session.
use super::Identity;
use crate::{
    config::Config,
    eip::{DirectoryListParams, DirectoryListResult, EIPError},
};
use serde::{Deserialize, Serialize};
use std::{io, process::Stdio, time::Duration};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[derive(Deserialize, Serialize)]
struct Request {
    identity: Identity,
    managed: bool,
    path: String,
    offset: u64,
    limit: u64,
    max_bytes: u64,
}

type Reply = Result<DirectoryListResult, EIPError>;

pub(crate) async fn list(config: &Config, params: DirectoryListParams) -> io::Result<Reply> {
    let request = Request {
        identity: config
            .execution
            .ok_or_else(|| io::Error::other("missing execution identity"))?,
        managed: config.managed,
        path: params.path,
        offset: params.offset,
        limit: params.limit,
        max_bytes: config.limits.max_response_bytes,
    };
    let mut command = if config.managed {
        let mut command = tokio::process::Command::new("/usr/bin/unshare");
        command.args([
            "--mount",
            "--pid",
            "--ipc",
            "--net",
            "--fork",
            "--kill-child",
            "--mount-proc",
        ]);
        command.arg(std::env::current_exe()?);
        command
    } else {
        tokio::process::Command::new(std::env::current_exe()?)
    };
    let mut child = command
        .arg("--internal-directory-worker")
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()?;
    let operation = async {
        let mut input = child
            .stdin
            .take()
            .ok_or_else(|| io::Error::other("missing discovery input"))?;
        input
            .write_all(&serde_json::to_vec(&request).map_err(io::Error::other)?)
            .await?;
        drop(input);
        let output = child
            .stdout
            .take()
            .ok_or_else(|| io::Error::other("missing discovery output"))?;
        let maximum = request.max_bytes.saturating_add(1024);
        let mut bytes = Vec::new();
        output.take(maximum + 1).read_to_end(&mut bytes).await?;
        if bytes.len() as u64 > maximum {
            return Err(io::Error::other("directory response exceeds limit"));
        }
        if !child.wait().await?.success() {
            return Err(io::Error::other("directory worker failed"));
        }
        serde_json::from_slice(&bytes).map_err(io::Error::other)
    };
    tokio::time::timeout(Duration::from_secs(10), operation)
        .await
        .map_err(io::Error::other)?
}

pub(crate) fn run() -> io::Result<()> {
    use std::io::Read;
    let request: Request =
        serde_json::from_reader(io::stdin().take(16 * 1024 * 1024)).map_err(io::Error::other)?;
    if request.managed {
        crate::egress::namespace::prepare_discovery()?;
    }
    super::enter(request.identity, &mut None)?;
    if request.managed && unsafe { libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) } != 0 {
        return Err(io::Error::last_os_error());
    }
    // No command execution in this helper; do not retain the worker's CAP_KILL.
    super::clear_payload_capabilities()?;
    super::disable_privilege_gain()?;
    let reply = crate::device_path::list_directory(
        &request.path,
        request.offset,
        request.limit,
        request.max_bytes,
    )
    .map_err(|error| crate::daemon::map_resource_error(crate::resource::map_path_error(error)));
    serde_json::to_writer(io::stdout(), &reply).map_err(io::Error::other)
}
