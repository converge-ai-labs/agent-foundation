use std::{
    collections::BTreeMap,
    error::Error,
    fmt,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    sync::{
        Mutex, MutexGuard, PoisonError,
        atomic::{AtomicU64, Ordering},
    },
    time::{Duration, Instant},
};

use serde::{Serialize, de::DeserializeOwned};
use tokio::sync::{Notify, mpsc, watch};

use crate::{
    config::Config,
    eip::{
        self, DispatchError, DispatchStage, EIPError, EIPErrorData, EIPServerInfo, EipHandler,
        EnvironmentDescribeParams, EnvironmentDescribeResult, EnvironmentDescriptor,
        EnvironmentReadinessParams, EnvironmentReadinessResult, ErrorType, ExecutionFeatures,
        InitializeParams, InitializeResult, JsonRpcErrorResponse, JsonRpcId, JsonRpcRequest,
        JsonRpcSuccessResponse, ReceiptOutcome, ReceiptStage, RetryHint, SessionCloseParams,
        SessionCloseResult,
    },
    isolation::IsolationRuntime,
    mount::MountRegistry,
    operation::{
        ActiveResponseHandoff, BeginOutcome, LedgerError, OperationInterruption, OperationLease,
        OperationLedger, OwnedOperationResult, PendingAdmissionWait, PendingOperationGuard,
        scope_carrier_attempt,
    },
    process::{ExecutionManager, ProcessError, StartFailure},
    resource::{ResourceError, ResourceRegistry},
    retention::{RetentionError, RetentionQuota, RetentionStore},
    transfer::{TransferError, TransferRegistry},
};

const MAX_STRING_REQUEST_ID_BYTES: usize = 128;
const BASE_CAPABILITIES: [&str; 3] = [
    "environment.describe",
    "environment.readiness",
    "session.close",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SessionState {
    Uninitialized,
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
    lifecycle: SessionState,
    active_session_work: usize,
}

struct SessionWorkGuard<'a> {
    admission: &'a SessionAdmission,
}

struct AuthoritySurface {
    mounts: MountRegistry,
    descriptor: EnvironmentDescriptor,
}

struct AuthoritySurfaces {
    scoped: AuthoritySurface,
}

pub(crate) struct CarrierResponse {
    payload: Vec<u8>,
    handoff: Option<ActiveResponseHandoff>,
    closes_session: bool,
}

impl CarrierResponse {
    fn plain(payload: Vec<u8>) -> Self {
        Self {
            payload,
            handoff: None,
            closes_session: false,
        }
    }

    pub(crate) fn into_parts(self) -> (Vec<u8>, Option<ActiveResponseHandoff>, bool) {
        (self.payload, self.handoff, self.closes_session)
    }
}

pub(crate) struct Daemon {
    surfaces: AuthoritySurfaces,
    session: SessionAdmission,
    max_operation_duration: Duration,
    operations: OperationLedger,
    resources: ResourceRegistry,
    retention: RetentionStore,
    execution: Option<ExecutionManager>,
    transfers: TransferRegistry,
    closed: watch::Sender<bool>,
    max_response_bytes: usize,
    next_carrier_attempt: AtomicU64,
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

fn build_descriptor(
    config: &Config,
    generation: u64,
    mounts: &MountRegistry,
    isolation: &IsolationRuntime,
    execution: Option<&ExecutionManager>,
) -> EnvironmentDescriptor {
    let mut available_methods = BASE_CAPABILITIES
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    available_methods.extend([
        "operation.cancel".to_owned(),
        "port.inspect".to_owned(),
        "port.wait".to_owned(),
        "receipt.get".to_owned(),
    ]);
    for (operation, methods) in [
        ("stat", &["file.stat"][..]),
        ("read_text", &["file.read_text"][..]),
        (
            "open_reader",
            &["file.open_reader", "file.close_reader"][..],
        ),
        ("list", &["file.list"][..]),
        ("find", &["file.find"][..]),
        ("search", &["file.search"][..]),
        ("write_text", &["file.write_text"][..]),
        (
            "open_writer",
            &[
                "file.open_writer",
                "file.commit_writer",
                "file.abort_writer",
            ][..],
        ),
        ("mkdir", &["file.mkdir"][..]),
        ("patch_text", &["file.patch_text"][..]),
        ("copy", &["file.copy"][..]),
        ("move", &["file.move"][..]),
        ("remove", &["file.remove"][..]),
    ] {
        if mounts.supports_anywhere(operation) {
            available_methods.extend(methods.iter().map(|method| (*method).to_owned()));
        }
    }
    let execution_available = execution.is_some() && mounts.supports_commands();
    let execution_features = ExecutionFeatures {
        process_count_limit: false,
        memory_bytes_limit: false,
        cpu_time_limit: false,
        per_command_network_deny: execution_available
            && isolation.supports_per_command_network_deny(),
        signal_interrupt: execution_available && cfg!(unix),
        signal_terminate: execution_available && cfg!(unix),
    };
    if execution_available {
        available_methods.extend([
            "output.read".to_owned(),
            "output.release".to_owned(),
            "shell.exec".to_owned(),
            "process.start".to_owned(),
            "process.inspect".to_owned(),
            "process.write_stdin".to_owned(),
            "process.close_stdin".to_owned(),
            "process.wait".to_owned(),
            "process.kill".to_owned(),
            "process.release".to_owned(),
        ]);
        if execution_features.signal_interrupt || execution_features.signal_terminate {
            available_methods.push("process.signal".to_owned());
        }
    }
    available_methods.sort();
    EnvironmentDescriptor {
        environment_id: config.environment_id.clone(),
        generation,
        mounts: mounts.descriptors(),
        shell_profiles: execution
            .map(ExecutionManager::shell_profiles)
            .unwrap_or_default(),
        limits: config.limits.descriptor(),
        isolation: isolation.posture(),
        root_mount_id: mounts.root_mount_id().map(str::to_owned),
        available_methods,
        execution_features,
    }
}

impl Daemon {
    pub(crate) fn new(config: &Config) -> Result<Self, DaemonInitError> {
        Self::with_generation(config, fresh_generation()?)
    }

