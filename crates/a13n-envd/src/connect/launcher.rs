//! Local launcher notifications, separate from EIP and never containing device credentials.

#[cfg(unix)]
use std::io::Read;
use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};

static ENABLED: AtomicBool = AtomicBool::new(false);

pub(crate) fn enable() -> std::io::Result<()> {
    #[cfg(unix)]
    {
        ENABLED.store(true, Ordering::Relaxed);
        // The app owns stdin's write end. App exit or crash closes it, so its
        // daemon cannot silently outlive the process that granted local access.
        std::thread::Builder::new()
            .name("launcher-lifetime".into())
            .spawn(|| {
                let mut byte = [0];
                while let Ok(1) = std::io::stdin().read(&mut byte) {}
                unsafe { libc::kill(libc::getpid(), libc::SIGINT) };
            })?;
        Ok(())
    }
    #[cfg(not(unix))]
    Err(std::io::Error::other("launcher mode requires Unix"))
}

pub(crate) fn emit(event: &str, fields: serde_json::Value) {
    if ENABLED.load(Ordering::Relaxed) {
        let mut output = std::io::stdout().lock();
        let _ = writeln!(
            output,
            "{}",
            serde_json::json!({"event": event, "data": fields})
        );
        let _ = output.flush();
    }
}
