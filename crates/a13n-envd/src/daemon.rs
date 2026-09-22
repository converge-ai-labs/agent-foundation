use std::{
    collections::BTreeMap,
    error::Error,
    fmt,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::{
        Arc, Mutex, MutexGuard, PoisonError,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

use serde::{Serialize, de::DeserializeOwned};
use tokio::sync::{
    Notify, OwnedRwLockReadGuard, OwnedSemaphorePermit, RwLock, Semaphore, mpsc, watch,
};

use crate::{
    capacity::DeviceCapacity,
    config::Config,
    device_path,
    eip::{
        self, DeviceDescriptor, DispatchError, DispatchStage, EIPError, EIPErrorData,
        EIPServerInfo, EipDeviceHandler, EipSessionHandler, EnvironmentDescribeParams,
        EnvironmentDescribeResult, EnvironmentReadinessParams, EnvironmentReadinessResult,
        ErrorType, ExecutionFeatures, InitializeParams, InitializeResult, JsonRpcErrorResponse,
        JsonRpcId, JsonRpcRequest, JsonRpcSuccessResponse, ReceiptOutcome, ReceiptStage, RetryHint,
        SessionCloseParams, SessionCloseResult, SessionDescriptor,
    },
    filesystem::DeviceFilesystem,
    operation::{
        ActiveResponseHandoff, BeginOutcome, LedgerError, OperationInterruption, OperationLease,
        OperationLedger, OwnedOperationResult, PendingAdmissionWait, PendingOperationGuard,
        ShortIdAllocator, scope_carrier_attempt,
    },
    process::{ExecutionManager, ProcessError, StartFailure},
    resource::{ResourceError, ResourceRegistry},
    retention::{RetentionError, RetentionQuota, RetentionStore},
    transfer::{TransferError, TransferRegistry},
};

const MAX_STRING_REQUEST_ID_BYTES: usize = 128;
#[cfg(target_os = "linux")]
pub(crate) mod egress;
#[cfg(target_os = "linux")]
mod isolated;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SessionState {
    Initialized,
    Ready,
    NotReady,
    Closed,
}

struct SessionAdmission {
    state: Mutex<SessionAdmissionState>,
    idle: Notify,
}

struct SessionAdmissionState {
    owner: Option<u64>,
    last_activity: Instant,
    detached_at: Option<Instant>,
    lifecycle: SessionState,
    active_session_work: usize,
}

struct SessionWorkGuard<'a> {
    admission: &'a SessionAdmission,
}

pub(crate) struct CarrierResponse {
    payload: Vec<u8>,
    handoff: Option<ResponseHandoff>,
}

pub(crate) enum ResponseHandoff {
    Local {
        operation: Option<ActiveResponseHandoff>,
        _history: OwnedRwLockReadGuard<()>,
    },
    #[cfg(target_os = "linux")]
    Remote(crate::egress::worker::client::Handoff),
}

impl ResponseHandoff {
    pub(crate) fn complete(self) {
        match self {
            Self::Local { operation, .. } => {
                if let Some(operation) = operation {
                    operation.complete();
                }
            }
            #[cfg(target_os = "linux")]
            Self::Remote(handoff) => handoff.complete(),
        }
    }
}

impl CarrierResponse {
    fn plain(payload: Vec<u8>) -> Self {
        Self {
            payload,
            handoff: None,
        }
    }

    pub(crate) fn into_parts(self) -> (Vec<u8>, Option<ResponseHandoff>) {
        (self.payload, self.handoff)
    }
}

/// The Device owns aggregate capacity and a registry, never a current Session.
pub(crate) struct Daemon {
    config: Config,
    descriptor: DeviceDescriptor,
    filesystem: Arc<DeviceFilesystem>,
    retention_quota: RetentionQuota,
    capacity: DeviceCapacity,
    request_capacity: Arc<Semaphore>,
    sessions: Mutex<BTreeMap<String, Arc<Session>>>,
    ids: ShortIdAllocator,
    next_carrier: AtomicU64,
    next_attempt: AtomicU64,
    draining: AtomicBool,
    last_diagnostics: Mutex<Option<Instant>>,
}

pub(crate) struct Carrier {
    id: u64,
    stateless: bool,
    initialized: AtomicBool,
    closed: AtomicBool,
    outbound: mpsc::Sender<eip::DataFrame>,
}

impl Carrier {
    pub(crate) fn initialized(&self) -> bool {
        self.initialized.load(Ordering::Acquire)
    }
    pub(crate) fn close(&self) {
        self.closed.store(true, Ordering::Release);
    }
}

struct DeviceRequest<'a> {
    daemon: &'a Daemon,
    carrier: &'a Carrier,
}

fn session_methods(config: &Config) -> Vec<String> {
    eip::METHODS
        .iter()
        .filter(|method| !method.device_scoped && method.name != "egress.update")
        .filter(|method| method.name != "file.commit" || cfg!(unix))
        .filter(|method| {
            let execution = method.name.starts_with("process.")
                || method.name.starts_with("output.")
                || method.name == "shell.exec";
            (!execution || config.command.is_some())
                && (method.name != "process.signal" || cfg!(unix))
        })
        .map(|method| method.name.to_owned())
        .collect()
}

impl Daemon {
    pub(crate) fn new(config: &Config) -> Result<Self, DaemonInitError> {
        Self::with_generation(config, fresh_generation()?)
    }

    fn with_generation(config: &Config, generation: u64) -> Result<Self, DaemonInitError> {
        if generation == 0 {
            return Err(DaemonInitError::new("generation must be nonzero"));
        }
        let filesystem = DeviceFilesystem::new(config)
            .map_err(|error| DaemonInitError::new(error.to_string()))?;
        let retention_quota = RetentionQuota::new(config)
            .map_err(|_| DaemonInitError::new("invalid retention limits"))?;
        let mut available_methods = session_methods(config);
        available_methods.extend(
            eip::METHODS
                .iter()
                .filter(|method| method.device_scoped)
                .filter(|method| method.name != "directory.list" || config.directory_discovery)
                .map(|method| method.name.to_owned()),
        );
        if config.egress.enabled && cfg!(target_os = "linux") {
            available_methods.push("egress.update".to_owned());
        }
        available_methods.sort();
        Ok(Self {
            config: config.clone(),
            filesystem,
            retention_quota,
            capacity: DeviceCapacity::new(&config.limits),
            request_capacity: Arc::new(Semaphore::new(
                config.limits.max_device_concurrent_operations as usize,
            )),
            descriptor: DeviceDescriptor {
                device_id: config.device_id.clone(),
                generation,
                display_name: config.display_name.clone(),
                description: config.description.clone(),
                path_style: device_path::path_style(),
                default_working_directory: config.default_working_directory.clone(),
                directory_discovery: config.directory_discovery,
                available_methods,
                limits: config.limits.descriptor(),
                lifecycle: eip::SessionLifecyclePolicy {
                    idle_timeout_ms: config.session_idle_timeout.as_millis() as u64,
                    disconnect_grace_ms: config.disconnect_grace.as_millis() as u64,
                },
            },
            sessions: Mutex::new(BTreeMap::new()),
            ids: ShortIdAllocator::for_generation(generation),
            next_carrier: AtomicU64::new(1),
            next_attempt: AtomicU64::new(1),
            draining: AtomicBool::new(false),
            last_diagnostics: Mutex::new(None),
        })
    }

    pub(crate) fn carrier(&self, outbound: mpsc::Sender<eip::DataFrame>) -> Arc<Carrier> {
        Arc::new(Carrier {
            id: self.next_carrier.fetch_add(1, Ordering::Relaxed),
            stateless: false,
            initialized: AtomicBool::new(false),
            closed: AtomicBool::new(false),
            outbound,
        })
    }

    pub(crate) fn http_carrier(&self, outbound: mpsc::Sender<eip::DataFrame>) -> Arc<Carrier> {
        Arc::new(Carrier {
            id: self.next_carrier.fetch_add(1, Ordering::Relaxed),
            stateless: true,
            initialized: AtomicBool::new(true),
            closed: AtomicBool::new(false),
            outbound,
        })
    }

    pub(crate) fn busy_response(&self, payload: &str) -> Vec<u8> {
        let request = eip::decode::<JsonRpcRequest>(payload).ok();
        self.error_response(
            request.as_ref().map(|request| request.id.clone()),
            request.and_then(|request| request.eip_session),
            protocol_error(ErrorType::Busy, "request capacity is exhausted"),
        )
    }

    /// Reserve before spawning a carrier task. Lifecycle traffic has bounded Session-local
    /// lanes that ordinary calls cannot consume, even when the Device is saturated.
    pub(crate) fn admit_payload(&self, payload: &str) -> Option<Vec<OwnedSemaphorePermit>> {
        let request = eip::decode::<JsonRpcRequest>(payload).ok();
        let session = request
            .as_ref()
            .and_then(|request| request.eip_session.as_deref())
            .and_then(|id| self.lookup(id).ok());
        if let (Some(request), Some(session)) = (&request, &session) {
            let lane = match request.method.as_str() {
                "session.keepalive" => Some(&session.keepalive_capacity),
                "session.attach" | "session.close" | "operation.cancel" | "process.kill"
                | "process.release" | "output.release" | "file.abort_writer"
                | "file.close_reader" => Some(&session.cleanup_capacity),
                _ => None,
            };
            if let Some(lane) = lane {
                return Some(vec![lane.clone().try_acquire_owned().ok()?]);
            }
        }
        let device = self.request_capacity.clone().try_acquire_owned().ok()?;
        let mut permits = vec![device];
        if let Some(session) = session {
            permits.push(session.request_capacity.clone().try_acquire_owned().ok()?);
        }
        Some(permits)
    }