    fn with_generation(config: &Config, generation: u64) -> Result<Self, DaemonInitError> {
        if generation == 0 {
            return Err(DaemonInitError::new("generation must be nonzero"));
        }
        let max_response_bytes = usize::try_from(config.limits.max_response_bytes)
            .map_err(|_| DaemonInitError::new("max_response_bytes does not fit this platform"))?;
        let max_operation_records =
            usize::try_from(config.limits.max_operation_records).map_err(|_| {
                DaemonInitError::new("max_operation_records does not fit this platform")
            })?;
        let operation_record_ttl = Duration::from_millis(config.limits.operation_record_ttl_ms);
        let scoped_mounts = MountRegistry::initialize_scoped(config).map_err(|error| {
            DaemonInitError::new(format!("mount initialization failed: {error}"))
        })?;
        let isolation = IsolationRuntime::initialize(config).map_err(|error| {
            DaemonInitError::new(format!(
                "execution isolation initialization failed: {error}"
            ))
        })?;
        let transfers = TransferRegistry::new(config, generation)
            .map_err(|_| DaemonInitError::new("transfer registry initialization failed"))?;
        let operations = OperationLedger::new(
            config.environment_id.clone(),
            generation,
            max_operation_records,
            operation_record_ttl,
            Duration::from_millis(config.limits.max_operation_duration_ms),
        );
        let retention_quota = RetentionQuota::new(config)
            .map_err(|_| DaemonInitError::new("retention quota initialization failed"))?;
        let resources = ResourceRegistry::new(config, operations.clone());
        let retention = RetentionStore::new(config, generation, retention_quota)
            .map_err(|_| DaemonInitError::new("retention store initialization failed"))?;
        let execution =
            ExecutionManager::new(config, isolation.clone(), generation, retention.clone())
                .map_err(|_| DaemonInitError::new("execution manager initialization failed"))?;
        let scoped_descriptor = build_descriptor(
            config,
            generation,
            &scoped_mounts,
            &isolation,
            execution.as_ref(),
        );
        let surfaces = AuthoritySurfaces {
            scoped: AuthoritySurface {
                mounts: scoped_mounts,
                descriptor: scoped_descriptor,
            },
        };
        let (closed, _) = watch::channel(false);
        Ok(Self {
            surfaces,
            session: SessionAdmission {
                state: Mutex::new(SessionAdmissionState {
                    lifecycle: SessionState::Uninitialized,
                    active_session_work: 0,
                }),
                idle: Notify::new(),
            },
            max_operation_duration: Duration::from_millis(config.limits.max_operation_duration_ms),
            operations,
            resources,
            retention,
            execution,
            transfers,
            closed,
            max_response_bytes,
            next_carrier_attempt: AtomicU64::new(1),
        })
    }

    pub(crate) fn subscribe_closed(&self) -> watch::Receiver<bool> {
        self.closed.subscribe()
    }

