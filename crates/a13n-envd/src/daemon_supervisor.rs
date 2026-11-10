//! Explicit foreground supervision for deployments without a service manager.

use std::{ffi::OsString, io, process::Stdio, time::Duration};
use tokio::{
    process::Command,
    time::{sleep, timeout},
};

const RESTART_DELAYS: [Duration; 3] = [
    Duration::from_secs(1),
    Duration::from_secs(2),
    Duration::from_secs(4),
];
const STOP_GRACE: Duration = Duration::from_secs(3);
const KILL_GRACE: Duration = Duration::from_secs(1);

pub(crate) const HELP: &str = "\
Run one foreground daemon:
  a13n-envd daemon [daemon options]

Run a foreground supervisor for HTTP or reverse WebSocket:
  a13n-envd supervise [daemon options]

Use daemon under an external service manager; let that manager own restarts.
Use supervise when no reliable service manager is available. It starts the same
binary as a separate daemon child, with three lifetime replacement attempts and
1/2/4-second backoff. Operator stop drains the child without restarting it.
Stdio remains caller-owned and cannot be supervised. Existing daemon launch
options and A13N_ENVD_* configuration apply to both commands.";

pub(crate) fn run(arguments: &[OsString]) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if !matches!(
        std::env::var("A13N_ENVD_TRANSPORT").as_deref(),
        Ok("http" | "reverse_websocket")
    ) {
        return Err("supervise requires A13N_ENVD_TRANSPORT=http or reverse_websocket; stdio is caller-owned".into());
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(supervise(arguments))?;
    Ok(())
}

async fn supervise(arguments: &[OsString]) -> io::Result<()> {
    let mut stop = crate::shutdown::Signal::new()?;
    let executable = std::env::current_exe()?;
    let mut replacements = 0;
    loop {
        let mut command = Command::new(&executable);
        command
            .arg("--internal-supervised-daemon")
            .args(arguments)
            .stdin(Stdio::piped())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);
        #[cfg(unix)]
        command.process_group(0);
        #[cfg(windows)]
        let (mut child, job) = crate::windows_job::WindowsJob::spawn(&mut command).await?;
        #[cfg(not(windows))]
        let mut child = command.spawn()?;
        // Child::wait closes child.stdin; retain the lifetime writer separately.
        let lifetime = child.stdin.take();
        let pid = child
            .id()
            .ok_or_else(|| io::Error::other("daemon PID unavailable"))?;
        eprintln!("a13n-envd supervise: started daemon pid={pid}");
        let stopped = tokio::select! {
            biased;
            _ = stop.recv() => true,
            result = child.wait() => {
                eprintln!("a13n-envd supervise: daemon exited: {}", result?);
                false
            }
        };
        if stopped {
            // Closing this private pipe requests graceful drain on every OS,
            // including Windows processes launched without an attached console.
            drop(lifetime);
            let _ = timeout(STOP_GRACE, child.wait()).await;
        }
        // A crashed leader may leave descendants. Retire the old tree before a
        // replacement acquires the runtime lock or accepts a fresh generation.
        #[cfg(windows)]
        job.terminate_and_wait(KILL_GRACE).await?;
        #[cfg(unix)]
        kill_group(pid)?;
        timeout(KILL_GRACE, child.wait())
            .await
            .map_err(|_| io::Error::other("daemon did not exit after termination"))??;
        if stopped {
            return Ok(());
        }
        let Some(delay) = RESTART_DELAYS.get(replacements) else {
            return Err(io::Error::other("daemon replacement budget exhausted"));
        };
        replacements += 1;
        eprintln!(
            "a13n-envd supervise: replacement {replacements}/3 in {}s",
            delay.as_secs()
        );
        tokio::select! {
            biased;
            _ = stop.recv() => return Ok(()),
            _ = sleep(*delay) => {},
        }
    }
}

#[cfg(unix)]
fn kill_group(pid: u32) -> io::Result<()> {
    if unsafe { libc::kill(-(pid as i32), libc::SIGKILL) } == 0 {
        return Ok(());
    }
    let error = io::Error::last_os_error();
    if error.raw_os_error() == Some(libc::ESRCH) {
        Ok(())
    } else {
        Err(error)
    }
}