    fn sessions(&self) -> MutexGuard<'_, BTreeMap<String, Arc<Session>>> {
        self.sessions.lock().unwrap_or_else(PoisonError::into_inner)
    }

    #[allow(
        clippy::result_large_err,
        reason = "the generated protocol error is the daemon's public error contract"
    )]
    fn lookup(&self, id: &str) -> Result<Arc<Session>, EIPError> {
        self.sessions()
            .get(id)
            .cloned()
            .ok_or_else(|| protocol_error(ErrorType::NotInitialized, "unknown or expired session"))
    }

    pub(crate) fn track_pending_payload(&self, payload: &str) -> Option<PendingOperationGuard> {
        let value: serde_json::Value = serde_json::from_str(payload).ok()?;
        let session = self.lookup(value.get("eip_session")?.as_str()?).ok()?;
        let operation_id = value
            .get("params")?
            .get("context")?
            .get("operation_id")?
            .as_str()?;
        Some(session.operations.track_pending(operation_id.to_owned()))
    }

    #[allow(
        clippy::result_large_err,
        reason = "the generated protocol error is the daemon's public error contract"
    )]
    fn select_session(
        &self,
        carrier: &Carrier,
        request: &JsonRpcRequest,
    ) -> Result<Arc<Session>, EIPError> {
        let session = self.lookup(request.eip_session.as_deref().ok_or_else(|| {
            protocol_error(ErrorType::InvalidRequest, "session selector is required")
        })?)?;
        {
            let mut state = session.session.state();
            let expired = state.last_activity.elapsed() >= self.config.session_idle_timeout
                || state
                    .detached_at
                    .is_some_and(|at| at.elapsed() >= self.config.disconnect_grace);
            if expired || state.lifecycle == SessionState::Closed {
                return Err(protocol_error(
                    ErrorType::NotInitialized,
                    "session is closed or expired",
                ));
            }
            if request.method == "session.attach" && state.owner.is_none() {
                session
                    .transfers
                    .attach(carrier.outbound.clone())
                    .map_err(map_transfer_error)?;
                state.owner = Some(carrier.id);
                state.detached_at = None;
            }
            if state.owner != Some(carrier.id) {
                return Err(protocol_error(
                    ErrorType::NotInitialized,
                    "session is not attached to this carrier",
                ));
            }
            state.last_activity = Instant::now();
        }
        session.preflight(&request.method)?;
        Ok(session)
    }

    pub(crate) fn validate_data_session(
        &self,
        carrier: &Carrier,
        id: &str,
    ) -> Result<(), TransferError> {
        let session = self.lookup(id).map_err(|_| TransferError::Protocol)?;
        let state = session.session.state();
        if carrier.closed.load(Ordering::Acquire)
            || state.owner != Some(carrier.id)
            || state.lifecycle != SessionState::Ready
            || state.last_activity.elapsed() >= self.config.session_idle_timeout
        {
            Err(TransferError::Protocol)
        } else {
            Ok(())
        }
    }

    pub(crate) fn fail_data_session(&self, carrier: &Carrier, id: &str) {
        if let Ok(session) = self.lookup(id) {
            let mut state = session.session.state();
            if state.owner == Some(carrier.id) {
                state.lifecycle = SessionState::Closed;
                session.closed.send_replace(true);
                session.operations.begin_drain();
                session.transfers.begin_session_close();
            }
        }
    }

    pub(crate) async fn handle_data_frame(
        &self,
        carrier: &Carrier,
        frame: eip::DataFrame,
    ) -> Result<(), TransferError> {
        self.validate_data_session(carrier, &frame.session_id)?;
        let session = self
            .lookup(&frame.session_id)
            .map_err(|_| TransferError::Protocol)?;
        let _work = session
            .session
            .admit_work()
            .ok_or(TransferError::Protocol)?;
        #[cfg(target_os = "linux")]
        if let Some(remote) = &session.remote {
            return remote
                .client
                .data(frame)
                .await
                .map_err(|_| TransferError::Protocol);
        }
        session.transfers.handle_frame(frame).await
    }

    pub(crate) async fn detach(&self, carrier: &Carrier) {
        carrier.closed.store(true, Ordering::Release);
        let sessions: Vec<_> = self.sessions().values().cloned().collect();
        let detached_at = Instant::now();
        let mut detaching = tokio::task::JoinSet::new();
        for session in sessions {
            {
                let mut state = session.session.state();
                if state.owner != Some(carrier.id) {
                    continue;
                }
                // All owned Sessions begin grace now, not after a sibling's cleanup.
                state.detached_at = Some(detached_at);
            }
            detaching.spawn(async move {
                let clean = tokio::time::timeout(Duration::from_secs(5), async {
                    let _cleanup = session.cleanup.lock().await;
                    #[cfg(target_os = "linux")]
                    if let Some(remote) = &session.remote {
                        let _ = remote.client.detach().await;
                    }
                    session.transfers.detach().await;
                })
                .await
                .is_ok();
                if !clean {
                    session.session.close();
                }
                // Publish detach only after the old route is removed. An attach
                // cannot install a new sender while old cleanup can still erase it.
                session.session.state().owner = None;
            });
        }
        while detaching.join_next().await.is_some() {}
    }

    pub(crate) async fn maintenance(&self) {
        let sessions: Vec<_> = self.sessions().values().cloned().collect();
        let mut closing = tokio::task::JoinSet::new();
        for session in sessions {
            let expired = {
                let mut state = session.session.state();
                let expired = state.lifecycle == SessionState::Closed
                    || state.last_activity.elapsed() >= self.config.session_idle_timeout
                    || state
                        .detached_at
                        .is_some_and(|at| at.elapsed() >= self.config.disconnect_grace);
                if expired {
                    state.lifecycle = SessionState::Closed;
                }
                expired
            };
            if expired {
                closing.spawn(async move {
                    let clean = session.close(Duration::from_secs(10)).await;
                    (session.descriptor.session_id.clone(), clean)
                });
            } else {
                session.transfers.expire().await;
                session.collect_history(false).await;
                let filesystem = session.filesystem.clone();
                let _ = tokio::task::spawn_blocking(move || filesystem.retry_cleanup()).await;
            }
        }
        while let Some(result) = closing.join_next().await {
            if let Ok((id, true)) = result {
                self.sessions().remove(&id);
            }
        }
        self.report_resources();
    }

    fn report_resources(&self) {
        {
            let mut last = self
                .last_diagnostics
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            if last.is_some_and(|at| at.elapsed() < Duration::from_secs(30)) {
                return;
            }
            *last = Some(Instant::now());
        }
        let sessions: Vec<_> = self.sessions().values().cloned().collect();
        let closing = sessions
            .iter()
            .filter(|session| session.session.state().lifecycle == SessionState::Closed)
            .count();
        let (spool_bytes, spool_objects) = self.retention_quota.usage();
        let (output_cleanup_failures, outputs_reclaimed) = self.retention_quota.diagnostics();
        let (staged_bytes, staged_objects) = self.filesystem.staging_usage();
        let (processes, process_records) = self.capacity.processes.available();
        let (transfers, transfer_records) = self.capacity.transfers.available();
        let limits = &self.config.limits;
        let record = serde_json::json!({
            "level": "info", "event": "a13n-envd.resource_totals",
            "sessions": sessions.len(), "closing_sessions": closing,
            "operation_records": limits.max_device_operation_records - self.capacity.operations.available_permits() as u64,
            "processes": limits.max_device_processes - processes as u64,
            "process_records": limits.max_device_process_records - process_records as u64,
            "transfers": limits.max_device_file_transfers - transfers as u64,
            "transfer_records": limits.max_device_file_transfer_records - transfer_records as u64,
            "spool_bytes": spool_bytes, "spool_objects": spool_objects,
            "staged_bytes": staged_bytes, "staged_objects": staged_objects,
            "outputs_reclaimed": outputs_reclaimed,
            "output_cleanup_failures": output_cleanup_failures,
            "staging_cleanup_failures": self.filesystem.cleanup_failures(),
        });
        eprintln!("{record}");
    }

    pub(crate) async fn drain(&self, budget: Duration) -> bool {
        self.draining.store(true, Ordering::Release);
        let sessions: Vec<_> = self.sessions().values().cloned().collect();
        let mut closing = tokio::task::JoinSet::new();
        for session in sessions {
            closing.spawn(async move {
                let clean = session.close(budget).await;
                (session.descriptor.session_id.clone(), clean)
            });
        }
        let mut complete = true;
        while let Some(result) = closing.join_next().await {
            match result {
                Ok((id, true)) => {
                    self.sessions().remove(&id);
                }
                _ => complete = false,
            }
        }
        complete
    }

    pub(crate) async fn handle_payload_for_carrier(
        &self,
        carrier: &Carrier,
        payload: &str,
    ) -> CarrierResponse {
        let attempt = self.next_attempt.fetch_add(1, Ordering::Relaxed);
        scope_carrier_attempt(
            attempt,
            Box::pin(self.handle_payload_core(carrier, payload)),
        )
        .await
    }

    async fn handle_payload_core(&self, carrier: &Carrier, payload: &str) -> CarrierResponse {
        let request = match eip::decode::<JsonRpcRequest>(payload) {
            Ok(request) => request,
            Err(error) => {
                let error_type = match error {
                    eip::DecodeError::Json(error) if error.is_syntax() || error.is_eof() => {
                        ErrorType::ParseError
                    }
                    _ => ErrorType::InvalidRequest,
                };
                return CarrierResponse::plain(self.error_response(
                    recover_request_id(payload),
                    None,
                    protocol_error(error_type, error_type_message(error_type)),
                ));
            }
        };
        let selector = request.eip_session.clone();
        let error_response = |error| {
            CarrierResponse::plain(self.error_response(
                Some(request.id.clone()),
                selector.clone(),
                error,
            ))
        };
        if !valid_request_id(&request.id) {
            return error_response(protocol_error(
                ErrorType::InvalidRequest,
                "invalid request ID",
            ));
        }
        if carrier.closed.load(Ordering::Acquire) || self.draining.load(Ordering::Acquire) {
            return error_response(protocol_error(
                ErrorType::NotInitialized,
                "carrier is closed",
            ));
        }
        if request.method != "initialize" && !carrier.initialized() {
            return error_response(protocol_error(
                ErrorType::NotInitialized,
                "initialize must precede Device access",
            ));
        }
        let Some(method) = eip::METHODS
            .iter()
            .find(|method| method.name == request.method)
        else {
            return error_response(protocol_error(
                ErrorType::MethodNotFound,
                "method not found",
            ));
        };
        if matches!(
            request.method.as_str(),
            "session.open"
                | "process.start"
                | "shell.exec"
                | "file.open_reader"
                | "file.open_writer"
        ) {
            let pressure = self
                .sessions()
                .values()
                .any(|session| session.under_pressure());
            if pressure {
                // Expired owners and expired history have priority over early eviction.
                self.maintenance().await;
                let sessions: Vec<_> = self.sessions().values().cloned().collect();
                for session in &sessions {
                    if !sessions.iter().any(|candidate| candidate.under_pressure()) {
                        break;
                    }
                    session.collect_history(true).await;
                }
            }
        }
        let session = if method.device_scoped {
            if selector.is_some() {
                return error_response(protocol_error(
                    ErrorType::InvalidRequest,
                    "Device methods cannot select a session",
                ));
            }
            None
        } else {
            match self.select_session(carrier, &request) {
                Ok(session) => Some(session),
                Err(error) => return error_response(error),
            }
        };
        #[cfg(target_os = "linux")]
        if let Some(session) = &session
            && session.remote.is_some()
            && !matches!(request.method.as_str(), "egress.update" | "session.close")
        {
            return match session.relay(carrier, &request).await {
                Ok(response) => response,
                Err(error) => error_response(error),
            };
        }
        let history = match &session {
            Some(session) => Some(session.history.clone().read_owned().await),
            None => None,
        };
        let params = serde_json::to_string(&request.params).expect("JSON params are serializable");
        let result = match &session {
            Some(session) => {
                eip::dispatch_session(session.as_ref(), &request.method, &params).await
            }
            None => {
                eip::dispatch_device(
                    &DeviceRequest {
                        daemon: self,
                        carrier,
                    },
                    &request.method,
                    &params,
                )
                .await
            }
        };
        let handoff = if let Some(session) = &session {
            if let Err(DispatchError::Method {
                error,
                method,
                params,
            }) = &result
            {
                session
                    .operations
                    .finish_dispatched_failure(method, params, error.clone());
            }
            match &result {
                Ok(success) => session
                    .operations
                    .active_response_handoff(success.method, &success.params),
                Err(DispatchError::Method { method, params, .. }) => {
                    session.operations.active_response_handoff(method, params)
                }
                _ => None,
            }
        } else {
            None
        };
        let payload = match result {
            Ok(success) => {
                if request.method == "session.close"
                    && let Some(id) = &selector
                {
                    self.sessions().remove(id);
                }
                let serde_json::Value::Object(fields) = success.result else {
                    return error_response(protocol_error(
                        ErrorType::InternalError,
                        "non-object method result",
                    ));
                };
                let response = JsonRpcSuccessResponse {
                    jsonrpc: "2.0".to_owned(),
                    id: request.id.clone(),
                    eip_session: selector.clone(),
                    result: fields.into_iter().collect(),
                    extensions: BTreeMap::new(),
                };
                match eip::encode(&response) {
                    Ok(bytes) if bytes.len() as u64 <= self.config.limits.max_response_bytes => {
                        bytes
                    }
                    _ => self.error_response(
                        Some(request.id),
                        selector,
                        protocol_error(ErrorType::InternalError, "response exceeds limits"),
                    ),
                }
            }
            Err(error) => self.error_response(
                Some(request.id),
                selector,
                map_dispatch_error(error, &request.method),
            ),
        };
        CarrierResponse {
            payload,
            handoff: history.map(|history| ResponseHandoff::Local {
                operation: handoff,
                _history: history,
            }),
        }
    }

    fn error_response(
        &self,
        id: Option<JsonRpcId>,
        eip_session: Option<String>,
        error: EIPError,
    ) -> Vec<u8> {
        let response = JsonRpcErrorResponse {
            jsonrpc: "2.0".to_owned(),
            id,
            eip_session,
            error,
            extensions: BTreeMap::new(),
        };
        eip::encode(&response).expect("bounded protocol errors serialize")
    }
}

impl EipDeviceHandler for DeviceRequest<'_> {
    async fn initialize(&self, params: InitializeParams) -> Result<InitializeResult, EIPError> {
        if !params
            .supported_protocol_versions
            .iter()
            .any(|version| version == eip::EIP_PROTOCOL_VERSION)
            || params
                .expected_device_id
                .as_ref()
                .is_some_and(|id| id != &self.daemon.descriptor.device_id)
        {
            return Err(protocol_error(
                ErrorType::ProtocolIncompatible,
                "Device identity or protocol does not match",
            ));
        }
        if !self.carrier.stateless && self.carrier.initialized.swap(true, Ordering::AcqRel) {
            return Err(protocol_error(
                ErrorType::AlreadyInitialized,
                "carrier is already initialized",
            ));
        }
        Ok(InitializeResult {
            protocol_version: eip::EIP_PROTOCOL_VERSION.to_owned(),
            server: EIPServerInfo {
                name: "a13n-envd".to_owned(),
                version: env!("CARGO_PKG_VERSION").to_owned(),
            },
            descriptor: self.daemon.descriptor.clone(),
        })
    }

    async fn device_describe(
        &self,
        _params: eip::DeviceDescribeParams,
    ) -> Result<eip::DeviceDescribeResult, EIPError> {
        Ok(eip::DeviceDescribeResult {
            descriptor: self.daemon.descriptor.clone(),
        })
    }

    async fn directory_list(
        &self,
        params: eip::DirectoryListParams,
    ) -> Result<eip::DirectoryListResult, EIPError> {
        self.daemon
            .check_device(&params.expected_device_id, params.expected_generation)?;
        if !self.daemon.config.directory_discovery {
            return Err(protocol_error(
                ErrorType::Unsupported,
                "directory discovery is disabled",
            ));
        }
        let bytes = self.daemon.config.limits.max_response_bytes;
        tokio::task::spawn_blocking(move || {
            device_path::list_directory(&params.path, params.offset, params.limit, bytes)
        })
        .await
        .map_err(|_| protocol_error(ErrorType::InternalError, "directory discovery failed"))?
        .map_err(|error| map_resource_error(crate::resource::map_path_error(error)))
    }

    async fn session_open(
        &self,
        params: eip::SessionOpenParams,
    ) -> Result<eip::SessionOpenResult, EIPError> {
        self.daemon
            .check_device(&params.expected_device_id, params.expected_generation)?;
        if params.protocol_version != eip::EIP_PROTOCOL_VERSION {
            return Err(protocol_error(
                ErrorType::ProtocolIncompatible,
                "session protocol does not match",
            ));
        }
        let methods = session_methods(&self.daemon.config);
        if params.required_methods.iter().any(|method| {
            !(methods.contains(method)
                || (method == "egress.update"
                    && params.egress.is_some()
                    && self.daemon.config.egress.enabled
                    && cfg!(target_os = "linux")))
        }) {
            return Err(protocol_error(
                ErrorType::Unsupported,
                "required session method is unavailable",
            ));
        }
        let path = params
            .working_directory
            .unwrap_or_else(|| self.daemon.config.default_working_directory.clone());
        let cwd = tokio::task::spawn_blocking(move || {
            device_path::from_native(&device_path::resolve_directory(&path)?)
        })
        .await
        .map_err(|_| protocol_error(ErrorType::InternalError, "cwd resolution failed"))?
        .map_err(|_| {
            protocol_error(
                ErrorType::NotFoundOrDenied,
                "working directory is unavailable",
            )
        })?;
        if let Some(policy) = params.egress {
            #[cfg(target_os = "linux")]
            return self.daemon.open_controlled(self.carrier, cwd, policy).await;
            #[cfg(not(target_os = "linux"))]
            {
                let _ = policy;
                return Err(protocol_error(
                    ErrorType::Unsupported,
                    "egress requires Linux",
                ));
            }
        }
        if self.daemon.sessions().len() >= self.daemon.config.limits.max_sessions {
            self.daemon.maintenance().await;
        }
        let mut sessions = self.daemon.sessions();
        if self.carrier.closed.load(Ordering::Acquire)
            || self.daemon.draining.load(Ordering::Acquire)
        {
            return Err(protocol_error(
                ErrorType::NotInitialized,
                "carrier closed during session opening",
            ));
        }
        if sessions.len() >= self.daemon.config.limits.max_sessions {
            return Err(protocol_error(
                ErrorType::Busy,
                "session capacity is exhausted",
            ));
        }
        let id = self.daemon.ids.next("session").map_err(map_ledger_error)?;
        let session = Arc::new(
            Session::new(self.daemon, self.carrier.id, id.clone(), cwd).map_err(|_| {
                protocol_error(ErrorType::InternalError, "session initialization failed")
            })?,
        );
        session
            .transfers
            .attach(self.carrier.outbound.clone())
            .map_err(map_transfer_error)?;
        let descriptor = session.descriptor.clone();
        sessions.insert(id, session);
        Ok(eip::SessionOpenResult { descriptor })
    }
}

impl Daemon {
    #[allow(
        clippy::result_large_err,
        reason = "the generated protocol error is the daemon's public error contract"
    )]
    fn check_device(&self, id: &str, generation: u64) -> Result<(), EIPError> {
        if id != self.descriptor.device_id || generation != self.descriptor.generation {
            Err(protocol_error(
                ErrorType::ProtocolIncompatible,
                "Device identity or generation does not match",
            ))
        } else {
            Ok(())
        }
    }
}

pub(crate) struct Session {
    #[cfg(target_os = "linux")]
    remote: Option<crate::egress::runtime::Runtime>,
    descriptor: SessionDescriptor,
    filesystem: Arc<DeviceFilesystem>,
    cleanup: tokio::sync::Mutex<()>,
    history: Arc<RwLock<()>>,
    request_capacity: Arc<Semaphore>,
    keepalive_capacity: Arc<Semaphore>,
    cleanup_capacity: Arc<Semaphore>,
    closed: watch::Sender<bool>,
    session: SessionAdmission,
    max_operation_duration: Duration,
    history_ttl: Duration,
    operations: OperationLedger,
    resources: ResourceRegistry,
    retention: RetentionStore,
    execution: Option<ExecutionManager>,
    transfers: TransferRegistry,
}

impl SessionAdmission {
    fn state(&self) -> MutexGuard<'_, SessionAdmissionState> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn admit_work(&self) -> Option<SessionWorkGuard<'_>> {
        let mut state = self.state();
        if state.lifecycle != SessionState::Ready {
            return None;
        }
        state.active_session_work = state
            .active_session_work
            .checked_add(1)
            .expect("session work accounting overflow");
        Some(SessionWorkGuard { admission: self })
    }

    fn close(&self) {
        self.state().lifecycle = SessionState::Closed;
    }

    async fn wait_until_idle(&self) {
        loop {
            let notified = self.idle.notified();
            if self.state().active_session_work == 0 {
                return;
            }
            notified.await;
        }
    }
}

impl Drop for SessionWorkGuard<'_> {
    fn drop(&mut self) {
        let mut state = self.admission.state();
        state.active_session_work = state
            .active_session_work
            .checked_sub(1)
            .expect("session work guard released exactly once");
        let idle = state.active_session_work == 0;
        drop(state);
        if idle {
            self.admission.idle.notify_waiters();
        }
    }
}