    pub(crate) fn track_pending_payload(&self, payload: &str) -> Option<PendingOperationGuard> {
        let value = serde_json::from_str::<serde_json::Value>(payload).ok()?;
        let operation_id = value
            .get("params")?
            .get("context")?
            .get("operation_id")?
            .as_str()?;
        Some(self.operations.track_pending(operation_id.to_owned()))
    }

    pub(crate) fn has_active_file_transfers(&self) -> bool {
        self.transfers.has_active()
    }

    pub(crate) fn begin_session(
        &self,
        sender: mpsc::Sender<eip::DataFrame>,
    ) -> Result<(), DaemonInitError> {
        let mut session = self.session.state();
        if !matches!(
            session.lifecycle,
            SessionState::Uninitialized | SessionState::Closed
        ) || session.active_session_work != 0
        {
            return Err(DaemonInitError::new("another EIP session is still active"));
        }
        self.transfers
            .begin_session(sender)
            .map_err(|_| DaemonInitError::new("transfer session is still active"))?;
        session.lifecycle = SessionState::Uninitialized;
        self.closed.send_replace(false);
        Ok(())
    }

    pub(crate) fn session_initialized(&self) -> bool {
        matches!(
            self.session.state().lifecycle,
            SessionState::Initialized | SessionState::Ready | SessionState::NotReady
        )
    }

    pub(crate) async fn handle_data_frame(
        &self,
        frame: eip::DataFrame,
    ) -> Result<(), TransferError> {
        let _work = self.session.admit_work().ok_or(TransferError::Protocol)?;
        self.transfers.handle_frame(frame).await
    }

    pub(crate) async fn transport_closed(&self, budget: Duration) -> bool {
        self.session.close();
        self.closed.send_replace(true);
        self.transfers.begin_session_close();
        let admission_budget = budget / 2;
        let admission_idle = tokio::time::timeout(admission_budget, self.session.wait_until_idle())
            .await
            .is_ok();
        let transfers_closed = tokio::time::timeout(
            budget.saturating_sub(admission_budget),
            self.transfers.close_session(),
        )
        .await
        .is_ok();
        admission_idle && transfers_closed
    }

    pub(crate) async fn drain_processes(&self, budget: Duration) -> bool {
        match &self.execution {
            Some(execution) => execution.drain(budget).await,
            None => true,
        }
    }

    pub(crate) async fn drain_owned_operations(&self, budget: Duration) -> bool {
        let started = Instant::now();
        self.operations.begin_drain();
        let tasks_drained =
            tokio::time::timeout(budget / 2, self.operations.wait_until_owned_idle())
                .await
                .is_ok();
        let transfers_reconciled = tokio::time::timeout(
            budget.saturating_sub(started.elapsed()),
            self.transfers.reconcile_committing(),
        )
        .await
        .is_ok();
        tasks_drained && transfers_reconciled
    }

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

    pub(crate) async fn maintenance(&self) {
        self.transfers.expire().await;
    }

    #[cfg(test)]
    pub(crate) async fn handle_payload(&self, payload: &str) -> Vec<u8> {
        let response = self.handle_payload_for_carrier(payload).await;
        let (payload, handoff, _) = response.into_parts();
        if let Some(handoff) = handoff {
            handoff.complete();
        }
        payload
    }

    pub(crate) async fn handle_payload_for_carrier(&self, payload: &str) -> CarrierResponse {
        let attempt = self.next_carrier_attempt.fetch_add(1, Ordering::Relaxed);
        scope_carrier_attempt(attempt, Box::pin(self.handle_payload_core(payload))).await
    }

