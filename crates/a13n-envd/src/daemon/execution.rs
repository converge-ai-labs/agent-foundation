//! Parent-side lifecycle and policy ownership; filesystem and execution stay in the worker.
#![allow(
    clippy::result_large_err,
    reason = "generated EIP errors are the public protocol contract"
)]
use super::*;
use crate::execution::{runtime::Runtime, worker::WorkerConfig};

pub(crate) struct Reservation {
    _operations: tokio::sync::OwnedSemaphorePermit,
    _processes: crate::capacity::ResourcePermit,
    _transfers: crate::capacity::ResourcePermit,
    _spool: crate::retention::SessionSpoolReservation,
    staging: crate::filesystem::SessionStagingReservation,
    pub clean: Arc<AtomicBool>,
    pub started: bool,
}
impl Drop for Reservation {
    fn drop(&mut self) {
        if self.started && !self.clean.load(Ordering::Acquire) {
            self.staging.retain();
            #[cfg(target_os = "macos")]
            if let Some(active) = self._processes.active.take() {
                // Seatbelt has no PID namespace death fence. Keep the charge
                // until Device restart when command cleanup is unconfirmed.
                active.forget();
            }
        }
    }
}
impl Daemon {
    fn reserve_worker(&self) -> Option<Reservation> {
        let limits = &self.config.limits;
        Some(Reservation {
            _operations: self
                .capacity
                .operations
                .clone()
                .try_acquire_many_owned(u32::try_from(limits.max_operation_records).ok()?)
                .ok()?,
            _processes: self
                .capacity
                .processes
                .reserve_session(limits.max_processes, limits.max_process_records)?,
            _transfers: self.capacity.transfers.reserve_session(
                limits.max_concurrent_file_transfers,
                limits.max_file_transfer_records,
            )?,
            _spool: self.retention_quota.reserve_session(
                limits.max_spool_bytes,
                usize::try_from(limits.max_spool_objects).ok()?,
            )?,
            staging: self
                .filesystem
                .reserve_session_staging(
                    limits.max_staged_file_bytes,
                    limits.max_staged_file_objects,
                )
                .ok()?,
            clean: Arc::new(AtomicBool::new(false)),
            started: false,
        })
    }

    pub(super) async fn open_worker(
        &self,
        carrier: &Carrier,
        cwd: String,
        policy: Option<eip::EgressPolicy>,
    ) -> Result<eip::SessionOpenResult, EIPError> {
        if policy.is_some() != self.config.egress.controlled() {
            return Err(protocol_error(
                ErrorType::InvalidParams,
                "Session egress policy is required exactly for controlled Devices",
            ));
        }
        #[cfg(target_os = "linux")]
        let policy = policy
            .map(crate::egress::policy::Policy::from_request)
            .transpose()
            .map_err(policy_error)?;
        #[cfg(target_os = "linux")]
        let controlled = policy.is_some();
        let reservation = self.reserve_worker().ok_or_else(|| {
            protocol_error(ErrorType::Busy, "Session worker capacity is exhausted")
        })?;
        let id = self.ids.next("session").map_err(map_ledger_error)?;
        let mut session =
            Session::new(self, carrier.id, id.clone(), cwd.clone()).map_err(|_| {
                protocol_error(ErrorType::InternalError, "session initialization failed")
            })?;
        #[cfg(target_os = "linux")]
        let mut hide = self.config.bootstrap_files.clone();
        #[cfg(target_os = "linux")]
        if !self.config.managed {
            match &self.config.transport {
                crate::config::TransportConfig::Http(http) => {
                    hide.push(http.credential_file.clone());
                    hide.extend(http.tls_private_key_file.iter().cloned());
                }
                crate::config::TransportConfig::ReverseWebSocket(ws) => {
                    hide.push(ws.credential_file.clone())
                }
                crate::config::TransportConfig::Stdio => {}
            }

            if let Some(runtime) = &self.config.runtime {
                hide.push(runtime.generation().to_path_buf());
            }
            if let Some(home) = std::env::var_os("HOME") {
                hide.push(std::path::PathBuf::from(home).join(".a13n"));
            }
        }
        let worker = WorkerConfig {
            managed: self.config.managed,
            sandbox: self.config.sandbox.clone(),
            grant_sources: self.config.grant_sources.clone(),
            egress: self.config.egress,
            execution: self.config.execution,
            groups: None,
            allow_sudo: self.config.allow_sudo,
            runtime_directory: "/run/a13n-session-state".into(),
            device_id: self.config.device_id.clone(),
            generation: self.descriptor.generation,
            session_id: id.clone(),
            working_directory: cwd,
            limits: self.config.limits.clone(),
            command: self.config.command.clone(),
            idle_timeout_ms: self.config.session_idle_timeout.as_millis() as u64,
            disconnect_grace_ms: self.config.disconnect_grace.as_millis() as u64,
        };
        #[cfg(target_os = "linux")]
        let runtime = Runtime::start(worker, policy, hide, reservation).await;
        #[cfg(target_os = "macos")]
        let runtime = Runtime::start(worker, reservation).await;
        let runtime = runtime.map_err(|_| {
            protocol_error(
                ErrorType::Unsupported,
                "Session worker initialization failed",
            )
        })?;
        session.descriptor.working_directory = runtime.client.working_directory.clone();
        runtime.client.attach(carrier.outbound.clone());
        #[cfg(target_os = "linux")]
        if controlled {
            session.descriptor.egress =
                Some(runtime.status().map_err(|_| {
                    protocol_error(ErrorType::InternalError, "egress broker closed")
                })?);
            session
                .descriptor
                .available_methods
                .push("egress.update".to_owned());
        }
        session.remote = Some(runtime);
        let descriptor = session.descriptor.clone();
        let session = Arc::new(session);
        let inserted = {
            let mut sessions = self.sessions();
            if carrier.closed.load(Ordering::Acquire)
                || self.draining.load(Ordering::Acquire)
                || sessions.len() >= self.config.limits.max_sessions
            {
                false
            } else {
                sessions.insert(id, session.clone());
                true
            }
        };
        if !inserted {
            session.close(Duration::from_secs(10)).await;
            return Err(protocol_error(
                ErrorType::Busy,
                "Session admission changed while opening",
            ));
        }
        Ok(eip::SessionOpenResult { descriptor })
    }
}

