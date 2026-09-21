//! Construct the existing Session engine inside its isolated worker process.
#![allow(
    clippy::result_large_err,
    reason = "generated EIP errors are the public protocol contract"
)]
use super::*;

impl Daemon {
    pub(crate) fn isolated_session(
        config: &Config,
        generation: u64,
        session_id: String,
        outbound: mpsc::Sender<eip::DataFrame>,
    ) -> Result<(Arc<Self>, Arc<Carrier>), DaemonInitError> {
        let mut daemon = Self::with_generation(config, generation)?;
        daemon.ids = ShortIdAllocator::for_session(&session_id);
        // The outer Session owns idle/grace expiry. Policy-only calls never cross
        // this pipe, so a second independent timer would expire a live Session.
        daemon.config.session_idle_timeout = Duration::MAX;
        daemon.config.disconnect_grace = Duration::MAX;
        let daemon = Arc::new(daemon);
        let carrier = daemon.carrier(outbound.clone());
        carrier.initialized.store(true, Ordering::Release);
        let session = Arc::new(Session::new(
            &daemon,
            carrier.id,
            session_id.clone(),
            config.default_working_directory.clone(),
        )?);
        session
            .transfers
            .attach(outbound)
            .map_err(|_| DaemonInitError::new("worker transfer initialization failed"))?;
        daemon.sessions().insert(session_id, session);
        Ok((daemon, carrier))
    }

    pub(crate) async fn close_isolated_session(&self, session_id: &str) -> bool {
        match self.lookup(session_id) {
            Ok(session) => session.close(Duration::from_secs(3)).await,
            Err(_) => true,
        }
    }

    /// The permanent private carrier stays open while its public route detaches.
    pub(crate) async fn detach_isolated_session(&self, session_id: &str) -> Result<(), EIPError> {
        let session = self.lookup(session_id)?;
        let _cleanup = session.cleanup.lock().await;
        session.transfers.detach().await;
        let mut state = session.session.state();
        state.owner = None;
        state.detached_at = Some(Instant::now());
        Ok(())
    }
}