    async fn handle_payload_core(&self, payload: &str) -> CarrierResponse {
        let request = match eip::decode::<JsonRpcRequest>(payload) {
            Ok(request) => request,
            Err(error) => {
                let request_id = match &error {
                    eip::DecodeError::Validation(_) => recover_request_id(payload),
                    eip::DecodeError::Json(_) => None,
                };
                let error_type = match error {
                    eip::DecodeError::Json(error)
                        if matches!(
                            error.classify(),
                            serde_json::error::Category::Syntax | serde_json::error::Category::Eof
                        ) =>
                    {
                        ErrorType::ParseError
                    }
                    eip::DecodeError::Json(_) | eip::DecodeError::Validation(_) => {
                        ErrorType::InvalidRequest
                    }
                };
                self.close_if_uninitialized();
                return CarrierResponse::plain(self.error_response(
                    request_id,
                    protocol_error(error_type, error_type_message(error_type)),
                ));
            }
        };

        if !valid_request_id(&request.id) {
            self.close_if_uninitialized();
            return CarrierResponse::plain(self.error_response(
                None,
                protocol_error(ErrorType::InvalidRequest, "invalid JSON-RPC request ID"),
            ));
        }

        let request_id = request.id.clone();
        if let Err(error) = self.preflight(&request.method).await {
            return CarrierResponse::plain(self.error_response(Some(request_id), error));
        }

        let params_json = match serde_json::to_string(&request.params) {
            Ok(params) => params,
            Err(_) => {
                return CarrierResponse::plain(self.error_response(
                    Some(request_id),
                    protocol_error(ErrorType::InternalError, "failed to encode request params"),
                ));
            }
        };
        let is_initialization = request.method == "initialize";
        let is_session_close = request.method == "session.close";
        let result = eip::dispatch(self, &request.method, &params_json).await;
        let closes_session = is_session_close && result.is_ok();
        if let Err(DispatchError::Method {
            error,
            method,
            params,
        }) = &result
        {
            self.operations
                .finish_dispatched_failure(method, params, error.clone());
        }
        if is_initialization && result.is_err() {
            self.close_if_uninitialized();
        }

        let handoff = match &result {
            Ok(success) => self
                .operations
                .active_response_handoff(success.method, &success.params),
            Err(DispatchError::Method { method, params, .. }) => {
                self.operations.active_response_handoff(method, params)
            }
            Err(_) => None,
        };
        let payload = match result {
            Ok(success) if matches!(success.result, serde_json::Value::Object(_)) => {
                let serde_json::Value::Object(fields) = success.result else {
                    unreachable!("guarded object result")
                };
                let response = JsonRpcSuccessResponse {
                    jsonrpc: "2.0".to_owned(),
                    id: request_id,
                    result: fields.into_iter().collect(),
                    extensions: BTreeMap::new(),
                };
                match eip::encode(&response) {
                    Ok(encoded) if encoded.len() <= self.max_response_bytes => encoded,
                    Ok(_) | Err(_) => self.error_response(
                        Some(response.id),
                        protocol_error(ErrorType::InternalError, "response encoding failed"),
                    ),
                }
            }
            Ok(_) => self.error_response(
                Some(request_id),
                protocol_error(
                    ErrorType::InternalError,
                    "method returned a non-object result",
                ),
            ),
            Err(error) => {
                self.error_response(Some(request_id), map_dispatch_error(error, &request.method))
            }
        };
        CarrierResponse {
            payload,
            handoff,
            closes_session,
        }
    }

    async fn preflight(&self, method: &str) -> Result<(), EIPError> {
        let mut state = self.session.state();
        match state.lifecycle {
            SessionState::Uninitialized if method == "initialize" => Ok(()),
            SessionState::Uninitialized => {
                state.lifecycle = SessionState::Closed;
                self.closed.send_replace(true);
                Err(protocol_error(
                    ErrorType::NotInitialized,
                    "initialize must be the first request",
                ))
            }
            SessionState::Initialized | SessionState::Ready | SessionState::NotReady
                if method == "initialize" =>
            {
                Err(protocol_error(
                    ErrorType::AlreadyInitialized,
                    "session is already initialized",
                ))
            }
            SessionState::Initialized | SessionState::Ready | SessionState::NotReady => {
                if !eip::METHODS.iter().any(|known| known.name == method) {
                    return Err(protocol_error(
                        ErrorType::MethodNotFound,
                        "method not found",
                    ));
                }
                let surface = &self.surfaces.scoped;
                if !surface
                    .descriptor
                    .available_methods
                    .iter()
                    .any(|advertised| advertised == method)
                {
                    return Err(protocol_error(
                        ErrorType::Unsupported,
                        "method is not available",
                    ));
                }
                match state.lifecycle {
                    SessionState::Initialized
                        if matches!(method, "environment.readiness" | "session.close") =>
                    {
                        Ok(())
                    }
                    SessionState::Initialized => Err(protocol_error(
                        ErrorType::NotInitialized,
                        "session readiness is not established",
                    )),
                    SessionState::Ready => Ok(()),
                    SessionState::NotReady if method == "session.close" => Ok(()),
                    SessionState::NotReady => Err(protocol_error(
                        ErrorType::NotInitialized,
                        "session is not ready",
                    )),
                    SessionState::Uninitialized | SessionState::Closed => {
                        unreachable!("matched initialized session state")
                    }
                }
            }
            SessionState::Closed => Err(protocol_error(
                ErrorType::NotInitialized,
                "session is closed",
            )),
        }
    }

