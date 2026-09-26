//! Local permission readiness precedes every EIP transport, not Session admission.
use std::{future::Future, io, time::Duration};

const GUIDANCE: &str = "Open System Settings > Privacy & Security and enable Screen Recording and Accessibility for the process or launcher macOS identifies for envd. Restart that launcher and envd if macOS requires it, then run the same command again. No EIP transport has started.";

#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub(super) struct Permissions {
    pub(super) capture: bool,
    pub(super) input: bool,
}

impl Permissions {
    fn ready(self) -> bool {
        self.capture && self.input
    }

    fn missing(self) -> &'static str {
        match (self.capture, self.input) {
            (false, false) => "Screen Recording and Accessibility",
            (false, true) => "Screen Recording",
            (true, false) => "Accessibility",
            (true, true) => "none",
        }
    }
}

pub(crate) async fn prepare(timeout: Duration) -> io::Result<()> {
    #[cfg(target_os = "macos")]
    {
        tokio::select! {
            result = wait(
                timeout,
                || async {
                    tokio::task::spawn_blocking(super::macos::permissions)
                        .await.map_err(io::Error::other)
                },
                super::macos::request_permissions,
            ) => result,
            result = tokio::signal::ctrl_c() => {
                result?;
                Err(io::Error::new(io::ErrorKind::Interrupted,
                    format!("computer-use authorization cancelled. {GUIDANCE}")))
            }
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = timeout;
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "computer use requires macOS",
        ))
    }
}

async fn wait<P, F>(
    timeout: Duration,
    mut probe: P,
    request: impl FnOnce(Permissions),
) -> io::Result<()>
where
    P: FnMut() -> F,
    F: Future<Output = io::Result<Permissions>>,
{
    let mut last = Permissions::default();
    let result = tokio::time::timeout(timeout, async {
        last = probe().await?;
        if last.ready() {
            return Ok::<(), io::Error>(());
        }
        eprintln!("Computer use is enabled; waiting up to {} ms for {}. {GUIDANCE} Press Ctrl+C to cancel.", timeout.as_millis(), last.missing());
        request(last);
        loop {
            tokio::time::sleep(Duration::from_millis(250)).await;
            let current = probe().await?;
            if current != last {
                last = current;
                eprintln!("Computer-use permissions still missing: {}.", last.missing());
            }
            if last.ready() {
                return Ok::<(), io::Error>(());
            }
        }
    }).await;
    match result {
        Ok(Ok(())) => {
            eprintln!("Computer-use permissions ready; starting EIP transport.");
            Ok(())
        }
        Ok(Err(error)) => Err(io::Error::other(format!(
            "computer-use permission check failed: {error}. {GUIDANCE}"
        ))),
        Err(_) => Err(io::Error::new(
            io::ErrorKind::TimedOut,
            format!(
                "computer-use authorization timed out after {} ms; missing or unconfirmed: {}. {GUIDANCE} Increase computer_use_permission_timeout_ms or A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS for a longer wait.",
                timeout.as_millis(),
                last.missing(),
            ),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    #[tokio::test]
    async fn authorized_startup_does_not_request_permissions() {
        wait(
            Duration::from_secs(1),
            || async {
                Ok(Permissions {
                    capture: true,
                    input: true,
                })
            },
            |_| panic!("already authorized"),
        )
        .await
        .unwrap();
    }

    #[tokio::test]
    async fn both_permissions_are_required_and_requested_only_once() {
        let calls = Cell::new(0);
        let requested = Cell::new(0);
        wait(
            Duration::from_secs(2),
            || {
                calls.set(calls.get() + 1);
                let input = calls.get() >= 3;
                async move {
                    Ok(Permissions {
                        capture: true,
                        input,
                    })
                }
            },
            |missing| {
                assert!(missing.capture);
                assert!(!missing.input);
                requested.set(requested.get() + 1);
            },
        )
        .await
        .unwrap();
        assert_eq!(calls.get(), 3);
        assert_eq!(requested.get(), 1);
    }

    #[tokio::test]
    async fn denied_permission_times_out_with_actionable_guidance() {
        let error = wait(
            Duration::from_millis(10),
            || async {
                Ok(Permissions {
                    capture: true,
                    input: false,
                })
            },
            |_| {},
        )
        .await
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert!(error.to_string().contains("Accessibility"));
        assert!(error.to_string().contains("No EIP transport has started"));
    }

    #[tokio::test]
    async fn failed_probe_is_reported_without_requesting_permissions() {
        let error = wait(
            Duration::from_secs(1),
            || async { Err(io::Error::other("probe failed")) },
            |_| panic!("probe failed"),
        )
        .await
        .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("permission check failed: probe failed")
        );
    }

    #[tokio::test]
    async fn stalled_probe_is_bounded_by_the_same_deadline() {
        let error = wait(
            Duration::from_millis(10),
            std::future::pending::<io::Result<Permissions>>,
            |_| panic!("probe stalled"),
        )
        .await
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
    }
}