#[cfg(target_os = "linux")]
pub(super) fn policy_error(error: crate::egress::policy::PolicyError) -> EIPError {
    use crate::egress::policy::PolicyError;
    match error {
        PolicyError::Conflict => {
            protocol_error(ErrorType::Conflict, "egress revision does not match")
        }
        PolicyError::Closed => protocol_error(ErrorType::NotInitialized, "egress broker is closed"),
        _ => protocol_error(ErrorType::InvalidParams, "invalid egress policy"),
    }
}

impl Session {
    pub(super) async fn relay(
        &self,
        carrier: &Carrier,
        request: &JsonRpcRequest,
    ) -> Result<CarrierResponse, EIPError> {
        let remote = self.remote.as_ref().expect("Session worker");
        if request.method == "session.attach" {
            remote.client.attach(carrier.outbound.clone());
        }
        let environment = if matches!(request.method.as_str(), "process.start" | "shell.exec") {
            remote
                .environment()
                .map_err(|_| protocol_error(ErrorType::NotInitialized, "egress broker is closed"))?
        } else {
            Default::default()
        };
        let payload = serde_json::to_value(request).map_err(|_| {
            protocol_error(ErrorType::InternalError, "request serialization failed")
        })?;
        let response = remote
            .client
            .request(payload, environment)
            .await
            .map_err(|_| {
                self.session.close();
                self.closed.send_replace(true);
                protocol_error(ErrorType::NotInitialized, "isolated worker disconnected")
            })?;
        if !matches!(
            request.method.as_str(),
            "environment.readiness" | "environment.describe" | "session.attach"
        ) {
            return Ok(CarrierResponse {
                payload: response.payload,
                handoff: Some(ResponseHandoff::Remote(response.handoff)),
            });
        }
        let mut payload: serde_json::Value = serde_json::from_slice(&response.payload)
            .map_err(|_| protocol_error(ErrorType::InternalError, "invalid worker response"))?;
        if request.method == "environment.readiness"
            && let Some(ready) = payload["result"]["ready"].as_bool()
        {
            let mut state = self.session.state();
            if state.lifecycle != SessionState::Closed {
                state.lifecycle = if ready {
                    SessionState::Ready
                } else {
                    SessionState::NotReady
                };
            }
        }
        if matches!(
            request.method.as_str(),
            "session.attach" | "environment.describe"
        ) && payload.get("result").is_some()
        {
            payload["result"]["descriptor"] = serde_json::to_value(self.current_descriptor()?)
                .map_err(|_| {
                    protocol_error(ErrorType::InternalError, "descriptor serialization failed")
                })?;
        }
        let payload = serde_json::to_vec(&payload).map_err(|_| {
            protocol_error(ErrorType::InternalError, "response serialization failed")
        })?;
        Ok(CarrierResponse {
            payload,
            handoff: Some(ResponseHandoff::Remote(response.handoff)),
        })
    }
}