    fn close_if_uninitialized(&self) {
        let mut state = self.session.state();
        if state.lifecycle == SessionState::Uninitialized {
            state.lifecycle = SessionState::Closed;
            self.closed.send_replace(true);
        }
    }

    #[allow(clippy::result_large_err)]
    fn effective_surface(&self) -> Result<&AuthoritySurface, EIPError> {
        self.ensure_ready()?;
        Ok(&self.surfaces.scoped)
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

    fn error_response(&self, id: Option<JsonRpcId>, error: EIPError) -> Vec<u8> {
        let response = JsonRpcErrorResponse {
            jsonrpc: "2.0".to_owned(),
            id,
            error,
            extensions: BTreeMap::new(),
        };
        match eip::encode(&response) {
            Ok(encoded) if encoded.len() <= self.max_response_bytes => encoded,
            Ok(_) | Err(_) => {
                b"{\"error\":{\"code\":-32603,\"data\":{\"dispatch_stage\":\"pre_dispatch\",\"error_type\":\"internal_error\",\"retry_hint\":\"never\"},\"message\":\"response encoding failed\"},\"id\":null,\"jsonrpc\":\"2.0\"}".to_vec()
            }
        }
    }
}

impl EipHandler for Daemon {
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
        let descriptor = &self.surfaces.scoped.descriptor;
        let result = EnvironmentReadinessResult {
            ready,
            environment_id: descriptor.environment_id.clone(),
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
            descriptor: self.effective_surface()?.descriptor.clone(),
        };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
    }

    async fn initialize(&self, params: InitializeParams) -> Result<InitializeResult, EIPError> {
        let mut state = self.session.state();
        if state.lifecycle != SessionState::Uninitialized {
            return Err(protocol_error(
                ErrorType::AlreadyInitialized,
                "session is already initialized",
            ));
        }

        let surface = &self.surfaces.scoped;
        let failure = if !params
            .supported_protocol_versions
            .iter()
            .any(|version| version == eip::EIP_PROTOCOL_VERSION)
        {
            Some(protocol_error(
                ErrorType::ProtocolIncompatible,
                "no mutually supported EIP protocol version",
            ))
        } else if params.expected_environment_id != self.surfaces.scoped.descriptor.environment_id {
            Some(protocol_error(
                ErrorType::ProtocolIncompatible,
                "expected Environment identity does not match",
            ))
        } else {
            let descriptor = &surface.descriptor;
            params
                .required_methods
                .iter()
                .find(|required| !descriptor.available_methods.contains(required))
                .map(|required| {
                    protocol_error(
                        ErrorType::ProtocolIncompatible,
                        format!("required method is not available: {required}"),
                    )
                })
        };

        if let Some(error) = failure {
            state.lifecycle = SessionState::Closed;
            self.closed.send_replace(true);
            return Err(error);
        }

        let descriptor = surface.descriptor.clone();
        state.lifecycle = SessionState::Initialized;
        Ok(InitializeResult {
            protocol_version: eip::EIP_PROTOCOL_VERSION.to_owned(),
            server: EIPServerInfo {
                name: "agent-envd".to_owned(),
                version: env!("CARGO_PKG_VERSION").to_owned(),
            },
            descriptor,
        })
    }