impl Session {
    fn new(
        daemon: &Daemon,
        owner: u64,
        session_id: String,
        working_directory: String,
    ) -> Result<Self, DaemonInitError> {
        let config = &daemon.config;
        let operations = OperationLedger::new(
            config.device_id.clone(),
            daemon.descriptor.generation,
            session_id.clone(),
            config.limits.max_operation_records as usize,
            Duration::from_millis(config.limits.operation_record_ttl_ms),
            Duration::from_millis(config.limits.max_operation_duration_ms),
            daemon.capacity.operations.clone(),
        );
        let retention =
            RetentionStore::new(config, daemon.ids.clone(), daemon.retention_quota.clone())
                .map_err(|_| DaemonInitError::new("retention initialization failed"))?;
        let execution = ExecutionManager::new(
            config,
            daemon.descriptor.generation,
            session_id.clone(),
            working_directory.clone(),
            daemon.ids.clone(),
            retention.clone(),
            daemon.capacity.processes.clone(),
        )
        .map_err(|_| DaemonInitError::new("execution initialization failed"))?;
        let transfers = TransferRegistry::new(
            config,
            session_id.clone(),
            daemon.ids.clone(),
            daemon.capacity.transfers.clone(),
        )
        .map_err(|_| DaemonInitError::new("transfer initialization failed"))?;
        let descriptor = SessionDescriptor {
            egress: None,
            device_id: config.device_id.clone(),
            generation: daemon.descriptor.generation,
            session_id,
            working_directory,
            available_methods: session_methods(config),
            limits: config.limits.descriptor(),
            shell_profiles: execution
                .as_ref()
                .map(ExecutionManager::shell_profiles)
                .unwrap_or_default(),
            execution_features: ExecutionFeatures {
                process_count_limit: false,
                memory_bytes_limit: false,
                cpu_time_limit: false,
                signal_interrupt: execution.is_some() && cfg!(unix),
                signal_terminate: execution.is_some() && cfg!(unix),
            },
            lifecycle: daemon.descriptor.lifecycle.clone(),
        };
        let (closed, _) = watch::channel(false);
        Ok(Self {
            #[cfg(target_os = "linux")]
            remote: None,
            closed,
            descriptor,
            filesystem: daemon.filesystem.for_session(),
            cleanup: tokio::sync::Mutex::new(()),
            history: Arc::new(RwLock::new(())),
            request_capacity: Arc::new(Semaphore::new(
                config.limits.max_concurrent_operations as usize,
            )),
            keepalive_capacity: Arc::new(Semaphore::new(1)),
            cleanup_capacity: Arc::new(Semaphore::new(4)),
            session: SessionAdmission {
                state: Mutex::new(SessionAdmissionState {
                    owner: Some(owner),
                    last_activity: Instant::now(),
                    detached_at: None,
                    lifecycle: SessionState::Initialized,
                    active_session_work: 0,
                }),
                idle: Notify::new(),
            },
            max_operation_duration: Duration::from_millis(config.limits.max_operation_duration_ms),
            history_ttl: Duration::from_millis(config.limits.operation_record_ttl_ms),
            resources: ResourceRegistry::new(config, operations.clone()),
            operations,
            retention,
            execution,
            transfers,
        })
    }

    #[allow(clippy::result_large_err, reason = "generated EIP error contract")]
    fn current_descriptor(&self) -> Result<SessionDescriptor, EIPError> {
        let descriptor = self.descriptor.clone();
        #[cfg(target_os = "linux")]
        let descriptor = {
            let mut descriptor = descriptor;
            if let Some(remote) = &self.remote {
                descriptor.egress = Some(remote.status().map_err(|_| {
                    protocol_error(ErrorType::NotInitialized, "egress broker is closed")
                })?);
            }
            descriptor
        };
        Ok(descriptor)
    }

    fn under_pressure(&self) -> bool {
        self.operations.under_pressure()
            || self.transfers.under_pressure()
            || self.retention.under_pressure()
            || self
                .execution
                .as_ref()
                .is_some_and(ExecutionManager::under_pressure)
    }

    async fn collect_history(&self, pressure: bool) {
        let Ok(_history) = self.history.try_write() else {
            return;
        };
        if self.session.state().lifecycle == SessionState::Closed {
            return;
        }
        if let Some(execution) = &self.execution {
            for (kind, selector) in execution.collect(self.history_ttl, pressure).await {
                self.operations.release_selector(&kind, &selector);
            }
        }
        for selector in self.retention.collect(self.history_ttl, pressure).await {
            self.operations.release_selector("output", &selector);
        }
        self.transfers.collect(pressure);
        self.operations.collect(pressure);
    }

    fn subscribe_closed(&self) -> watch::Receiver<bool> {
        self.closed.subscribe()
    }

    async fn close(&self, budget: Duration) -> bool {
        let Ok(_cleanup) = tokio::time::timeout(budget, self.cleanup.lock()).await else {
            return false;
        };
        self.session.close();
        self.closed.send_replace(true);
        self.operations.begin_drain();
        self.transfers.begin_session_close();
        #[cfg(target_os = "linux")]
        if let Some(remote) = &self.remote {
            return tokio::time::timeout(budget, remote.close()).await.is_ok();
        }
        // Fence and cancel first. Waiting for an active process before cancelling would deadlock close.
        tokio::time::timeout(budget, async {
            let execution_closed = match &self.execution {
                Some(execution) => execution.drain(budget / 2).await,
                None => true,
            };
            let transfers_closed = self.transfers.close_session().await;
            self.operations.wait_until_owned_idle().await;
            self.session.wait_until_idle().await;
            self.transfers.reconcile_committing().await;
            let outputs_closed = execution_closed && self.retention.close().await;
            let filesystem = self.filesystem.clone();
            let candidates_closed = tokio::task::spawn_blocking(move || filesystem.retry_cleanup())
                .await
                .unwrap_or(false);
            transfers_closed && outputs_closed && candidates_closed
        })
        .await
        .unwrap_or(false)
    }

    #[allow(
        clippy::result_large_err,
        reason = "the generated protocol error is the daemon's public error contract"
    )]
    async fn await_owned_operation<T>(
        &self,
        operation_id: &str,
        result: OwnedOperationResult<T>,
    ) -> Result<T, EIPError> {
        match result.await {
            Ok(Some(result)) => result,
            Ok(None) | Err(_) => Err(self
                .operations
                .failure_by_operation(operation_id)
                .unwrap_or_else(|| {
                    protocol_error(
                        ErrorType::InternalError,
                        "owned operation task ended without terminal evidence",
                    )
                })),
        }
    }

    #[allow(clippy::result_large_err)]
    fn preflight(&self, method: &str) -> Result<(), EIPError> {
        let state = self.session.state();
        if state.lifecycle == SessionState::Closed {
            return Err(protocol_error(
                ErrorType::NotInitialized,
                "session is closed",
            ));
        }
        if !self
            .descriptor
            .available_methods
            .iter()
            .any(|available| available == method)
        {
            return Err(protocol_error(
                ErrorType::Unsupported,
                "method is not available",
            ));
        }
        if state.lifecycle != SessionState::Ready
            && !matches!(
                method,
                "environment.readiness" | "session.attach" | "session.keepalive" | "session.close"
            )
        {
            return Err(protocol_error(
                ErrorType::NotInitialized,
                "session readiness is not established",
            ));
        }
        Ok(())
    }

    #[allow(clippy::result_large_err)]
    fn execution_manager(&self) -> Result<&ExecutionManager, EIPError> {
        self.execution.as_ref().ok_or_else(|| {
            protocol_error(
                ErrorType::Unsupported,
                "command and process execution is not configured",
            )
        })
    }

    #[allow(clippy::result_large_err)]
    fn ensure_ready(&self) -> Result<(), EIPError> {
        if self.session.state().lifecycle == SessionState::Ready {
            Ok(())
        } else {
            Err(protocol_error(
                ErrorType::NotInitialized,
                "session readiness is not established",
            ))
        }
    }

    #[allow(clippy::result_large_err)]
    fn admit_record<P: Serialize>(
        &self,
        method: &str,
        context: &eip::EIPCallContext,
        params: &P,
    ) -> Result<BeginOutcome, EIPError> {
        let session = self.session.state();
        if session.lifecycle != SessionState::Ready {
            return Err(protocol_error(
                ErrorType::NotInitialized,
                "session readiness is not established",
            ));
        }
        self.begin_record(method, context, params)
    }

    #[allow(clippy::result_large_err)]
    fn admit_owned_record<'a, P: Serialize>(
        &'a self,
        method: &str,
        context: &eip::EIPCallContext,
        params: &P,
    ) -> Result<(SessionWorkGuard<'a>, BeginOutcome), EIPError> {
        let work = self.session.admit_work().ok_or_else(|| {
            protocol_error(ErrorType::NotInitialized, "session is not initialized")
        })?;
        let operation = self.begin_record(method, context, params)?;
        Ok((work, operation))
    }

    #[allow(clippy::result_large_err)]
    fn begin_record<P: Serialize>(
        &self,
        method: &str,
        context: &eip::EIPCallContext,
        params: &P,
    ) -> Result<BeginOutcome, EIPError> {
        self.operations
            .begin(method, context, params)
            .map_err(map_ledger_error)
    }

    #[allow(clippy::result_large_err)]
    fn decode_replay<R: DeserializeOwned>(&self, value: serde_json::Value) -> Result<R, EIPError> {
        serde_json::from_value(value).map_err(|_| {
            protocol_error(
                ErrorType::InternalError,
                "retained operation result failed validation",
            )
        })
    }
}

