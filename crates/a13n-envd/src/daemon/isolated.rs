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
        staging: &[crate::filesystem::StagingDirectory],
    ) -> Result<(Arc<Self>, Arc<Carrier>), DaemonInitError> {
        let mut daemon = Self::with_generation(config, generation)?;
        Arc::get_mut(&mut daemon.filesystem)
            .expect("new worker filesystem")
            .set_staging_directories(staging)
            .map_err(|error| DaemonInitError::new(error.to_string()))?;
        daemon.ids = ShortIdAllocator::for_session(&session_id);
        let daemon = Arc::new(daemon);
        let mut carrier = daemon.carrier(outbound.clone());
        Arc::get_mut(&mut carrier)
            .expect("new private carrier")
            .authority = CarrierAuthority::Delegated;
        carrier.initialized.store(true, Ordering::Release);
        let session = Arc::new(Session::new(
            &daemon,
            config,
            carrier.id,
            session_id.clone(),
            config.default_working_directory.clone(),
            daemon.descriptor.lifecycle.clone(),
        )?);
        // Only the outer Session enforces lifetime. Policy-only activity need
        // not cross the worker pipe, and attach never resets the outer clock.
        {
            let mut state = session.session.state();
            state.idle_timeout = Duration::MAX;
            state.max_lifetime = None;
        }
        session
            .transfers
            .attach(outbound)
            .map_err(|_| DaemonInitError::new("worker transfer initialization failed"))?;
        daemon.sessions().insert(session_id, session);
        Ok((daemon, carrier))
    }

    pub(crate) fn worker_ready(
        &self,
        session_id: &str,
    ) -> Result<crate::execution::worker::WorkerReady, EIPError> {
        let session = self.lookup(session_id)?;
        let descriptor = &session.descriptor;
        Ok(crate::execution::worker::WorkerReady {
            working_directory: descriptor.working_directory.clone(),
            path_style: descriptor.path_style,
            system_info: descriptor.system_info.clone(),
            available_methods: descriptor.available_methods.clone(),
            shell_profiles: descriptor.shell_profiles.clone(),
            execution_features: descriptor.execution_features.clone(),
        })
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
        // The outer Session owns public attachment. This private carrier remains
        // authorized for already-admitted work while its transfer route is absent.
        Ok(())
    }
}