    async fn session_close(
        &self,
        params: SessionCloseParams,
    ) -> Result<SessionCloseResult, EIPError> {
        let operation = {
            let mut session = self.session.state();
            if !matches!(
                session.lifecycle,
                SessionState::Initialized | SessionState::Ready | SessionState::NotReady
            ) {
                return Err(protocol_error(
                    ErrorType::NotInitialized,
                    "session is not initialized",
                ));
            }
            let operation = self.begin_record("session.close", &params.context, &params)?;
            session.lifecycle = SessionState::Closed;
            operation
        };
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        self.closed.send_replace(true);
        self.transfers.begin_session_close();
        self.session.wait_until_idle().await;
        self.transfers.close_session().await;
        let result = SessionCloseResult { closed: true };
        operation.finish(&result, None).map_err(map_ledger_error)?;
        Ok(result)
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
        let mounts = self.effective_surface()?.mounts.clone();
        let result = self
            .transfers
            .open_reader(&mounts, &params)
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
        let mounts = self.effective_surface()?.mounts.clone();
        let result = self
            .transfers
            .open_writer(&mounts, &params)
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
        let mounts = self.effective_surface()?.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.stat(&mounts, &call))
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
        let mounts = self.effective_surface()?.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.read_text(&mounts, &call))
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
        let mounts = self.effective_surface()?.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.list(&mounts, &call))
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
        let mounts = self.effective_surface()?.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.find(&mounts, &call))
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
        let mounts = self.effective_surface()?.mounts.clone();
        let call = params.clone();
        let result = tokio::task::spawn_blocking(move || resources.search(&mounts, &call))
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
        let mounts = self.effective_surface()?.mounts.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let write =
                    tokio::task::spawn_blocking(move || resources.write_text(&mounts, &params))
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
        let mounts = self.effective_surface()?.mounts.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let mkdir =
                    tokio::task::spawn_blocking(move || resources.mkdir(&mounts, &params)).await;
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
        let mounts = self.effective_surface()?.mounts.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let patch =
                    tokio::task::spawn_blocking(move || resources.patch_text(&mounts, &params))
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
        let mounts = self.effective_surface()?.mounts.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let copy =
                    tokio::task::spawn_blocking(move || resources.copy(&mounts, &params)).await;
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
        let mounts = self.effective_surface()?.mounts.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let moved =
                    tokio::task::spawn_blocking(move || resources.move_path(&mounts, &params))
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
        let mounts = self.effective_surface()?.mounts.clone();
        let owned = self
            .operations
            .spawn_owned(operation_id.clone(), async move {
                let removed =
                    tokio::task::spawn_blocking(move || resources.remove(&mounts, &params)).await;
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
        let mounts = self.effective_surface()?.mounts.clone();
        let operation = self.begin_record("process.start", &params.context, &params)?;
        let operation = match operation {
            BeginOutcome::Replay(value) => return self.decode_replay(value),
            BeginOutcome::ReplayFailure(error) => return Err(*error),
            BeginOutcome::New(operation) => operation,
        };
        let started = match execution
            .start(&mounts, &params.request, true, || {
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
        let mounts = self.effective_surface()?.mounts.clone();
        let hard_deadline =
            effective_deadline(params.context.timeout_ms, self.max_operation_duration)?;
        let started = match execution
            .start(&mounts, &params.request, true, || {
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
        error.data.environment_id = Some(receipt.environment_id.clone());
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
        error.data.environment_id = Some(receipt.environment_id.clone());
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
    failure.data.environment_id = Some(unknown_receipt.environment_id.clone());
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
            environment_id: None,
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
        config::{Config, TrustedMountConfig},
        eip::{
            EIPCallContext, EIPPath, EipHandler, EnvironmentDescribeParams, FileWriteMode,
            FileWriteTextParams, OperationCancelParams, OperationCancelStatus,
        },
        operation::{BeginOutcome, random_selector},
    };

    use super::{Daemon, fresh_generation, mutation_receipt};

    struct TempTree(PathBuf);

    impl TempTree {
        fn new() -> Self {
            let path = std::env::temp_dir().join(
                random_selector("agent-envd-daemon-test").expect("random temporary directory"),
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

    fn request(id: Value, method: &str, params: Value) -> String {
        json!({"jsonrpc": "2.0", "id": id, "method": method, "params": params}).to_string()
    }

    fn initialize_params(required_methods: Value) -> Value {
        json!({
            "supported_protocol_versions": ["0.1"],
            "client": {"name": "test", "version": "1"},
            "expected_environment_id": "env-test",
            "required_methods": required_methods
        })
    }

    async fn initialize_protocol(daemon: &Daemon) -> Value {
        let bytes = daemon
            .handle_payload(&request(
                json!(1),
                "initialize",
                initialize_params(json!([])),
            ))
            .await;
        serde_json::from_slice(&bytes).expect("response is JSON")
    }

    async fn readiness(daemon: &Daemon, request_id: u64, operation_id: &str) -> Value {
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

    async fn initialize(daemon: &Daemon) -> Value {
        let initialized = initialize_protocol(daemon).await;
        let ready = readiness(daemon, 2, "readiness-initial").await;
        assert_eq!(ready["result"]["ready"], true);
        initialized
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
    async fn carrier_loss_allows_a_fresh_session_in_the_same_generation() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 7).expect("daemon builds");
        let (first_sender, _first_receiver) = tokio::sync::mpsc::channel(2);
        daemon
            .begin_session(first_sender)
            .expect("first carrier begins a session");

        let first = initialize(&daemon).await;
        assert_eq!(first["result"]["descriptor"]["generation"], 7);
        assert!(daemon.transport_closed(Duration::from_secs(1)).await);

        let (second_sender, _second_receiver) = tokio::sync::mpsc::channel(2);
        daemon
            .begin_session(second_sender)
            .expect("replacement carrier begins a fresh session");
        assert!(!daemon.session_initialized());
        let second = initialize(&daemon).await;
        assert_eq!(second["result"]["descriptor"]["generation"], 7);
    }

    #[tokio::test]
    async fn initialize_describe_and_close_follow_session_lifecycle() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 7).expect("daemon builds");

        let initialized = initialize(&daemon).await;
        assert_eq!(initialized["result"]["protocol_version"], "0.1");
        assert_eq!(initialized["result"]["descriptor"]["generation"], 7);
        assert_eq!(
            initialized["result"]["descriptor"]["available_methods"],
            json!([
                "environment.describe",
                "environment.readiness",
                "operation.cancel",
                "port.inspect",
                "port.wait",
                "receipt.get",
                "session.close"
            ])
        );
        assert_eq!(
            initialized["result"]["descriptor"]["execution_features"],
            json!({
                "process_count_limit": false,
                "memory_bytes_limit": false,
                "cpu_time_limit": false,
                "per_command_network_deny": false,
                "signal_interrupt": false,
                "signal_terminate": false
            })
        );

        let described: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "environment.describe",
                    json!({"context": {"operation_id": "describe-1"}}),
                ))
                .await,
        )
        .expect("response is JSON");
        assert_eq!(described["result"]["descriptor"]["generation"], 7);

        let closed: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(3),
                    "session.close",
                    json!({"context": {"operation_id": "close-1"}}),
                ))
                .await,
        )
        .expect("response is JSON");
        assert_eq!(closed["result"]["closed"], true);
        assert!(*daemon.subscribe_closed().borrow());
    }

    #[tokio::test]
    async fn readiness_gates_application_dispatch_and_reports_exact_generation() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 7).expect("daemon builds");

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
        assert_eq!(ready["result"]["environment_id"], "env-test");
        assert_eq!(ready["result"]["generation"], 7);

        let later = readiness(&daemon, 4, "readiness-later").await;
        assert_eq!(later["result"]["ready"], true);
    }

    #[tokio::test]
    async fn draining_readiness_returns_false_and_still_allows_session_close() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 7).expect("daemon builds");
        let _ = initialize_protocol(&daemon).await;
        daemon.operations.begin_drain();

        let readiness = readiness(&daemon, 2, "readiness-draining").await;
        assert_eq!(readiness["result"]["ready"], false);
        let closed: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(3),
                    "session.close",
                    json!({"context": {"operation_id": "close-not-ready"}}),
                ))
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
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: native.clone(),
            writable: true,
            allow_command_execution: false,
            max_file_bytes: 1024 * 1024,
            allowed_operations: Vec::new(),
        });
        let daemon = Arc::new(Daemon::with_generation(&config, 71).expect("daemon builds"));
        let _ = initialize(&daemon).await;
        EipHandler::file_open_writer(
            daemon.as_ref(),
            FileWriterOpenParams {
                context: EIPCallContext {
                    operation_id: "open-before-close".to_owned(),
                    timeout_ms: None,
                },
                path: EIPPath {
                    mount_id: "workspace".to_owned(),
                    path: "/candidate.bin".to_owned(),
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
        let closing = tokio::spawn(async move {
            closing_daemon
                .transport_closed(Duration::from_secs(1))
                .await
        });
        tokio::task::yield_now().await;
        assert!(!closing.is_finished());
        drop(in_flight);
        assert!(closing.await.expect("close waits for admitted work"));
        assert!(!daemon.has_active_file_transfers());
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

        let second = Arc::new(Daemon::with_generation(&config, 72).expect("daemon builds"));
        let _ = initialize(&second).await;
        assert!(second.transport_closed(Duration::from_secs(1)).await);
        let rejected = EipHandler::file_write_text(
            second.as_ref(),
            FileWriteTextParams {
                context: EIPCallContext {
                    operation_id: "write-after-close".to_owned(),
                    timeout_ms: None,
                },
                path: EIPPath {
                    mount_id: "workspace".to_owned(),
                    path: "/too-late.txt".to_owned(),
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
        let daemon = Arc::new(Daemon::with_generation(&config, 74).expect("daemon builds"));
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
        let daemon = Daemon::with_generation(&config, 73).expect("daemon builds");
        let _ = initialize(&daemon).await;
        let stalled = daemon
            .session
            .admit_work()
            .expect("admits a handoff before close");
        let closed = tokio::time::timeout(
            Duration::from_secs(1),
            daemon.transport_closed(Duration::from_millis(20)),
        )
        .await
        .expect("transport teardown stays bounded");
        assert!(!closed);
        drop(stalled);
    }

    #[tokio::test]
    async fn first_non_initialize_request_fails_and_closes_session() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 8).expect("daemon builds");

        let response: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(1),
                    "environment.describe",
                    json!({"context": {"operation_id": "describe-1"}}),
                ))
                .await,
        )
        .expect("response is JSON");

        assert_eq!(response["error"]["code"], -32001);
        assert!(*daemon.subscribe_closed().borrow());
    }

    #[tokio::test]
    async fn active_only_operation_ids_are_reusable_after_response_handoff() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 9).expect("daemon builds");
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
        let daemon = Daemon::with_generation(&config, 91).expect("daemon builds");
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
            let (payload, handoff, _) = response.into_parts();
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
        let daemon = Daemon::with_generation(&config, 10).expect("daemon builds");
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
        let daemon = Daemon::with_generation(&config, 10).expect("daemon builds");
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
        let daemon = Daemon::with_generation(&config, 11).expect("daemon builds");
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
        let daemon = Daemon::with_generation(&config, 9).expect("daemon builds");
        let _ = initialize(&daemon).await;

        let response: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "file.stat",
                    json!({"context": {"operation_id": "stat-1"}, "path": {"mount_id": "workspace", "path": "/"}}),
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
        let daemon = Daemon::with_generation(&config, 9).expect("daemon builds");
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
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: native.clone(),
            writable: false,
            allow_command_execution: false,
            max_file_bytes: 1024 * 1024,
            allowed_operations: Vec::new(),
        });
        let daemon = Daemon::with_generation(&config, 12).expect("daemon builds");
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
                        "path": {"mount_id": "workspace", "path": "/"}
                    }),
                ))
                .await,
        )
        .expect("collision response");
        assert!(collided.get("result").is_some());

        let open_params = |operation_id: &str| {
            json!({
                "context": {"operation_id": operation_id},
                "path": {"mount_id": "workspace", "path": "/replay.txt"}
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
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: native.clone(),
            writable: true,
            allow_command_execution: false,
            max_file_bytes: 1024,
            allowed_operations: Vec::new(),
        });
        let daemon = Daemon::with_generation(&config, 73).expect("daemon builds");
        let _ = initialize(&daemon).await;

        let opened: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(2),
                    "file.open_writer",
                    json!({
                        "context": {"operation_id": "quota-writer-open"},
                        "path": {"mount_id": "workspace", "path": "/writer.bin"},
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
                        "path": {"mount_id": "workspace", "path": "/inline.txt"},
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
                        "path": {"mount_id": "workspace", "path": "/inline.txt"},
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
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: native.clone(),
            writable: true,
            allow_command_execution: false,
            max_file_bytes: 1024 * 1024,
            allowed_operations: Vec::new(),
        });
        let daemon = Daemon::with_generation(&config, 12).expect("daemon builds");
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
                        "path": {"mount_id": "workspace", "path": "/block2.txt"},
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
                        "path": {"mount_id": "workspace", "path": "/block2.txt"},
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
                        "path": {"mount_id": "workspace", "path": "/missing.txt"},
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
                        "path": {"mount_id": "workspace", "path": "/missing.txt"},
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
        let daemon = Daemon::with_generation(&config, 13).expect("daemon builds");
        let _ = initialize(&daemon).await;
        let params = FileWriteTextParams {
            context: EIPCallContext {
                operation_id: "drain-write".to_owned(),
                timeout_ms: None,
            },
            path: EIPPath {
                mount_id: "workspace".to_owned(),
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
        let daemon = Daemon::with_generation(&config, 14).expect("daemon builds");
        let _ = initialize(&daemon).await;
        daemon.operations.begin_drain();
        let params = FileWriteTextParams {
            context: EIPCallContext {
                operation_id: "late-write".to_owned(),
                timeout_ms: None,
            },
            path: EIPPath {
                mount_id: "workspace".to_owned(),
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
    async fn failed_required_method_negotiation_is_terminal() {
        let config = Config::for_test("env-test");
        let daemon = Daemon::with_generation(&config, 10).expect("daemon builds");

        let response: Value = serde_json::from_slice(
            &daemon
                .handle_payload(&request(
                    json!(1),
                    "initialize",
                    initialize_params(json!(["file.stat"])),
                ))
                .await,
        )
        .expect("response is JSON");

        assert_eq!(response["error"]["code"], -32003);
        assert_eq!(
            response["error"]["data"]["error_type"],
            "protocol_incompatible"
        );
        assert!(*daemon.subscribe_closed().borrow());
    }
}
