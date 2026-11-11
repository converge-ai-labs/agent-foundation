//! Operator shutdown and the native daemon supervisor's private lifetime pipe.

use std::io;

/// Register before launching a child so stop requests also win during backoff.
pub(crate) struct Signal {
    #[cfg(unix)]
    interrupt: tokio::signal::unix::Signal,
    #[cfg(unix)]
    terminate: tokio::signal::unix::Signal,
    #[cfg(unix)]
    hangup: tokio::signal::unix::Signal,
    #[cfg(windows)]
    interrupt: tokio::signal::windows::CtrlC,
    #[cfg(windows)]
    stop: tokio::signal::windows::CtrlBreak,
    #[cfg(windows)]
    close: tokio::signal::windows::CtrlClose,
}

impl Signal {
    pub(crate) fn new() -> io::Result<Self> {
        #[cfg(unix)]
        {
            use tokio::signal::unix::{SignalKind, signal};
            Ok(Self {
                interrupt: signal(SignalKind::interrupt())?,
                terminate: signal(SignalKind::terminate())?,
                hangup: signal(SignalKind::hangup())?,
            })
        }
        #[cfg(windows)]
        {
            use tokio::signal::windows::{ctrl_break, ctrl_c, ctrl_close};
            Ok(Self {
                interrupt: ctrl_c()?,
                stop: ctrl_break()?,
                close: ctrl_close()?,
            })
        }
    }

    pub(crate) async fn recv(&mut self) {
        #[cfg(unix)]
        tokio::select! {
            _ = self.interrupt.recv() => {},
            _ = self.terminate.recv() => {},
            _ = self.hangup.recv() => {},
        }
        #[cfg(windows)]
        tokio::select! {
            _ = self.interrupt.recv() => {},
            _ = self.stop.recv() => {},
            _ = self.close.recv() => {},
        }
    }
}

pub(crate) async fn wait(supervised: bool) -> io::Result<()> {
    let mut signal = Signal::new()?;
    let parent_closed = async {
        use tokio::io::AsyncReadExt;
        if supervised {
            // Only the private supervised launch uses stdin as a lifetime pipe.
            // Ordinary HTTP/WS daemons must remain independent of stdin EOF.
            let mut byte = [0];
            let _ = tokio::io::stdin().read(&mut byte).await;
        } else {
            std::future::pending::<()>().await;
        }
    };
    tokio::select! {
        _ = signal.recv() => {},
        _ = parent_closed => {},
    }
    Ok(())
}