impl EipSessionHandler for Session {
    async fn environment_readiness(
        &self,
        params: EnvironmentReadinessParams,
    ) -> Result<EnvironmentReadinessResult, EIPError> {
        let (operation, ready) = {
            let mut session = self.session.state();
            if !matches!(
                session.lifecycle,
                SessionState::Initialized | SessionState::Ready
            ) {
                return Err(protocol_error(
                    ErrorType::NotInitialized,
                    "session cannot perform readiness",
                ));
            }
            let (operation, ready) = self
                .operations
                .begin_readiness(&params.context, &params)
                .map_err(map_ledger_error)?;
            session.lifecycle = if ready {
                SessionState::Ready
            } else {
                SessionState::NotReady
            };
            (operation, ready)
        };
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let descriptor = &self.descriptor;
        let result = EnvironmentReadinessResult {
            ready,
            device_id: descriptor.device_id.clone(),
            session_id: descriptor.session_id.clone(),
            generation: descriptor.generation,
        };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn environment_describe(
        &self,
        params: EnvironmentDescribeParams,
    ) -> Result<EnvironmentDescribeResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("environment.describe", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let result = EnvironmentDescribeResult {
            descriptor: self.current_descriptor()?,
        };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn session_attach(
        &self,
        _params: eip::SessionAttachParams,
    ) -> Result<eip::SessionOpenResult, EIPError> {
        Ok(eip::SessionOpenResult {
            descriptor: self.current_descriptor()?,
        })
    }

    async fn session_keepalive(
        &self,
        _params: eip::SessionKeepaliveParams,
    ) -> Result<eip::SessionKeepaliveResult, EIPError> {
        Ok(eip::SessionKeepaliveResult { alive: true })
    }

    async fn egress_update(
        &self,
        params: eip::EgressUpdateParams,
    ) -> Result<eip::EgressUpdateResult, EIPError> {
        #[cfg(target_os = "linux")]
        if let Some(remote) = &self.remote {
            return remote
                .update(params)
                .map(|egress| eip::EgressUpdateResult { egress })
                .map_err(egress::policy_error);
        }
        #[cfg(not(target_os = "linux"))]
        let _ = params;
        Err(protocol_error(
            ErrorType::Unsupported,
            "Session has no egress policy",
        ))
    }

    async fn session_close(
        &self,
        _params: SessionCloseParams,
    ) -> Result<SessionCloseResult, EIPError> {
        if !self.close(Duration::from_secs(10)).await {
            return Err(protocol_error(
                ErrorType::InternalError,
                "session cleanup is incomplete",
            ));
        }
        Ok(SessionCloseResult { closed: true })
    }

    async fn file_open_reader(
        &self,
        params: eip::FileReaderOpenParams,
    ) -> Result<eip::FileReaderOpenResult, EIPError> {
        self.transfers.expire().await;
        let _work = self.session.admit_work().ok_or_else(|| {
            protocol_error(ErrorType::NotInitialized, "session is not initialized")
        })?;
        let operation = self
            .operations
            .begin("file.open_reader", &params.context, &params)
            .map_err(map_ledger_error)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let filesystem = self.filesystem.clone();
        let result = self
            .transfers
            .open_reader(&filesystem, &params)
            .await
            .map_err(map_transfer_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_close_reader(
        &self,
        params: eip::FileReaderCloseParams,
    ) -> Result<eip::FileReaderCloseResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("file.close_reader", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let result = self
            .transfers
            .close_reader(&params.reader)
            .await
            .map_err(map_transfer_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_open_writer(
        &self,
        params: eip::FileWriterOpenParams,
    ) -> Result<eip::FileWriterOpenResult, EIPError> {
        self.transfers.expire().await;
        let _work = self.session.admit_work().ok_or_else(|| {
            protocol_error(ErrorType::NotInitialized, "session is not initialized")
        })?;
        let operation = self
            .operations
            .begin("file.open_writer", &params.context, &params)
            .map_err(map_ledger_error)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let filesystem = self.filesystem.clone();
        let result = self
            .transfers
            .open_writer(&filesystem, &params)
            .await
            .map_err(map_transfer_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_abort_writer(
        &self,
        params: eip::FileWriterAbortParams,
    ) -> Result<eip::FileWriterAbortResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("file.abort_writer", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let result = eip::FileWriterAbortResult {
            status: self
                .transfers
                .abort_writer(&params.writer)
                .await
                .map_err(map_transfer_error)?,
        };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_commit_writer(
        &self,
        params: eip::FileWriterCommitParams,
    ) -> Result<eip::FileWriterCommitResult, EIPError> {
        let work = self.session.admit_work().ok_or_else(|| {
            protocol_error(ErrorType::NotInitialized, "session is not initialized")
        })?;
        let operation = match self.begin_record("file.commit_writer", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.commit_writer")?;
        let commit = match self.transfers.prepare_commit(&params).await {
            Ok(commit) => commit,
            Err(error) => {
                return Err(mutation_failure(
                    operation,
                    "file.commit_writer",
                    map_transfer_error(error),
                ));
            }
        };
        let operation_id = params.context.operation_id.clone();
        let operations = self.operations.clone();
        let commit_operation_id = operation_id.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let committed = match commit.execute(operations, commit_operation_id).await {
                    Ok(committed) => committed,
                    Err(error) => {
                        return Err(mutation_failure(
                            operation,
                            "file.commit_writer",
                            map_transfer_error(error),
                        ));
                    }
                };
                let result = eip::FileWriterCommitResult {
                    info: committed.info,
                    transferred_bytes: committed.transferred_bytes,
                    transfer_digest: committed.transfer_digest,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_ledger_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_stat(
        &self,
        params: eip::FileStatParams,
    ) -> Result<eip::FileStatResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("file.stat", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.stat(&filesystem, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_read_text(
        &self,
        params: eip::FileReadTextParams,
    ) -> Result<eip::FileReadTextResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("file.read_text", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.read_text(&filesystem, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_list(
        &self,
        params: eip::FileListParams,
    ) -> Result<eip::FileListResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("file.list", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.list(&filesystem, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_find(
        &self,
        params: eip::FileFindParams,
    ) -> Result<eip::FileFindResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("file.find", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.find(&filesystem, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_search(
        &self,
        params: eip::FileSearchParams,
    ) -> Result<eip::FileSearchResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("file.search", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.search(&filesystem, &call))
            .await
            .map_err(|_| protocol_error(ErrorType::InternalError, "resource worker failed"))?
            .map_err(map_resource_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn file_write_text(
        &self,
        params: eip::FileWriteTextParams,
    ) -> Result<eip::FileWriteTextResult, EIPError> {
        self.ensure_ready()?;
        let (work, operation) =
            self.admit_owned_record("file.write_text", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.write_text")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let write =
                    tokio::task::spawn_blocking(move || resources.write_text(&filesystem, &params))
                        .await;
                let (info, bytes_written) = match write {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.write_text",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.write_text",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileWriteTextResult {
                    info,
                    bytes_written,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_ledger_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_commit(
        &self,
        params: eip::FileCommitParams,
    ) -> Result<eip::FileCommitResult, EIPError> {
        self.ensure_ready()?;
        let (work, operation) = self.admit_owned_record("file.commit", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.commit")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let write =
                    tokio::task::spawn_blocking(move || resources.commit(&filesystem, &params))
                        .await;
                let files_written = match write {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.commit",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.commit",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileCommitResult {
                    files_written,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_ledger_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_mkdir(
        &self,
        params: eip::FileMkdirParams,
    ) -> Result<eip::FileMkdirResult, EIPError> {
        self.ensure_ready()?;
        let (work, operation) = self.admit_owned_record("file.mkdir", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.mkdir")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let mkdir =
                    tokio::task::spawn_blocking(move || resources.mkdir(&filesystem, &params))
                        .await;
                let (info, created_directories) = match mkdir {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.mkdir",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.mkdir",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileMkdirResult {
                    info,
                    created_directories,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_ledger_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_patch_text(
        &self,
        params: eip::FilePatchTextParams,
    ) -> Result<eip::FilePatchTextResult, EIPError> {
        self.ensure_ready()?;
        let (work, operation) =
            self.admit_owned_record("file.patch_text", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.patch_text")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let patch =
                    tokio::task::spawn_blocking(move || resources.patch_text(&filesystem, &params))
                        .await;
                let (info, hunks_applied) = match patch {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.patch_text",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.patch_text",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FilePatchTextResult {
                    info,
                    hunks_applied,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_ledger_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_copy(
        &self,
        params: eip::FileCopyParams,
    ) -> Result<eip::FileCopyResult, EIPError> {
        self.ensure_ready()?;
        let (work, operation) = self.admit_owned_record("file.copy", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.copy")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let copy =
                    tokio::task::spawn_blocking(move || resources.copy(&filesystem, &params)).await;
                let (destination, bytes_copied) = match copy {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.copy",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.copy",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileCopyResult {
                    destination,
                    bytes_copied,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_ledger_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_move(
        &self,
        params: eip::FileMoveParams,
    ) -> Result<eip::FileMoveResult, EIPError> {
        self.ensure_ready()?;
        let (work, operation) = self.admit_owned_record("file.move", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.move")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let moved =
                    tokio::task::spawn_blocking(move || resources.move_path(&filesystem, &params))
                        .await;
                let destination = match moved {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.move",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.move",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileMoveResult {
                    destination,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_ledger_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn file_remove(
        &self,
        params: eip::FileRemoveParams,
    ) -> Result<eip::FileRemoveResult, EIPError> {
        self.ensure_ready()?;
        let (work, operation) = self.admit_owned_record("file.remove", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "file.remove")?;
        let operation_id = params.context.operation_id.clone();
        let resources = self.resources.clone();
        let filesystem = self.filesystem.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let removed =
                    tokio::task::spawn_blocking(move || resources.remove(&filesystem, &params))
                        .await;
                let removed_entries = match removed {
                    Ok(Ok(result)) => result,
                    Ok(Err(error)) => {
                        return Err(mutation_failure(
                            operation,
                            "file.remove",
                            map_resource_error(error),
                        ));
                    }
                    Err(_) => {
                        return Err(mutation_failure(
                            operation,
                            "file.remove",
                            protocol_error(ErrorType::InternalError, "resource worker failed"),
                        ));
                    }
                };
                let result = eip::FileRemoveResult {
                    removed_entries,
                    receipt: receipt.clone(),
                };
                operation
                    .finish(&result, Some(receipt))
                    .map_err(map_ledger_error)?;
                Ok(result)
            });
        drop(work);
        self.await_owned_operation(&operation_id, owned).await
    }

    async fn operation_cancel(
        &self,
        params: eip::OperationCancelParams,
    ) -> Result<eip::OperationCancelResult, EIPError> {
        self.ensure_ready()?;
        let deadline = effective_deadline(params.context.timeout_ms, self.max_operation_duration)?;
        let operation = match self.admit_record("operation.cancel", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let mut status = self.operations.cancel(&params.target_operation_id);
        if status == eip::OperationCancelStatus::NotFound {
            match self
                .operations
                .wait_for_admission(
                    &params.target_operation_id,
                    deadline,
                    self.subscribe_closed(),
                )
                .await
            {
                PendingAdmissionWait::Admitted => {
                    status = self.operations.cancel(&params.target_operation_id);
                }
                PendingAdmissionWait::Removed => {}
                PendingAdmissionWait::TimedOut => {
                    return Err(protocol_error(
                        ErrorType::Timeout,
                        "operation cancel deadline expired before target admission",
                    ));
                }
                PendingAdmissionWait::Closed => {
                    return Err(protocol_error(
                        ErrorType::NotInitialized,
                        "session closed before target operation admission",
                    ));
                }
            }
        }
        let result = eip::OperationCancelResult { status };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn receipt_get(
        &self,
        params: eip::ReceiptGetParams,
    ) -> Result<eip::ReceiptGetResult, EIPError> {
        self.ensure_ready()?;
        // Snapshot the target before this observation competes for ledger capacity. The
        // target may itself be reclaimable reconciliation evidence.
        let receipt = self.operations.receipt_by_operation(&params.operation_id);
        let operation = match self.admit_record("receipt.get", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let receipt = receipt
            .ok_or_else(|| protocol_error(ErrorType::NotFoundOrDenied, "receipt was not found"))?;
        let result = eip::ReceiptGetResult { receipt };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn output_read(
        &self,
        params: eip::OutputReadParams,
    ) -> Result<eip::OutputReadResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("output.read", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let deadline = effective_deadline(params.context.timeout_ms, self.max_operation_duration)?;
        let result = self
            .retention
            .read(&params, deadline)
            .await
            .map_err(map_retention_error)?;
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn output_release(
        &self,
        params: eip::OutputReleaseParams,
    ) -> Result<eip::OutputReleaseResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("output.release", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (operation, receipt) = mutation_receipt(operation, "output.release")?;
        let released = match self.retention.release_reference(&params.reference).await {
            Ok(released) => released,
            Err(error) => {
                return Err(mutation_failure(
                    operation,
                    "output.release",
                    map_retention_error(error),
                ));
            }
        };
        if released {
            self.operations
                .release_selector("output", &params.reference.0);
        }
        let result = eip::OutputReleaseResult {
            released,
            receipt: receipt.clone(),
        };
        operation
            .finish(&result, Some(receipt))
            .map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn port_inspect(
        &self,
        params: eip::PortInspectParams,
    ) -> Result<eip::PortInspectResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("port.inspect", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let deadline = effective_deadline(params.context.timeout_ms, self.max_operation_duration)?;
        let observation = inspect_port(&params.target, deadline).await;
        let result = eip::PortInspectResult { observation };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn port_wait(
        &self,
        params: eip::PortWaitParams,
    ) -> Result<eip::PortWaitResult, EIPError> {
        self.ensure_ready()?;
        let requested_deadline = params.context.timeout_ms.ok_or_else(|| {
            protocol_error(
                ErrorType::InvalidParams,
                "port.wait requires a finite context deadline",
            )
        })?;
        let operation = match self.admit_record("port.wait", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let deadline = effective_deadline(Some(requested_deadline), self.max_operation_duration)?;
        loop {
            if self
                .operations
                .cancellation_requested(&params.context.operation_id)
            {
                return Err(protocol_error(
                    ErrorType::Cancelled,
                    "port wait was cancelled",
                ));
            }
            let observation = inspect_port(&params.target, deadline).await;
            let desired = match params.desired_status {
                eip::DesiredPortStatus::Listening => eip::PortStatus::Listening,
                eip::DesiredPortStatus::NotListening => eip::PortStatus::NotListening,
            };
            if observation.status == desired {
                let result = eip::PortWaitResult { observation };
                operation.finish(&result, None).map_err(map_ledger_error)?;
                return Ok(result);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(protocol_error(
                    ErrorType::Timeout,
                    "port wait deadline has expired",
                ));
            }
            tokio::time::sleep(remaining.min(Duration::from_millis(25))).await;
        }
    }

    async fn process_start(
        &self,
        params: eip::ProcessStartParams,
    ) -> Result<eip::ProcessStartResult, EIPError> {
        self.ensure_ready()?;
        let execution = self.execution_manager()?;
        let filesystem = self.filesystem.clone();
        let operation = self.begin_record("process.start", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        #[cfg(target_os = "linux")]
        let request =
            crate::egress::worker::request_environment(&params.request).map_err(|()| {
                protocol_error(
                    ErrorType::InvalidParams,
                    "command cannot override egress environment variables",
                )
            })?;
        #[cfg(not(target_os = "linux"))]
        let request = std::borrow::Cow::Borrowed(&params.request);
        let started = match execution
            .start(&filesystem, &request, true, || {
                match self.operations.interruption(&params.context.operation_id) {
                    Some(OperationInterruption::Cancelled) => {
                        return Err(ProcessError::PreDispatchCancelled);
                    }
                    Some(OperationInterruption::TimedOut) => {
                        return Err(ProcessError::PreDispatchTimeout);
                    }
                    None => {}
                }
                Ok(())
            })
            .await
        {
            Ok(started) => started,
            Err(StartFailure { error, started }) => {
                let mapped = started.as_ref().map_or_else(
                    || map_process_error(error),
                    |started| map_started_process_error(error, started, execution),
                );
                return Err(
                    if matches!(
                        error,
                        ProcessError::PreDispatchCancelled | ProcessError::PreDispatchTimeout
                    ) && started.is_none()
                    {
                        mutation_pre_dispatch_failure(operation, "process.start", mapped)
                    } else {
                        mutation_failure(operation, "process.start", mapped)
                    },
                );
            }
        };
        if let Some(interruption) = self.operations.interruption(&params.context.operation_id) {
            let reason = match interruption {
                OperationInterruption::Cancelled => crate::supervisor::StopReason::Cancelled,
                OperationInterruption::TimedOut => crate::supervisor::StopReason::Timeout,
            };
            if let Err(error) = execution
                .interrupt_started(&started, reason, Instant::now() + Duration::from_secs(3))
                .await
            {
                return Err(mutation_failure(
                    operation,
                    "process.start",
                    map_started_process_error(error, &started, execution),
                ));
            }
            execution.release_started(&started);
            let error_type = match interruption {
                OperationInterruption::Cancelled => ErrorType::Cancelled,
                OperationInterruption::TimedOut => ErrorType::Timeout,
            };
            return Err(mutation_failure(
                operation,
                "process.start",
                protocol_error(error_type, "process start was interrupted"),
            ));
        }
        let (operation, receipt) =
            mutation_receipt_at(operation, "process.start", ReceiptStage::ExecConfirmed)?;
        let process = match started.info(execution) {
            Ok(process) => process,
            Err(error) => {
                return Err(mutation_failure(
                    operation,
                    "process.start",
                    map_process_error(error),
                ));
            }
        };
        let result = eip::ProcessStartResult {
            process,
            receipt: receipt.clone(),
        };
        operation
            .finish(&result, Some(receipt))
            .map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn process_inspect(
        &self,
        params: eip::ProcessInspectParams,
    ) -> Result<eip::ProcessInspectResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("process.inspect", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let result = eip::ProcessInspectResult {
            process: self
                .execution_manager()?
                .inspect(&params.handle)
                .map_err(map_process_error)?,
        };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn process_wait(
        &self,
        params: eip::ProcessWaitParams,
    ) -> Result<eip::ProcessWaitResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("process.wait", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let deadline = effective_deadline(params.context.timeout_ms, self.max_operation_duration)?;
        let process = loop {
            if let Some(interruption) = self.operations.interruption(&params.context.operation_id) {
                let error_type = match interruption {
                    OperationInterruption::Cancelled => ErrorType::Cancelled,
                    OperationInterruption::TimedOut => ErrorType::Timeout,
                };
                return Err(protocol_error(error_type, "process wait was interrupted"));
            }
            let slice = (Instant::now() + Duration::from_millis(25)).min(deadline);
            match self
                .execution_manager()?
                .wait(&params.handle, params.condition, slice)
                .await
            {
                Ok(process) => break process,
                Err(ProcessError::Timeout) if Instant::now() < deadline => continue,
                Err(error) => return Err(map_process_error(error)),
            }
        };
        let result = eip::ProcessWaitResult { process };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn process_write_stdin(
        &self,
        params: eip::ProcessWriteStdinParams,
    ) -> Result<eip::ProcessWriteStdinResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("process.write_stdin", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (accepted_bytes, stdin_open) = match self
            .execution_manager()?
            .write_stdin(&params.handle, &params.data, params.close_after_write)
            .await
        {
            Ok(result) => result,
            Err(error) => {
                return Err(mutation_failure(
                    operation,
                    "process.write_stdin",
                    map_process_error(error),
                ));
            }
        };
        let (operation, receipt) = mutation_receipt(operation, "process.write_stdin")?;
        let result = eip::ProcessWriteStdinResult {
            accepted_bytes,
            stdin_open,
            receipt: receipt.clone(),
        };
        operation
            .finish(&result, Some(receipt))
            .map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn process_close_stdin(
        &self,
        params: eip::ProcessCloseStdinParams,
    ) -> Result<eip::ProcessCloseStdinResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("process.close_stdin", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        if let Err(error) = self.execution_manager()?.close_stdin(&params.handle).await {
            return Err(mutation_failure(
                operation,
                "process.close_stdin",
                map_process_error(error),
            ));
        }
        let (operation, receipt) = mutation_receipt(operation, "process.close_stdin")?;
        let result = eip::ProcessCloseStdinResult {
            stdin_open: false,
            receipt: receipt.clone(),
        };
        operation
            .finish(&result, Some(receipt))
            .map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn process_signal(
        &self,
        params: eip::ProcessSignalParams,
    ) -> Result<eip::ProcessSignalResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("process.signal", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let (accepted, process) = match self
            .execution_manager()?
            .signal(&params.handle, params.signal)
            .await
        {
            Ok(result) => result,
            Err(error) => {
                return Err(mutation_failure(
                    operation,
                    "process.signal",
                    map_process_error(error),
                ));
            }
        };
        let (operation, receipt) =
            mutation_receipt_at(operation, "process.signal", ReceiptStage::Dispatched)?;
        let result = eip::ProcessSignalResult {
            accepted,
            process,
            receipt: receipt.clone(),
        };
        operation
            .finish(&result, Some(receipt))
            .map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn process_kill(
        &self,
        params: eip::ProcessKillParams,
    ) -> Result<eip::ProcessKillResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("process.kill", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let deadline = effective_deadline(params.context.timeout_ms, self.max_operation_duration)?;
        let execution = self.execution_manager()?;
        let process = match execution
            .kill(
                &params.handle,
                crate::supervisor::StopReason::Kill,
                deadline,
            )
            .await
        {
            Ok(process) => process,
            Err(error) => {
                let mapped = match execution.inspect(&params.handle) {
                    Ok(process) => map_process_error_with_evidence(error, &process),
                    Err(_) => map_process_error(error),
                };
                return Err(mutation_failure(operation, "process.kill", mapped));
            }
        };
        let (operation, receipt) = mutation_receipt(operation, "process.kill")?;
        let result = eip::ProcessKillResult {
            process,
            receipt: receipt.clone(),
        };
        operation
            .finish(&result, Some(receipt))
            .map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn process_release(
        &self,
        params: eip::ProcessReleaseParams,
    ) -> Result<eip::ProcessReleaseResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("process.release", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let released = match self.execution_manager()?.release(&params.handle) {
            Ok(released) => released,
            Err(error) => {
                return Err(mutation_failure(
                    operation,
                    "process.release",
                    map_process_error(error),
                ));
            }
        };
        if released {
            self.operations
                .release_selector("process", &params.handle.0);
        }
        let (operation, receipt) = mutation_receipt(operation, "process.release")?;
        let result = eip::ProcessReleaseResult {
            released,
            receipt: receipt.clone(),
        };
        operation
            .finish(&result, Some(receipt))
            .map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn shell_exec(
        &self,
        params: eip::ShellExecParams,
    ) -> Result<eip::ShellExecResult, EIPError> {
        self.ensure_ready()?;
        let operation = match self.admit_record("shell.exec", &params.context, &params)? {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let execution = self.execution_manager()?;
        let filesystem = self.filesystem.clone();
        let hard_deadline =
            effective_deadline(params.context.timeout_ms, self.max_operation_duration)?;
        #[cfg(target_os = "linux")]
        let request =
            crate::egress::worker::request_environment(&params.request).map_err(|()| {
                protocol_error(
                    ErrorType::InvalidParams,
                    "command cannot override egress environment variables",
                )
            })?;
        #[cfg(not(target_os = "linux"))]
        let request = std::borrow::Cow::Borrowed(&params.request);
        let started = match execution
            .start(&filesystem, &request, true, || {
                match self.operations.interruption(&params.context.operation_id) {
                    Some(OperationInterruption::Cancelled) => {
                        return Err(ProcessError::PreDispatchCancelled);
                    }
                    Some(OperationInterruption::TimedOut) => {
                        return Err(ProcessError::PreDispatchTimeout);
                    }
                    None => {}
                }
                Ok(())
            })
            .await
        {
            Ok(started) => started,
            Err(StartFailure { error, started }) => {
                let mapped = started.as_ref().map_or_else(
                    || map_process_error(error),
                    |started| map_started_process_error(error, started, execution),
                );
                return Err(
                    if matches!(
                        error,
                        ProcessError::PreDispatchCancelled | ProcessError::PreDispatchTimeout
                    ) && started.is_none()
                    {
                        mutation_pre_dispatch_failure(operation, "shell.exec", mapped)
                    } else {
                        mutation_failure(operation, "shell.exec", mapped)
                    },
                );
            }
        };
        let process = loop {
            if let Some(interruption) = self.operations.interruption(&params.context.operation_id) {
                let reason = match interruption {
                    OperationInterruption::Cancelled => crate::supervisor::StopReason::Cancelled,
                    OperationInterruption::TimedOut => crate::supervisor::StopReason::Timeout,
                };
                match execution
                    .interrupt_started(&started, reason, Instant::now() + Duration::from_secs(3))
                    .await
                {
                    Ok(process) => break process,
                    Err(error) => {
                        return Err(mutation_failure(
                            operation,
                            "shell.exec",
                            map_started_process_error(error, &started, execution),
                        ));
                    }
                }
            }
            let slice = (Instant::now() + Duration::from_millis(25)).min(hard_deadline);
            match execution
                .wait_started(&started, eip::ProcessWaitCondition::TreeCleaned, slice)
                .await
            {
                Ok(process) => break process,
                Err(ProcessError::Timeout) if Instant::now() < hard_deadline => continue,
                Err(ProcessError::Timeout) => {
                    match execution
                        .interrupt_started(
                            &started,
                            crate::supervisor::StopReason::Timeout,
                            Instant::now() + Duration::from_secs(3),
                        )
                        .await
                    {
                        Ok(process) => break process,
                        Err(error) => {
                            return Err(mutation_failure(
                                operation,
                                "shell.exec",
                                map_started_process_error(error, &started, execution),
                            ));
                        }
                    }
                }
                Err(error) => {
                    execution.release_started(&started);
                    return Err(mutation_failure(
                        operation,
                        "shell.exec",
                        map_process_error(error),
                    ));
                }
            }
        };
        if process.status.termination_reason == Some(eip::TerminationReason::BackendLost) {
            let release = execution.prepare_started_release(&started);
            let error = if release.is_some() {
                map_process_error_with_output_evidence(ProcessError::UnknownOutcome, &process)
            } else {
                map_process_error_with_evidence(ProcessError::UnknownOutcome, &process)
            };
            if let Some(release) = release {
                release.preserve_output();
            }
            return Err(mutation_failure(operation, "shell.exec", error));
        }
        if process.status.termination_reason == Some(eip::TerminationReason::OutputLimit) {
            let release = execution.prepare_started_release(&started);
            let error = if release.is_some() {
                map_process_error_with_output_evidence(ProcessError::OutputLimit, &process)
            } else {
                map_process_error_with_evidence(ProcessError::OutputLimit, &process)
            };
            if let Some(release) = release {
                release.preserve_output();
            }
            return Err(mutation_failure(operation, "shell.exec", error));
        }
        let outcome = match process.status.phase {
            eip::ProcessPhase::TimedOut => ReceiptOutcome::TimedOut,
            eip::ProcessPhase::Cancelled => ReceiptOutcome::Cancelled,
            eip::ProcessPhase::Failed => ReceiptOutcome::Failed,
            _ => ReceiptOutcome::Succeeded,
        };
        let (operation, mut receipt) =
            mutation_receipt_at(operation, "shell.exec", ReceiptStage::Completed)?;
        receipt.outcome = Some(outcome);
        let result = eip::ShellExecResult {
            status: process.status,
            output: process.output,
            receipt: receipt.clone(),
        };
        let pending_release = execution.prepare_started_release(&started);
        if let Err(error) = operation.finish(&result, Some(receipt)) {
            return Err(map_ledger_error(error));
        }
        if let Some(release) = pending_release {
            release.preserve_output();
        }
        Ok(result)
    }
}

#[allow(clippy::result_large_err)]
fn mutation_pre_dispatch_failure(
    operation: OperationLease,
    method: &str,
    mut error: EIPError,
) -> EIPError {
    let outcome = match error.data.error_type {
        ErrorType::Cancelled => ReceiptOutcome::Cancelled,
        ErrorType::Timeout => ReceiptOutcome::TimedOut,
        _ => ReceiptOutcome::Failed,
    };
    if let Ok(receipt) = operation.receipt(method, ReceiptStage::Accepted, Some(outcome)) {
        error.data.dispatch_stage = DispatchStage::PreDispatch;
        error.data.operation_id = Some(receipt.operation_id.clone());
        error.data.device_id = Some(receipt.device_id.clone());
        error.data.session_id = Some(receipt.session_id.clone());
        error.data.generation = Some(receipt.generation);
        error.data.receipt = Some(receipt.clone());
        operation.finish_failure(receipt, error.clone());
    }
    error
}

#[allow(clippy::result_large_err)]
fn mutation_failure(operation: OperationLease, method: &str, mut error: EIPError) -> EIPError {
    let (stage, outcome) = match error.data.error_type {
        ErrorType::UnknownOutcome => (ReceiptStage::Unknown, ReceiptOutcome::Unknown),
        ErrorType::Cancelled => (ReceiptStage::Completed, ReceiptOutcome::Cancelled),
        ErrorType::Timeout => (ReceiptStage::Completed, ReceiptOutcome::TimedOut),
        _ => (ReceiptStage::Completed, ReceiptOutcome::Failed),
    };
    if let Ok(receipt) = operation.receipt(method, stage, Some(outcome)) {
        error.data.dispatch_stage = if stage == ReceiptStage::Unknown {
            DispatchStage::Unknown
        } else {
            DispatchStage::Completed
        };
        error.data.operation_id = Some(receipt.operation_id.clone());
        error.data.device_id = Some(receipt.device_id.clone());
        error.data.session_id = Some(receipt.session_id.clone());
        error.data.generation = Some(receipt.generation);
        error.data.receipt = Some(receipt.clone());
        operation.finish_failure(receipt, error.clone());
    }
    error
}

#[allow(clippy::result_large_err)]
fn mutation_receipt(
    operation: OperationLease,
    method: &str,
) -> Result<(OperationLease, eip::OperationReceipt), EIPError> {
    mutation_receipt_at(operation, method, ReceiptStage::Completed)
}

#[allow(clippy::result_large_err)]
fn mutation_receipt_at(
    mut operation: OperationLease,
    method: &str,
    stage: ReceiptStage,
) -> Result<(OperationLease, eip::OperationReceipt), EIPError> {
    let receipt = operation
        .receipt(method, stage, Some(ReceiptOutcome::Succeeded))
        .map_err(map_ledger_error)?;
    let mut unknown_receipt = receipt.clone();
    unknown_receipt.stage = ReceiptStage::Unknown;
    unknown_receipt.outcome = Some(ReceiptOutcome::Unknown);
    unknown_receipt.observed_at = chrono::Utc::now();
    let mut failure = protocol_error(
        ErrorType::UnknownOutcome,
        "mutation completion evidence became unavailable before terminal recording",
    );
    failure.data.retry_hint = RetryHint::ReconcileFirst;
    failure.data.dispatch_stage = DispatchStage::Unknown;
    failure.data.operation_id = Some(unknown_receipt.operation_id.clone());
    failure.data.device_id = Some(unknown_receipt.device_id.clone());
    failure.data.session_id = Some(unknown_receipt.session_id.clone());
    failure.data.generation = Some(unknown_receipt.generation);
    failure.data.receipt = Some(unknown_receipt.clone());
    operation.preserve_failure_on_drop(unknown_receipt, failure);
    Ok((operation, receipt))
}

#[allow(clippy::result_large_err)]
fn effective_deadline(
    requested_ms: Option<u64>,
    hard_duration: Duration,
) -> Result<Instant, EIPError> {
    let now = Instant::now();
    let requested = requested_ms
        .map(Duration::from_millis)
        .unwrap_or(hard_duration);
    if requested.is_zero() {
        return Err(protocol_error(
            ErrorType::Timeout,
            "operation timeout has expired",
        ));
    }
    Ok(now + requested.min(hard_duration))
}

async fn inspect_port(target: &eip::PortTarget, deadline: Instant) -> eip::PortObservation {
    let ip = match target.address {
        eip::PortAddress::Loopback => IpAddr::V4(Ipv4Addr::LOCALHOST),
        eip::PortAddress::Any => IpAddr::V4(Ipv4Addr::UNSPECIFIED),
    };
    let timeout = deadline
        .saturating_duration_since(Instant::now())
        .min(Duration::from_millis(100));
    let status = if timeout.is_zero() {
        eip::PortStatus::Unknown
    } else {
        match tokio::time::timeout(
            timeout,
            tokio::net::TcpStream::connect(SocketAddr::new(ip, target.port as u16)),
        )
        .await
        {
            Ok(Ok(_)) => eip::PortStatus::Listening,
            Ok(Err(error)) if error.kind() == std::io::ErrorKind::ConnectionRefused => {
                eip::PortStatus::NotListening
            }
            Ok(Err(_)) | Err(_) => eip::PortStatus::Unknown,
        }
    };
    eip::PortObservation {
        target: target.clone(),
        status,
        managed_process: None,
        observed_at: chrono::Utc::now(),
    }
}

fn map_resource_error(error: ResourceError) -> EIPError {
    let error = match error {
        ResourceError::PartialRemove {
            removed_entries,
            cause,
        } => {
            let mut mapped = map_resource_error(*cause);
            mapped.data.emitted_items = Some(removed_entries);
            mapped.data.retry_hint = RetryHint::ReconcileFirst;
            return mapped;
        }
        error => error,
    };
    let (error_type, message, retry_hint) = match error {
        ResourceError::InvalidInput { field, reason } => {
            let mut mapped =
                protocol_error(ErrorType::InvalidParams, "invalid resource operation input");
            mapped.data.field = Some(field.to_owned());
            mapped.data.safe_detail = Some(reason.to_owned());
            return mapped;
        }
        ResourceError::Invalid => (
            ErrorType::InvalidParams,
            "invalid resource operation parameters",
            RetryHint::Never,
        ),
        ResourceError::Denied => (
            ErrorType::Denied,
            "resource access is denied",
            RetryHint::Never,
        ),
        ResourceError::NotFound => (
            ErrorType::NotFoundOrDenied,
            "resource was not found or is not visible",
            RetryHint::Never,
        ),
        ResourceError::Conflict => (
            ErrorType::Conflict,
            "resource state conflicts with the requested operation",
            RetryHint::ReconcileFirst,
        ),
        ResourceError::Unsupported => (
            ErrorType::Unsupported,
            "resource operation is unsupported",
            RetryHint::Never,
        ),
        ResourceError::Limit => (
            ErrorType::QuotaExceeded,
            "resource operation exceeded a finite limit",
            RetryHint::Never,
        ),
        ResourceError::OutputLimit => (
            ErrorType::OutputLimitExceeded,
            "resource result exceeds the daemon response limit",
            RetryHint::Never,
        ),
        ResourceError::Cancelled => (
            ErrorType::Cancelled,
            "resource operation was cancelled",
            RetryHint::Never,
        ),
        ResourceError::Timeout => (
            ErrorType::Timeout,
            "resource operation exceeded its deadline",
            RetryHint::Never,
        ),
        ResourceError::UnknownOutcome => (
            ErrorType::UnknownOutcome,
            "resource commit completed with uncertain durability evidence",
            RetryHint::ReconcileFirst,
        ),
        ResourceError::Io => (
            ErrorType::ProviderUnavailable,
            "resource provider I/O failed",
            RetryHint::SameRequest,
        ),
        ResourceError::Internal => (
            ErrorType::InternalError,
            "resource operation failed internally",
            RetryHint::Never,
        ),
        ResourceError::PartialRemove { .. } => unreachable!("handled before error mapping"),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    mapped
}

fn map_process_error(error: ProcessError) -> EIPError {
    let (error_type, message, retry_hint) = match error {
        ProcessError::Invalid => (
            ErrorType::InvalidParams,
            "command or process parameters are invalid",
            RetryHint::Never,
        ),
        ProcessError::Unsupported => (
            ErrorType::Unsupported,
            "requested command behavior is unsupported by the active backend",
            RetryHint::Never,
        ),
        ProcessError::Denied => (
            ErrorType::Denied,
            "command execution was denied by configured policy",
            RetryHint::AfterRefresh,
        ),
        ProcessError::NotFound => (
            ErrorType::NotFoundOrDenied,
            "process handle was not found",
            RetryHint::Never,
        ),
        ProcessError::Busy => (
            ErrorType::Busy,
            "process capacity is unavailable",
            RetryHint::AfterCapacity,
        ),
        ProcessError::Conflict => (
            ErrorType::Conflict,
            "process state conflicts with the requested operation",
            RetryHint::Never,
        ),
        ProcessError::OutputLimit => (
            ErrorType::OutputLimitExceeded,
            "process input or output limit was exceeded",
            RetryHint::Never,
        ),
        ProcessError::Timeout => (
            ErrorType::Timeout,
            "process operation deadline expired",
            RetryHint::Never,
        ),
        ProcessError::Cancelled | ProcessError::PreDispatchCancelled => (
            ErrorType::Cancelled,
            "process operation was cancelled",
            RetryHint::Never,
        ),
        ProcessError::PreDispatchTimeout => (
            ErrorType::Timeout,
            "process operation deadline expired before dispatch",
            RetryHint::Never,
        ),
        ProcessError::UnknownOutcome => (
            ErrorType::UnknownOutcome,
            "process control outcome could not be confirmed",
            RetryHint::ReconcileFirst,
        ),
        ProcessError::StartFailed => (
            ErrorType::CommandStartFailed,
            "requested executable could not be started",
            RetryHint::Never,
        ),
        ProcessError::CleanupFailed => (
            ErrorType::CleanupFailed,
            "command tree cleanup failed",
            RetryHint::ReconcileFirst,
        ),
        ProcessError::Internal => (
            ErrorType::InternalError,
            "process manager failed",
            RetryHint::Never,
        ),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    mapped
}

fn map_started_process_error(
    error: ProcessError,
    started: &crate::process::StartedProcess,
    execution: &ExecutionManager,
) -> EIPError {
    started.info(execution).map_or_else(
        |_| map_process_error(error),
        |process| map_process_error_with_evidence(error, &process),
    )
}

fn map_process_error_with_evidence(error: ProcessError, process: &eip::ProcessInfo) -> EIPError {
    let mut mapped = map_process_error_with_output_evidence(error, process);
    mapped.data.process = Some(process.clone());
    mapped
}

fn map_process_error_with_output_evidence(
    error: ProcessError,
    process: &eip::ProcessInfo,
) -> EIPError {
    let mut mapped = map_process_error(error);
    mapped.data.process_status = Some(process.status.clone());
    mapped.data.produced_bytes = Some(
        process
            .output
            .stdout
            .produced_bytes
            .saturating_add(process.output.stderr.produced_bytes),
    );
    mapped.data.output = Some(process.output.clone());
    mapped
}

fn map_retention_error(error: RetentionError) -> EIPError {
    let (error_type, message, retry_hint) = match error {
        RetentionError::InvalidSelector => (
            ErrorType::InvalidHandle,
            "output reference was not found",
            RetryHint::Never,
        ),
        RetentionError::InvalidOffset => (
            ErrorType::InvalidParams,
            "output start_offset exceeds retained bytes",
            RetryHint::Never,
        ),
        RetentionError::Busy => (
            ErrorType::Busy,
            "output spool capacity is exhausted",
            RetryHint::AfterCapacity,
        ),
        RetentionError::Conflict => (
            ErrorType::Conflict,
            "output is active or remains attached to a process",
            RetryHint::AfterRefresh,
        ),
        RetentionError::CleanupFailed => (
            ErrorType::CleanupFailed,
            "output spool deletion could not be proven",
            RetryHint::SameRequest,
        ),
        RetentionError::Internal => (
            ErrorType::InternalError,
            "output spool operation failed internally",
            RetryHint::Never,
        ),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    mapped
}

fn map_dispatch_error(error: DispatchError, _method: &str) -> EIPError {
    match error {
        DispatchError::MethodNotFound => {
            protocol_error(ErrorType::MethodNotFound, "method not found")
        }
        DispatchError::InvalidParams(_) => {
            protocol_error(ErrorType::InvalidParams, "invalid method params")
        }
        DispatchError::InvalidResult(_) | DispatchError::Encode(_) => {
            protocol_error(ErrorType::InternalError, "method result encoding failed")
        }
        DispatchError::Method { error, .. } => error,
    }
}

fn map_ledger_error(error: LedgerError) -> EIPError {
    let (error_type, message, retry_hint) = match error {
        LedgerError::Collision => (
            ErrorType::Conflict,
            "operation_id is already active or retained",
            RetryHint::Never,
        ),
        LedgerError::DeadlineExpired => (
            ErrorType::Timeout,
            "operation deadline has expired",
            RetryHint::Never,
        ),
        LedgerError::InProgress => (
            ErrorType::OperationInProgress,
            "matching operation is still in progress",
            RetryHint::ReconcileFirst,
        ),
        LedgerError::TerminalFailure => (
            ErrorType::Conflict,
            "matching operation completed without a replayable success result",
            RetryHint::ReconcileFirst,
        ),
        LedgerError::Capacity => (
            ErrorType::Busy,
            "operation record capacity is currently exhausted",
            RetryHint::AfterCapacity,
        ),
        LedgerError::Encoding => (
            ErrorType::InternalError,
            "operation evidence encoding failed",
            RetryHint::Never,
        ),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    mapped
}

fn map_transfer_error(error: TransferError) -> EIPError {
    let (error_type, message, retry_hint) = match error {
        TransferError::InvalidHandle | TransferError::WrongKind | TransferError::WrongState => (
            ErrorType::InvalidHandle,
            "file transfer handle or state is invalid",
            RetryHint::Never,
        ),
        TransferError::Conflict => (
            ErrorType::Conflict,
            "file transfer precondition changed",
            RetryHint::AfterRefresh,
        ),
        TransferError::IntegrityMismatch => (
            ErrorType::IntegrityMismatch,
            "file transfer count or digest did not match",
            RetryHint::Never,
        ),
        TransferError::Expired => (
            ErrorType::Timeout,
            "file transfer expired",
            RetryHint::Never,
        ),
        TransferError::Busy => (
            ErrorType::Busy,
            "file transfer capacity is exhausted",
            RetryHint::AfterCapacity,
        ),
        TransferError::Quota | TransferError::Limit => (
            ErrorType::QuotaExceeded,
            "file transfer quota is exhausted",
            RetryHint::AfterCapacity,
        ),
        TransferError::Unsupported => (
            ErrorType::Unsupported,
            "file transfer operation is unsupported",
            RetryHint::Never,
        ),
        TransferError::Denied => (
            ErrorType::Denied,
            "file transfer is denied",
            RetryHint::Never,
        ),
        TransferError::NotFound => (
            ErrorType::NotFoundOrDenied,
            "file resource was not found or is denied",
            RetryHint::Never,
        ),
        TransferError::Source => (
            ErrorType::ProviderUnavailable,
            "native file source became unavailable",
            RetryHint::AfterRefresh,
        ),
        TransferError::Protocol => (
            ErrorType::InvalidParams,
            "file transfer request is invalid",
            RetryHint::Never,
        ),
        TransferError::Cancelled => (
            ErrorType::Cancelled,
            "file transfer operation was cancelled",
            RetryHint::Never,
        ),
        TransferError::Timeout => (
            ErrorType::Timeout,
            "file transfer operation exceeded its deadline",
            RetryHint::Never,
        ),
        TransferError::UnknownOutcome => (
            ErrorType::UnknownOutcome,
            "file commit completed with uncertain durability evidence",
            RetryHint::ReconcileFirst,
        ),
        TransferError::CleanupFailed => (
            ErrorType::CleanupFailed,
            "staged file cleanup could not be proven",
            RetryHint::ReconcileFirst,
        ),
        TransferError::SessionClosed => (
            ErrorType::NotInitialized,
            "file transfer session is closed",
            RetryHint::Never,
        ),
        TransferError::Internal => (
            ErrorType::InternalError,
            "file transfer internal failure",
            RetryHint::Never,
        ),
    };
    let mut mapped = protocol_error(error_type, message);
    mapped.data.retry_hint = retry_hint;
    mapped
}

fn protocol_error(error_type: ErrorType, message: impl Into<String>) -> EIPError {
    EIPError {
        code: error_type.code(),
        message: message.into(),
        data: EIPErrorData {
            error_type,
            retry_hint: RetryHint::Never,
            dispatch_stage: DispatchStage::PreDispatch,
            operation_id: None,
            device_id: None,
            session_id: None,
            generation: None,
            field: None,
            handle_kind: None,
            produced_bytes: None,
            emitted_items: None,
            dropped_items: None,
            process_status: None,
            receipt: None,
            safe_detail: None,
            process: None,
            output: None,
        },
    }
}

fn error_type_message(error_type: ErrorType) -> &'static str {
    match error_type {
        ErrorType::ParseError => "invalid JSON payload",
        ErrorType::InvalidRequest => "invalid JSON-RPC request",
        _ => "protocol error",
    }
}

fn valid_request_id(id: &JsonRpcId) -> bool {
    match id {
        JsonRpcId::String(value) => !value.is_empty() && value.len() <= MAX_STRING_REQUEST_ID_BYTES,
        JsonRpcId::Integer(_) => true,
    }
}

fn recover_request_id(payload: &str) -> Option<JsonRpcId> {
    let value = serde_json::from_str::<serde_json::Value>(payload).ok()?;
    let id = value.as_object()?.get("id")?.clone();
    let id = serde_json::from_value::<JsonRpcId>(id).ok()?;
    valid_request_id(&id).then_some(id)
}

fn fresh_generation() -> Result<u64, DaemonInitError> {
    loop {
        let mut bytes = [0_u8; 8];
        getrandom::fill(&mut bytes)
            .map_err(|_| DaemonInitError::new("secure random generation failed"))?;
        let generation = u64::from_ne_bytes(bytes);
        if generation != 0 {
            return Ok(generation);
        }
    }
}

#[derive(Debug)]
pub(crate) struct DaemonInitError {
    message: String,
}

impl DaemonInitError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for DaemonInitError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for DaemonInitError {}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        path::PathBuf,
        sync::{
            Arc, Condvar, Mutex, PoisonError,
            atomic::{AtomicBool, Ordering},
        },
        time::Duration,
    };

    use serde_json::{Value, json};

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use crate::eip::FileWriterOpenParams;
    use crate::{
        config::Config,
        eip::{
            EIPCallContext, EIPPath, EipSessionHandler, EnvironmentDescribeParams, FileWriteMode,
            FileWriteTextParams, OperationCancelParams, OperationCancelStatus,
        },
        operation::{BeginOutcome, random_selector},
    };

    use super::{Daemon, fresh_generation, mutation_receipt};

    struct TempTree(PathBuf);

    impl TempTree {
        fn new() -> Self {
            let path = std::env::temp_dir().join(
                random_selector("a13n-envd-daemon-test").expect("random temporary directory"),
            );
            fs::create_dir(&path).expect("creates temporary directory");
            Self(path)
        }

        fn child(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for TempTree {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    struct Fixture {
        device: Daemon,
        carrier: Arc<super::Carrier>,
        opened: std::sync::OnceLock<Arc<super::Session>>,
        _outbound: Mutex<tokio::sync::mpsc::Receiver<crate::eip::DataFrame>>,
    }

    impl std::ops::Deref for Fixture {
        type Target = super::Session;
        fn deref(&self) -> &Self::Target {
            self.opened.get().expect("explicitly opened Session")
        }
    }

    impl Fixture {
        fn new(config: &Config, generation: u64) -> Self {
            let device = Daemon::with_generation(config, generation).unwrap();
            let (sender, receiver) = tokio::sync::mpsc::channel(16);
            let carrier = device.carrier(sender);
            Self {
                device,
                carrier,
                opened: std::sync::OnceLock::new(),
                _outbound: Mutex::new(receiver),
            }
        }

        fn open_params(&self, required_methods: Value) -> Value {
            json!({"expected_device_id": self.device.descriptor.device_id,
                "expected_generation": self.device.descriptor.generation,
                "protocol_version": "0.1",
                "working_directory": self.device.descriptor.default_working_directory,
                "required_methods": required_methods})
        }

        fn scoped_payload(&self, payload: &str) -> String {
            let mut value: Value = serde_json::from_str(payload).unwrap();
            let method = value["method"].as_str().unwrap();
            if !crate::eip::METHODS
                .iter()
                .any(|m| m.name == method && m.device_scoped)
                && let Some(session) = self.opened.get()
            {
                value["eip_session"] = json!(session.descriptor.session_id);
            }
            value.to_string()
        }

        async fn handle_payload_for_carrier(&self, payload: &str) -> super::CarrierResponse {
            self.device
                .handle_payload_for_carrier(&self.carrier, &self.scoped_payload(payload))
                .await
        }

        async fn handle_payload(&self, payload: &str) -> Vec<u8> {
            let (bytes, handoff) = self.handle_payload_for_carrier(payload).await.into_parts();
            if let Some(handoff) = handoff {
                handoff.complete();
            }
            bytes
        }

        async fn call(&self, method: &str, params: Value) -> Value {
            serde_json::from_slice(
                &self
                    .handle_payload(&request(json!(1), method, params))
                    .await,
            )
            .unwrap()
        }

        fn track_pending_payload(
            &self,
            payload: &str,
        ) -> Option<crate::operation::PendingOperationGuard> {
            self.device
                .track_pending_payload(&self.scoped_payload(payload))
        }
    }

    fn request(id: Value, method: &str, params: Value) -> String {
        json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}).to_string()
    }

    fn initialize_params() -> Value {
        json!({
            "supported_protocol_versions": ["0.1"],
            "client": {"name": "test", "version": "1"},
            "expected_device_id": "env-test"
        })
    }

    async fn initialize_protocol(daemon: &Fixture) -> Value {
        let bytes = daemon
            .handle_payload(&request(json!(1), "initialize", initialize_params()))
            .await;
        let initialized: Value = serde_json::from_slice(&bytes).expect("response is JSON");
        let opened = daemon
            .call("session.open", daemon.open_params(json!([])))
            .await;
        let id = opened["result"]["descriptor"]["session_id"]
            .as_str()
            .expect("opened Session");
        assert!(daemon.opened.set(daemon.device.lookup(id).unwrap()).is_ok());
        initialized
    }

    async fn readiness(daemon: &Fixture, request_id: u64, operation_id: &str) -> Value {
        let bytes = daemon
            .handle_payload(&request(
                json!(request_id),
                "environment.readiness",
                json!({
                    "context": {
                        "operation_id": operation_id,
                        "timeout_ms": 1_000
                    }
                }),
            ))
            .await;
        serde_json::from_slice(&bytes).expect("response is JSON")
    }

    async fn initialize(daemon: &Fixture) -> Value {
        let initialized = initialize_protocol(daemon).await;
        let ready = readiness(daemon, 2, "readiness-initial").await;
        assert_eq!(ready["result"]["ready"], true);
        initialized
    }

    #[tokio::test]
    async fn device_metadata_and_session_descriptor_are_distinct() {
        let mut config = Config::for_test("env-test");
        config.display_name = Some("Build Linux".into());
        config.description = Some("Build artifacts".into());
        let fixture = Fixture::new(&config, 7);
        let initialized = initialize(&fixture).await;
        assert_eq!(initialized["result"]["protocol_version"], "0.1");
        let device = fixture.call("device.describe", json!({})).await;
        assert_eq!(
            device["result"]["descriptor"],
            initialized["result"]["descriptor"]
        );
        assert_eq!(
            device["result"]["descriptor"]["display_name"],
            "Build Linux"
        );
        assert_eq!(
            device["result"]["descriptor"]["description"],
            "Build artifacts"
        );
        let session = fixture
            .call(
                "environment.describe",
                json!({"context":{"operation_id":"describe"}}),
            )
            .await;
        assert_eq!(session["result"]["descriptor"]["device_id"], "env-test");
        assert!(session["result"]["descriptor"]["session_id"].is_string());
        assert!(device["result"]["descriptor"].get("session_id").is_none());
    }

    #[test]
    fn generations_are_nonzero_and_fresh() {
        let first = fresh_generation().expect("secure randomness is available");
        let second = fresh_generation().expect("secure randomness is available");
        assert_ne!(first, 0);
        assert_ne!(second, 0);
        assert_ne!(first, second);
    }

    #[tokio::test]
    async fn carrier_loss_allows_attach_and_independent_open_in_the_same_generation() {
        let config = Config::for_test("env-test");
        let fixture = Fixture::new(&config, 7);
        initialize(&fixture).await;
        let original = fixture.descriptor.clone();
        fixture.device.detach(&fixture.carrier).await;
        let (sender, _receiver) = tokio::sync::mpsc::channel(16);
        let carrier = fixture.device.carrier(sender);
        let response = fixture
            .device
            .handle_payload_for_carrier(
                &carrier,
                &request(json!(1), "initialize", initialize_params()),
            )
            .await;
        assert!(
            serde_json::from_slice::<Value>(&response.payload)
                .unwrap()
                .get("result")
                .is_some()
        );
        let mut attach: Value =
            serde_json::from_str(&request(json!(2), "session.attach", json!({}))).unwrap();
        attach["eip_session"] = json!(original.session_id);
        let response = fixture
            .device
            .handle_payload_for_carrier(&carrier, &attach.to_string())
            .await;
        let attached: Value = serde_json::from_slice(&response.payload).unwrap();
        assert_eq!(
            attached["result"]["descriptor"],
            serde_json::to_value(original).unwrap()
        );
        let response = fixture
            .device
            .handle_payload_for_carrier(
                &carrier,
                &request(json!(3), "session.open", fixture.open_params(json!([]))),
            )
            .await;
        let opened: Value = serde_json::from_slice(&response.payload).unwrap();
        assert_eq!(opened["result"]["descriptor"]["generation"], 7);
        assert_ne!(
            opened["result"]["descriptor"]["session_id"],
            attached["result"]["descriptor"]["session_id"]
        );
    }

    #[test]
    fn dispatch_fits_a_windows_sized_thread_stack() {
        std::thread::Builder::new()
            .name("eip-small-stack".to_owned())
            .stack_size(1024 * 1024)
            .spawn(|| {
                let runtime = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                    .expect("runtime builds");
                runtime.block_on(async {
                    let config = Config::for_test("env-test");
                    let daemon = Fixture::new(&config, 7);
                    let initialized = initialize(&daemon).await;
                    assert_eq!(initialized["result"]["descriptor"]["generation"], 7);
                });
            })
            .expect("small-stack thread starts")
            .join()
            .expect("dispatch completes on the bounded stack");
    }

    #[tokio::test]
    async fn initialize_creates_no_session_and_session_close_preserves_carrier() {
        let config = Config::for_test("env-test");
        let fixture = Fixture::new(&config, 7);
        fixture.call("initialize", initialize_params()).await;
        assert!(fixture.device.sessions().is_empty());
        initialize(&fixture).await;
        let sibling = fixture
            .call("session.open", fixture.open_params(json!([])))
            .await;
        let sibling_id = sibling["result"]["descriptor"]["session_id"]
            .as_str()
            .unwrap();
        let closed = fixture.call("session.close", json!({})).await;
        assert_eq!(closed["result"]["closed"], true);
        assert!(*fixture.subscribe_closed().borrow());
        assert!(!fixture.carrier.closed.load(Ordering::Acquire));
        assert!(fixture.device.lookup(sibling_id).is_ok());
        assert!(
            fixture
                .call("device.describe", json!({}))
                .await
                .get("result")
                .is_some()
        );
    }

    #[tokio::test]
    async fn readiness_gates_application_dispatch_and_reports_exact_generation() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 7);

        let initialized = initialize_protocol(&daemon).await;
        assert_eq!(initialized["result"]["descriptor"]["generation"], 7);
        let rejected: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "environment.describe",
                    json!({"context": {"operation_id": "describe-before-ready"}}),
                ))
                .await,
        )
        .expect("response is JSON");
        assert_eq!(rejected["error"]["data"]["error_type"], "not_initialized");

        let ready = readiness(&daemon, 3, "readiness-first").await;
        assert_eq!(ready["result"]["ready"], true);
        assert_eq!(ready["result"]["device_id"], "env-test");
        assert_eq!(ready["result"]["generation"], 7);

        let later = readiness(&daemon, 4, "readiness-later").await;
        assert_eq!(later["result"]["ready"], true);
    }

    #[tokio::test]
    async fn draining_readiness_returns_false_and_still_allows_session_close() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 7);
        let _ = initialize_protocol(&daemon).await;
        daemon.operations.begin_drain();

        let readiness = readiness(&daemon, 2, "readiness-draining").await;
        assert_eq!(readiness["result"]["ready"], false);
        let closed: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(json!(3), "session.close", json!({})))
                .await,
        )
        .expect("response is JSON");
        assert_eq!(closed["result"]["closed"], true);
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn session_teardown_serializes_transfer_and_mutation_admission() {
        let tree = TempTree::new();
        let native = tree.child("native");
        fs::create_dir(&native).expect("native root");
        let mut config = Config::for_test("env-test");
        config.default_working_directory = crate::device_path::from_native(&native).unwrap();
        let daemon = Arc::new(Fixture::new(&config, 71));
        let _ = initialize(&daemon).await;
        EipSessionHandler::file_open_writer(
            daemon.opened.get().unwrap().as_ref(),
            FileWriterOpenParams {
                context: EIPCallContext {
                    operation_id: "open-before-close".to_owned(),
                    timeout_ms: None,
                },
                path: EIPPath {
                    path: crate::device_path::from_native(&native.join("candidate.bin")).unwrap(),
                },
                mode: FileWriteMode::Create,
                executable: None,
                transfer_timeout_ms: None,
            },
        )
        .await
        .expect("opens session-owned writer");
        let in_flight = daemon
            .session
            .admit_work()
            .expect("admits work before teardown");
        let closing_daemon = Arc::clone(&daemon);
        let closing =
            tokio::spawn(async move { closing_daemon.close(Duration::from_secs(1)).await });
        tokio::task::yield_now().await;
        assert!(!closing.is_finished());
        drop(in_flight);
        assert!(closing.await.expect("close waits for admitted work"));
        assert!(daemon.transfers.close_session().await);
        assert_eq!(
            fs::read_dir(&native)
                .expect("native directory")
                .filter_map(Result::ok)
                .filter(|entry| entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".eip-stage-"))
                .count(),
            0
        );

        let second = Arc::new(Fixture::new(&config, 72));
        let _ = initialize(&second).await;
        assert!(second.close(Duration::from_secs(1)).await);
        let rejected = EipSessionHandler::file_write_text(
            second.opened.get().unwrap().as_ref(),
            FileWriteTextParams {
                context: EIPCallContext {
                    operation_id: "write-after-close".to_owned(),
                    timeout_ms: None,
                },
                path: EIPPath {
                    path: crate::device_path::from_native(&native.join("too-late.txt")).unwrap(),
                },
                mode: FileWriteMode::Create,
                text: "too late".to_owned(),
                executable: None,
            },
        )
        .await
        .expect_err("post-close mutation is rejected before operation admission");
        assert_eq!(
            rejected.data.error_type,
            crate::eip::ErrorType::NotInitialized
        );
        assert!(!native.join("too-late.txt").exists());
    }

    #[tokio::test]
    async fn pending_operation_wait_observes_admission_deadline_and_close() {
        let config = Config::for_test("env-test");
        let daemon = Arc::new(Fixture::new(&config, 74));
        let _ = initialize(&daemon).await;

        let admission_guard = daemon
            .track_pending_payload(&request(
                json!(70),
                "environment.describe",
                json!({"context": {"operation_id": "pending-target"}}),
            ))
            .expect("transport-read payload registers a pending target");
        let cancelling = {
            let daemon = Arc::clone(&daemon);
            tokio::spawn(async move {
                daemon
                    .operation_cancel(OperationCancelParams {
                        context: EIPCallContext {
                            operation_id: "pending-cancel".to_owned(),
                            timeout_ms: Some(1_000),
                        },
                        target_operation_id: "pending-target".to_owned(),
                    })
                    .await
            })
        };
        daemon.operations.wait_until_admission_wait().await;
        let target_params = EnvironmentDescribeParams {
            context: EIPCallContext {
                operation_id: "pending-target".to_owned(),
                timeout_ms: None,
            },
        };
        let target = daemon
            .begin_record(
                "environment.describe",
                &target_params.context,
                &target_params,
            )
            .expect("target admission succeeds");
        let BeginOutcome::New(target) = target else {
            panic!("new target operation expected");
        };
        let cancelled = cancelling
            .await
            .expect("cancel task joins")
            .expect("cancel succeeds after admission");
        assert_eq!(
            cancelled.status,
            OperationCancelStatus::CancellationRequested
        );
        daemon.operations.complete_active_for_test("pending-cancel");
        drop(target);
        drop(admission_guard);

        let timeout_guard = daemon
            .track_pending_payload(&request(
                json!(71),
                "environment.describe",
                json!({"context": {"operation_id": "timeout-target"}}),
            ))
            .expect("transport-read payload registers a timeout target");
        let timing_out = {
            let daemon = Arc::clone(&daemon);
            tokio::spawn(async move {
                daemon
                    .operation_cancel(OperationCancelParams {
                        context: EIPCallContext {
                            operation_id: "timeout-cancel".to_owned(),
                            timeout_ms: Some(100),
                        },
                        target_operation_id: "timeout-target".to_owned(),
                    })
                    .await
            })
        };
        daemon.operations.wait_until_admission_wait().await;
        let timeout = timing_out
            .await
            .expect("timeout wait joins")
            .expect_err("pending admission wait respects the cancel deadline");
        assert_eq!(timeout.data.error_type, crate::eip::ErrorType::Timeout);
        daemon.operations.complete_active_for_test("timeout-cancel");
        drop(timeout_guard);

        let close_guard = daemon
            .track_pending_payload(&request(
                json!(72),
                "environment.describe",
                json!({"context": {"operation_id": "close-target"}}),
            ))
            .expect("transport-read payload registers a close target");
        let waiting = {
            let daemon = Arc::clone(&daemon);
            tokio::spawn(async move {
                daemon
                    .operation_cancel(OperationCancelParams {
                        context: EIPCallContext {
                            operation_id: "close-cancel".to_owned(),
                            timeout_ms: Some(1_000),
                        },
                        target_operation_id: "close-target".to_owned(),
                    })
                    .await
            })
        };
        daemon.operations.wait_until_admission_wait().await;
        daemon.closed.send_replace(true);
        let closed = waiting
            .await
            .expect("close wait joins")
            .expect_err("session close terminates pending admission wait");
        assert_eq!(
            closed.data.error_type,
            crate::eip::ErrorType::NotInitialized
        );
        daemon.operations.complete_active_for_test("close-cancel");
        drop(close_guard);
    }

    #[tokio::test]
    async fn transport_teardown_bounds_stalled_admission_handoffs() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 73);
        let _ = initialize(&daemon).await;
        let stalled = daemon
            .session
            .admit_work()
            .expect("admits a handoff before close");
        let closed = tokio::time::timeout(
            Duration::from_secs(1),
            daemon.close(Duration::from_millis(20)),
        )
        .await
        .expect("transport teardown stays bounded");
        assert!(!closed);
        drop(stalled);
    }

    #[tokio::test]
    async fn first_non_initialize_request_fails_before_any_session_exists() {
        let fixture = Fixture::new(&Config::for_test("env-test"), 8);
        let response = fixture.call("device.describe", json!({})).await;
        assert_eq!(response["error"]["code"], -32001);
        assert!(!fixture.carrier.initialized());
        assert!(fixture.device.sessions().is_empty());
    }

    #[tokio::test]
    async fn pressure_reclaims_expired_owner_before_healthy_history() {
        let tree = TempTree::new();
        let mut config = Config::for_test("env-test");
        config.runtime =
            Some(crate::runtime::RuntimeState::prepare(&tree.child("runtime")).unwrap());
        config.limits.max_output_preview_bytes = 4;
        config.limits.max_output_bytes_per_stream = 4;
        config.limits.max_spool_bytes = 16;
        config.limits.max_device_spool_bytes = 16;
        let fixture = Fixture::new(&config, 8);
        initialize(&fixture).await;
        let opened = fixture
            .call("session.open", fixture.open_params(json!([])))
            .await;
        let abandoned = fixture
            .device
            .lookup(
                opened["result"]["descriptor"]["session_id"]
                    .as_str()
                    .unwrap(),
            )
            .unwrap();
        let (a, b) = abandoned.retention.create_live_pair().await.unwrap();
        a.append(b"1234").await;
        b.append(b"5678").await;
        a.complete().await;
        b.complete().await;
        let (kept, empty) = fixture.retention.create_live_pair().await.unwrap();
        kept.append(b"ok").await;
        kept.complete().await;
        empty.complete().await;
        kept.detach();
        empty.detach();
        assert!(fixture.under_pressure());
        abandoned.session.state().last_activity =
            super::Instant::now() - config.session_idle_timeout;
        let result = fixture
            .call("session.open", fixture.open_params(json!([])))
            .await;
        assert!(result.get("result").is_some(), "{result}");
        assert!(
            fixture
                .device
                .lookup(&abandoned.descriptor.session_id)
                .is_err()
        );
        assert!(
            fixture
                .retention
                .read(
                    &crate::eip::OutputReadParams {
                        context: EIPCallContext {
                            operation_id: "read-kept".into(),
                            timeout_ms: None
                        },
                        reference: kept.reference().clone(),
                        start_offset: 0,
                        wait_ms: 0,
                    },
                    super::Instant::now() + Duration::from_secs(1)
                )
                .await
                .is_ok()
        );
        assert_eq!(fixture.device.retention_quota.usage().0, 2);
        assert!(fixture.device.drain(Duration::from_secs(1)).await);
    }

    #[tokio::test]
    async fn lost_open_expires_and_capacity_admission_reclaims_only_expired_sessions() {
        let mut config = Config::for_test("env-test");
        config.limits.max_sessions = 1;
        let fixture = Fixture::new(&config, 8);
        fixture.call("initialize", initialize_params()).await;
        // No requester retains this response or renews the new Session.
        drop(
            fixture
                .call("session.open", fixture.open_params(json!([])))
                .await,
        );
        let abandoned = fixture.device.sessions().values().next().unwrap().clone();
        let busy = fixture
            .call("session.open", fixture.open_params(json!([])))
            .await;
        assert_eq!(busy["error"]["data"]["error_type"], "busy");
        abandoned.session.state().last_activity =
            super::Instant::now() - config.session_idle_timeout;
        let replacement = fixture
            .call("session.open", fixture.open_params(json!([])))
            .await;
        let replacement_id = replacement["result"]["descriptor"]["session_id"]
            .as_str()
            .unwrap();
        assert_ne!(replacement_id, abandoned.descriptor.session_id);
        assert!(
            fixture
                .device
                .lookup(&abandoned.descriptor.session_id)
                .is_err()
        );
        assert_eq!(fixture.device.sessions().len(), 1);
        assert!(*abandoned.subscribe_closed().borrow());
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn response_handoff_prevents_evidence_collection_until_delivery() {
        let tree = TempTree::new();
        let config = Config::for_test("env-test");
        let fixture = Fixture::new(&config, 8);
        initialize(&fixture).await;
        let payload = request(
            json!(3),
            "file.write_text",
            json!({
                "context":{"operation_id":"held-response"},
                "path":{"path":crate::device_path::from_native(&tree.child("held.txt")).unwrap()},
                "mode":"create","text":"held"
            }),
        );
        let (bytes, handoff) = fixture
            .handle_payload_for_carrier(&payload)
            .await
            .into_parts();
        let response: Value = serde_json::from_slice(&bytes).unwrap();
        assert!(response.get("result").is_some(), "{response}");
        assert!(handoff.is_some());
        fixture.collect_history(true).await;
        assert!(
            fixture
                .operations
                .receipt_by_operation("held-response")
                .is_some()
        );
        handoff.unwrap().complete();
        fixture.collect_history(true).await;
        assert!(
            fixture
                .operations
                .receipt_by_operation("held-response")
                .is_none()
        );
        assert_eq!(fs::read(tree.child("held.txt")).unwrap(), b"held");
    }

    #[tokio::test]
    async fn active_only_operation_ids_are_reusable_after_response_handoff() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 9);
        let _ = initialize(&daemon).await;
        let describe = request(
            json!(2),
            "environment.describe",
            json!({"context": {"operation_id": "describe-once"}}),
        );

        let first: Value = serde_json::from_slice(&daemon.handle_payload(&describe).await)
            .expect("response is JSON");
        assert_eq!(first["result"]["descriptor"]["generation"], 9);
        assert_eq!(daemon.operations.record_stats().1, 0);
        let repeated: Value = serde_json::from_slice(&daemon.handle_payload(&describe).await)
            .expect("response is JSON");
        assert_eq!(repeated["result"], first["result"]);
        assert_eq!(daemon.operations.record_stats().1, 0);
    }

    #[tokio::test]
    async fn explicit_defaults_use_typed_identity_for_active_handoff() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 91);
        let _ = initialize(&daemon).await;
        let payload = request(
            json!(2),
            "environment.describe",
            json!({
                "context": {
                    "operation_id": "describe-explicit-default",
                    "timeout_ms": null
                }
            }),
        );

        for _ in 0..2 {
            let response = daemon.handle_payload_for_carrier(&payload).await;
            let (payload, handoff) = response.into_parts();
            let response: Value = serde_json::from_slice(&payload).expect("response is JSON");
            assert_eq!(response["result"]["descriptor"]["generation"], 91);
            handoff
                .expect("successful active-only response has an exact handoff")
                .complete();
            assert_eq!(daemon.operations.record_stats().1, 0);
        }
    }

    #[tokio::test]
    async fn operation_ledger_memory_is_bounded_by_id_and_record_limits() {
        let mut config = Config::for_test("env-test");
        config.limits.max_concurrent_operations = 1;
        config.limits.max_operation_records = 2;
        let daemon = Fixture::new(&config, 10);
        let _ = initialize(&daemon).await;

        for (request_id, suffix) in [(2, 'a'), (3, 'b')] {
            let operation_id = format!("{}{suffix}", "🧪".repeat(127));
            let response: Value = serde_json::from_slice(
                &daemon
                    .handle_payload(&request(
                        json!(request_id),
                        "environment.describe",
                        json!({"context": {"operation_id": operation_id}}),
                    ))
                    .await,
            )
            .expect("response is JSON");
            assert_eq!(response["result"]["descriptor"]["generation"], 10);
        }

        let too_long: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(4),
                    "environment.describe",
                    json!({"context": {"operation_id": "🧪".repeat(129)}}),
                ))
                .await,
        )
        .expect("response is JSON");
        assert_eq!(too_long["error"]["code"], -32602);

        let (active, records, identifier_bytes) = daemon.operations.record_stats();
        assert_eq!(active, 0);
        assert_eq!(records, 0);
        assert_eq!(identifier_bytes, 0);
    }

    #[tokio::test]
    async fn active_only_ids_do_not_consume_terminal_capacity() {
        let mut config = Config::for_test("env-test");
        config.limits.max_concurrent_operations = 1;
        config.limits.max_operation_records = 1;
        let daemon = Fixture::new(&config, 10);
        let _ = initialize(&daemon).await;
        for (request_id, operation_id) in [(2, "first"), (3, "second"), (4, "first")] {
            let response: Value = serde_json::from_slice(
                &daemon
                    .handle_payload(&request(
                        json!(request_id),
                        "environment.describe",
                        json!({"context": {"operation_id": operation_id}}),
                    ))
                    .await,
            )
            .expect("response is JSON");
            assert_eq!(response["result"]["descriptor"]["generation"], 10);
        }
    }

    #[tokio::test]
    async fn active_only_ids_do_not_wait_for_terminal_ttl() {
        let mut config = Config::for_test("env-test");
        config.limits.operation_record_ttl_ms = 1;
        let daemon = Fixture::new(&config, 11);
        let _ = initialize(&daemon).await;
        let describe = request(
            json!(2),
            "environment.describe",
            json!({"context": {"operation_id": "expires"}}),
        );
        let _ = daemon.handle_payload(&describe).await;
        tokio::time::sleep(Duration::from_millis(5)).await;
        let reused: Value = serde_json::from_slice(&daemon.handle_payload(&describe).await)
            .expect("response is JSON");
        assert_eq!(reused["result"]["descriptor"]["generation"], 11);
    }

    #[tokio::test]
    async fn known_unadvertised_method_is_unsupported() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 9);
        let _ = initialize(&daemon).await;

        let response: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "process.inspect",
                    json!({"context": {"operation_id": "list-1"}}),
                ))
                .await,
        )
        .expect("response is JSON");

        assert_eq!(response["error"]["code"], -32012);
        assert_eq!(response["error"]["data"]["error_type"], "unsupported");
    }

    #[tokio::test]
    async fn unknown_method_is_method_not_found() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 9);
        let _ = initialize(&daemon).await;

        let response: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "vendor.unknown",
                    json!({"context": {"operation_id": "unknown-method"}}),
                ))
                .await,
        )
        .expect("response is JSON");

        assert_eq!(response["error"]["code"], -32601);
        assert_eq!(response["error"]["data"]["error_type"], "method_not_found");
    }

    #[tokio::test]
    async fn operation_namespace_and_session_resource_replay_are_unified() {
        let tree = TempTree::new();
        let native = tree.child("native");
        fs::create_dir(&native).expect("native root");
        fs::write(native.join("replay.txt"), "replay").expect("replay source");
        let mut config = Config::for_test("env-test");
        config.default_working_directory = crate::device_path::from_native(&native).unwrap();
        let daemon = Fixture::new(&config, 12);
        let _ = initialize(&daemon).await;

        let described: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(10),
                    "environment.describe",
                    json!({"context": {"operation_id": "cross-method"}}),
                ))
                .await,
        )
        .expect("describe response");
        assert_eq!(described["result"]["descriptor"]["generation"], 12);
        let collided: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(11),
                    "file.stat",
                    json!({
                        "context": {"operation_id": "cross-method"},
                        "path": {"path": crate::device_path::from_native(&native).unwrap()}
                    }),
                ))
                .await,
        )
        .expect("collision response");
        assert!(collided.get("result").is_some());

        let open_params = |operation_id: &str| {
            json!({
                "context": {"operation_id": operation_id},
                "path": {"path": crate::device_path::from_native(&native.join("replay.txt")).unwrap()}
            })
        };
        let opened: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(12),
                    "file.open_reader",
                    open_params("reader-open-1"),
                ))
                .await,
        )
        .expect("reader open response");
        let reader = opened["result"]["reader"].clone();
        assert_eq!(reader, "reader-c-1");
        let repeated: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(13),
                    "file.open_reader",
                    open_params("reader-open-1"),
                ))
                .await,
        )
        .expect("reader repeat response");
        assert_eq!(repeated["result"]["reader"], "reader-c-2");
        let independently_opened: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(14),
                    "file.open_reader",
                    open_params("reader-open-2"),
                ))
                .await,
        )
        .expect("independent reader response");
        assert_eq!(independently_opened["result"]["reader"], "reader-c-3");
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn resource_and_transfer_candidates_share_one_candidate_quota() {
        let tree = TempTree::new();
        let native = tree.child("native");
        fs::create_dir(&native).expect("native root");
        let mut config = Config::for_test("env-test");
        config.limits.max_staged_file_objects = 1;
        config.limits.max_staged_file_bytes = 1024;
        config.default_working_directory = crate::device_path::from_native(&native).unwrap();
        let daemon = Fixture::new(&config, 73);
        let _ = initialize(&daemon).await;

        let opened: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "file.open_writer",
                    json!({
                        "context": {"operation_id": "quota-writer-open"},
                        "path": {"path": crate::device_path::from_native(&native.join("writer.bin")).unwrap()},
                        "mode": "create"
                    }),
                ))
                .await,
        )
        .expect("writer response");
        let writer = opened["result"]["writer"].clone();
        assert_eq!(writer, "writer-21-1");

        let blocked: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(3),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "quota-write-blocked"},
                        "path": {"path": crate::device_path::from_native(&native.join("inline.txt")).unwrap()},
                        "mode": "create",
                        "text": "blocked"
                    }),
                ))
                .await,
        )
        .expect("blocked write response");
        assert_eq!(blocked["error"]["data"]["error_type"], "quota_exceeded");
        assert!(!native.join("inline.txt").exists());

        let aborted: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(4),
                    "file.abort_writer",
                    json!({
                        "context": {"operation_id": "quota-writer-abort"},
                        "writer": writer
                    }),
                ))
                .await,
        )
        .expect("abort response");
        assert_eq!(aborted["result"]["status"], "aborted");

        let written: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(5),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "quota-write-after-abort"},
                        "path": {"path": crate::device_path::from_native(&native.join("inline.txt")).unwrap()},
                        "mode": "create",
                        "text": "released"
                    }),
                ))
                .await,
        )
        .expect("write response");
        assert_eq!(written["result"]["bytes_written"], 8);
        assert_eq!(
            fs::read_to_string(native.join("inline.txt")).expect("inline target"),
            "released"
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[tokio::test]
    async fn configured_resource_handlers_return_receipts_and_reconcile() {
        let tree = TempTree::new();
        let native = tree.child("native");
        fs::create_dir(&native).expect("native root");
        let mut config = Config::for_test("env-test");
        config.default_working_directory = crate::device_path::from_native(&native).unwrap();
        let daemon = Fixture::new(&config, 12);
        let initialized = initialize(&daemon).await;
        let available_methods = initialized["result"]["descriptor"]["available_methods"]
            .as_array()
            .expect("available methods array");
        for method in ["file.stat", "file.write_text", "file.find", "file.search"] {
            assert!(available_methods.iter().any(|value| value == method));
        }

        let write: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "write-e2e"},
                        "path": {"path": crate::device_path::from_native(&native.join("block2.txt")).unwrap()},
                        "mode": "create",
                        "text": "block2\n"
                    }),
                ))
                .await,
        )
        .expect("write response");
        assert_eq!(write["result"]["bytes_written"], 7);
        assert_eq!(
            fs::read_to_string(native.join("block2.txt")).expect("file"),
            "block2\n"
        );
        let receipt: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(3),
                    "receipt.get",
                    json!({
                        "context": {"operation_id": "receipt-e2e"},
                        "operation_id": "write-e2e"
                    }),
                ))
                .await,
        )
        .expect("receipt response");
        assert_eq!(receipt["result"]["receipt"]["operation_id"], "write-e2e");
        assert_eq!(receipt["result"]["receipt"]["outcome"], "succeeded");

        let read: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(4),
                    "file.read_text",
                    json!({
                        "context": {"operation_id": "read-e2e"},
                        "path": {"path": crate::device_path::from_native(&native.join("block2.txt")).unwrap()},
                        "line_offset": 0,
                        "line_limit": 1,
                        "max_line_length": 2000
                    }),
                ))
                .await,
        )
        .expect("read response");
        assert_eq!(read["result"]["text"], "block2\n");
        assert_eq!(read["result"]["line_offset"], 0);
        assert_eq!(read["result"]["lines_read"], 1);
        assert_eq!(read["result"]["has_more"], false);

        let failed: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(41),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "failed-write"},
                        "path": {"path": crate::device_path::from_native(&native.join("missing.txt")).unwrap()},
                        "mode": "replace",
                        "text": "never committed"
                    }),
                ))
                .await,
        )
        .expect("failed mutation response");
        assert_eq!(
            failed["error"]["data"]["receipt"]["operation_id"],
            "failed-write"
        );
        assert_eq!(failed["error"]["data"]["receipt"]["outcome"], "failed");
        let failed_receipt: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(42),
                    "receipt.get",
                    json!({
                        "context": {"operation_id": "failed-receipt"},
                        "operation_id": "failed-write"
                    }),
                ))
                .await,
        )
        .expect("failed receipt response");
        assert_eq!(failed_receipt["result"]["receipt"]["outcome"], "failed");
        let failed_replay: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(43),
                    "file.write_text",
                    json!({
                        "context": {"operation_id": "failed-write"},
                        "path": {"path": crate::device_path::from_native(&native.join("missing.txt")).unwrap()},
                        "mode": "replace",
                        "text": "never committed"
                    }),
                ))
                .await,
        )
        .expect("failed replay response");
        assert_eq!(
            failed_replay["error"]["data"]["error_type"],
            failed["error"]["data"]["error_type"]
        );
        assert_eq!(
            failed_replay["error"]["data"]["receipt"],
            failed["error"]["data"]["receipt"]
        );
        assert_eq!(
            failed_replay["error"]["data"]["operation_id"],
            "failed-write"
        );

        let cancelled: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(5),
                    "operation.cancel",
                    json!({
                        "context": {"operation_id": "cancel-e2e"},
                        "target_operation_id": "write-e2e"
                    }),
                ))
                .await,
        )
        .expect("cancel response");
        assert_eq!(cancelled["result"]["status"], "already_terminal");
    }

    #[tokio::test]
    async fn abandoned_waiter_does_not_end_owned_native_mutation_early() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 13);
        let _ = initialize(&daemon).await;
        let params = FileWriteTextParams {
            context: EIPCallContext {
                operation_id: "drain-write".to_owned(),
                timeout_ms: None,
            },
            path: EIPPath {
                path: "/drain.txt".to_owned(),
            },
            mode: FileWriteMode::Create,
            text: "drain".to_owned(),
            executable: None,
        };
        let operation = match daemon
            .begin_record("file.write_text", &params.context, &params)
            .expect("operation is admitted")
        {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(_) | BeginOutcome::ReplayFailure(_) => {
                panic!("new operation expected")
            }
        };
        let (operation, receipt) =
            mutation_receipt(operation, "file.write_text").expect("receipt is armed");
        let gate = Arc::new((Mutex::new(false), Condvar::new()));
        let blocking_gate = Arc::clone(&gate);
        let (started_tx, started_rx) = tokio::sync::oneshot::channel();
        let owned =
            daemon
                .operations
                .spawn_owned(params.context.operation_id.clone(), async move {
                    tokio::task::spawn_blocking(move || {
                        let _ = started_tx.send(());
                        let (released, changed) = &*blocking_gate;
                        let mut released = released.lock().unwrap_or_else(PoisonError::into_inner);
                        while !*released {
                            released = changed
                                .wait(released)
                                .unwrap_or_else(PoisonError::into_inner);
                        }
                    })
                    .await
                    .expect("blocking worker exits");
                    operation
                        .finish(&json!({"completed": true}), Some(receipt))
                        .expect("operation finishes");
                    Ok(json!({"completed": true}))
                });
        let response_waiter = tokio::spawn(async move {
            let _ = owned.await;
        });
        started_rx.await.expect("blocking worker starts");
        response_waiter.abort();
        assert!(
            response_waiter
                .await
                .expect_err("response waiter is cancelled")
                .is_cancelled()
        );
        assert_eq!(daemon.operations.record_stats().0, 1);
        assert_eq!(
            daemon.operations.active_owned_ids(),
            vec!["drain-write".to_owned()]
        );
        daemon.operations.begin_drain();
        let ledger = daemon.operations.clone();
        let draining = tokio::spawn(async move {
            ledger.wait_until_owned_idle().await;
        });
        tokio::task::yield_now().await;
        assert!(!draining.is_finished());

        let (released, changed) = &*gate;
        *released.lock().unwrap_or_else(PoisonError::into_inner) = true;
        changed.notify_all();
        draining
            .await
            .expect("owned mutation reaches native completion");

        let retry = params;
        let replay = daemon
            .begin_record("file.write_text", &retry.context, &retry)
            .expect("same operation identity replays");
        let BeginOutcome::Replay(result) = replay else {
            panic!("completed result replay expected");
        };
        assert_eq!(result["completed"], true);
    }

    #[tokio::test]
    async fn mutation_registered_after_drain_is_reconciled_before_dispatch() {
        let config = Config::for_test("env-test");
        let daemon = Fixture::new(&config, 14);
        let _ = initialize(&daemon).await;
        daemon.operations.begin_drain();
        let params = FileWriteTextParams {
            context: EIPCallContext {
                operation_id: "late-write".to_owned(),
                timeout_ms: None,
            },
            path: EIPPath {
                path: "/late.txt".to_owned(),
            },
            mode: FileWriteMode::Create,
            text: "late".to_owned(),
            executable: None,
        };
        let operation = match daemon
            .begin_record("file.write_text", &params.context, &params)
            .expect("operation is admitted")
        {
            BeginOutcome::New(operation) => operation,
            BeginOutcome::Replay(_) | BeginOutcome::ReplayFailure(_) => {
                panic!("new operation expected")
            }
        };
        let (operation, receipt) =
            mutation_receipt(operation, "file.write_text").expect("receipt is armed");
        let dispatched = Arc::new(AtomicBool::new(false));
        let worker_dispatched = Arc::clone(&dispatched);
        let owned =
            daemon
                .operations
                .spawn_owned(params.context.operation_id.clone(), async move {
                    worker_dispatched.store(true, Ordering::SeqCst);
                    operation
                        .finish(&json!({"completed": true}), Some(receipt))
                        .expect("operation finishes");
                    Ok(json!({"completed": true}))
                });
        let error = daemon
            .await_owned_operation(&params.context.operation_id, owned)
            .await
            .expect_err("late registration is reconciled");
        assert_eq!(error.data.error_type, crate::eip::ErrorType::UnknownOutcome);
        assert!(!dispatched.load(Ordering::SeqCst));
        daemon.operations.wait_until_owned_idle().await;
    }

    #[tokio::test]
    async fn failed_open_negotiation_does_not_poison_carrier_or_siblings() {
        let fixture = Fixture::new(&Config::for_test("env-test"), 10);
        initialize(&fixture).await;
        let response = fixture
            .call(
                "session.open",
                fixture.open_params(json!(["vendor.unknown"])),
            )
            .await;
        assert_eq!(response["error"]["data"]["error_type"], "unsupported");
        assert!(!fixture.carrier.closed.load(Ordering::Acquire));
        assert_eq!(
            readiness(&fixture, 3, "ready-after-failed-open").await["result"]["ready"],
            true
        );
        assert!(
            fixture
                .call("session.open", fixture.open_params(json!([])))
                .await
                .get("result")
                .is_some()
        );
    }
}
