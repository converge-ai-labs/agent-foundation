//! Explicit foreground supervision for deployments without a service manager.

use std::{ffi::OsString, io, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    process::{ChildStdin, ChildStdout, Command},
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
Use a13n-envd restart --help for explicit restart after a Host-installed upgrade.
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
    #[cfg(target_os = "linux")]
    // Adopt and reap command cleanup owners when the daemon exits abruptly.
    // They must survive long enough to retire their separate payload groups.
    if unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let mut stop = crate::shutdown::Signal::new()?;
    let executable = std::env::current_exe()?;
    let control = crate::daemon_control::Control::bind().await?;
    let mut replacements = 0;
    let mut acknowledgement: Option<(tokio::net::TcpStream, Option<String>)> = None;
    loop {
        let mut command = Command::new(&executable);
        command
            .arg("--internal-supervised-daemon")
            .args(arguments)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);
        #[cfg(unix)]
        command.process_group(0);
        #[cfg(windows)]
        let (mut child, job) = crate::windows_job::WindowsJob::spawn(&mut command).await?;
        #[cfg(not(windows))]
        let mut child = command.spawn()?;
        // Child::wait closes child.stdin; retain the lifetime writer separately.
        let mut lifetime = child.stdin.take().expect("piped daemon stdin");
        let mut output = child.stdout.take().expect("piped daemon stdout");
        let pid = child
            .id()
            .ok_or_else(|| io::Error::other("daemon PID unavailable"))?;
        eprintln!("a13n-envd supervise: started daemon pid={pid}");
        let expected = acknowledgement
            .as_ref()
            .and_then(|(_, version)| version.as_deref());
        let startup = tokio::select! {
            biased;
            _ = stop.recv() => None,
            result = authorize_launch(&mut output, &mut lifetime, expected) => Some(result),
        };
        // The first line is private startup identity; subsequent stdout retains
        // the foreground launcher's destination.
        let forwarding = tokio::spawn(async move {
            let _ = tokio::io::copy(&mut output, &mut tokio::io::stdout()).await;
        });
        let action = if let Some(Ok(version)) = startup.as_ref() {
            if let Some((mut stream, _)) = acknowledgement.take() {
                // EIP readiness is checked independently by the caller.
                let _ = crate::daemon_control::reply(&mut stream, None).await;
            }
            loop {
                let (mut stream, request) = tokio::select! {
                    biased;
                    _ = stop.recv() => break Action::Stop,
                    result = child.wait() => {
                        eprintln!("a13n-envd supervise: daemon exited: {}", result?);
                        break Action::Crash;
                    }
                    result = control.receive() => result?,
                };
                if request.version.as_deref() == Some(version.as_str()) {
                    let _ = crate::daemon_control::reply(&mut stream, None).await;
                    continue;
                }
                let installed = tokio::select! {
                    biased;
                    _ = stop.recv() => break Action::Stop,
                    result = crate::daemon_control::binary_version(&executable) => result,
                };
                if installed.as_ref().is_ok_and(|installed| {
                    request
                        .version
                        .as_ref()
                        .is_none_or(|expected| expected == installed)
                }) {
                    acknowledgement = Some((stream, request.version));
                    eprintln!("a13n-envd supervise: explicit restart accepted");
                    break Action::Restart;
                }
                let _ = crate::daemon_control::reply(
                    &mut stream,
                    Some("installed executable does not match requested version"),
                )
                .await;
            }
        } else if let Some(Err(error)) = startup {
            eprintln!("a13n-envd supervise: daemon launch refused: {error}");
            if let Some((mut stream, _)) = acknowledgement.take() {
                let _ = crate::daemon_control::reply(&mut stream, Some(&error.to_string())).await;
                Action::Refuse(error)
            } else {
                Action::Crash
            }
        } else {
            Action::Stop
        };
        let mut stopping = matches!(action, Action::Stop);
        let crashed = matches!(action, Action::Crash);
        let cleanup = async {
            // Closing this private pipe requests graceful drain on every OS,
            // including Windows processes launched without an attached console.
            drop(lifetime);
            if !crashed {
                let _ = timeout(STOP_GRACE, child.wait()).await;
            }
            #[cfg(windows)]
            job.terminate_and_wait(KILL_GRACE).await?;
            #[cfg(unix)]
            // Kill only the daemon. Its command supervisors need control-pipe EOF
            // to terminate and reap payloads in their own process groups.
            if child.try_wait()?.is_none() {
                child.start_kill()?;
            }
            timeout(KILL_GRACE, child.wait())
                .await
                .map_err(|_| io::Error::other("daemon did not exit after termination"))??;
            #[cfg(unix)]
            {
                eprintln!("a13n-envd supervise: waiting for daemon descendants");
                wait_group(pid).await?;
            }
            Ok::<_, io::Error>(())
        };
        tokio::pin!(cleanup);
        tokio::select! {
            biased;
            _ = stop.recv(), if !stopping => {
                stopping = true;
                cleanup.await?;
            },
            result = &mut cleanup => result?,
        }
        forwarding.abort();
        if stopping {
            return Ok(());
        }
        match action {
            Action::Stop => return Ok(()),
            Action::Restart => continue,
            Action::Crash => {}
            Action::Refuse(error) => return Err(error),
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

enum Action {
    Stop,
    Crash,
    Restart,
    Refuse(io::Error),
}

async fn authorize_launch(
    output: &mut ChildStdout,
    lifetime: &mut ChildStdin,
    expected: Option<&str>,
) -> io::Result<String> {
    timeout(Duration::from_secs(5), async {
        let mut bytes = Vec::new();
        loop {
            let byte = output.read_u8().await?;
            if byte == b'\n' {
                break;
            }
            if bytes.len() == 1024 {
                return Err(io::Error::other("daemon startup identity exceeds limit"));
            }
            bytes.push(byte);
        }
        let version = std::str::from_utf8(&bytes).map_err(io::Error::other)?;
        semver::Version::parse(version).map_err(io::Error::other)?;
        if expected.is_some_and(|expected| expected != version) {
            return Err(io::Error::other(
                "launched daemon does not match requested version",
            ));
        }
        // The actual child reports its compiled version before configuration or
        // runtime activation. A path probe alone cannot fence atomic replacement.
        lifetime.write_all(b"\n").await?;
        Ok(version.to_owned())
    })
    .await
    .map_err(|_| io::Error::other("daemon startup identity timed out"))?
}

pub(crate) fn authorize_child() -> io::Result<()> {
    use std::io::{Read, Write};
    let mut output = io::stdout().lock();
    writeln!(output, "{}", env!("CARGO_PKG_VERSION"))?;
    output.flush()?;
    drop(output);
    let mut permission = [0];
    io::stdin().read_exact(&mut permission)?;
    if permission != *b"\n" {
        return Err(io::Error::other("invalid supervisor launch permission"));
    }
    Ok(())
}

#[cfg(unix)]
async fn wait_group(pid: u32) -> io::Result<()> {
    let group = -(pid as i32);
    timeout(STOP_GRACE, async {
        loop {
            #[cfg(target_os = "linux")]
            // The daemon has been awaited. Only its adopted descendants remain.
            while unsafe { libc::waitpid(group, std::ptr::null_mut(), libc::WNOHANG) } > 0 {}
            if unsafe { libc::kill(group, 0) } != 0 {
                let error = io::Error::last_os_error();
                return if error.raw_os_error() == Some(libc::ESRCH) {
                    Ok(())
                } else {
                    Err(error)
                };
            }
            sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .map_err(|_| {
        io::Error::other("daemon descendants did not finish cleanup; replacement refused")
    })?
}
