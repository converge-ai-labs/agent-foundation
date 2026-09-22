use std::{
    collections::BTreeMap,
    path::PathBuf,
    process::Stdio,
    sync::{Arc, Mutex, PoisonError, Weak},
    time::{Duration, Instant},
};

use base64::Engine as _;
use tokio::{
    io::BufReader,
    process::{Child, ChildStdin},
    sync::{Mutex as AsyncMutex, Notify, oneshot},
};

use crate::{
    capacity::{ResourceCapacity, ResourcePermit},
    config::{CommandConfig, Config, reserved_environment_name, valid_environment_name},
    eip::{
        self, CleanupOutcome, CommandRequest, CommandSpec, EncodedBytes, ProcessHandle,
        ProcessInfo, ProcessOutput, ProcessPhase, ProcessSignal, ProcessStatus,
        ProcessWaitCondition, RequestedProcessSignal, TerminationReason,
    },
    filesystem::{DeviceFilesystem, PathError},
    operation::ShortIdAllocator,
    retention::{AppendOutcome, LiveOutput, RetentionError, RetentionStore},
    supervisor::{
        self, ControlSignal, LaunchPlan, OutputStream, StopReason, SupervisorCleanup,
        SupervisorEvent, SupervisorRequest,
    },
};

const SUPERVISOR_HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(5);
const PROCESS_CONTROL_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ProcessError {
    Invalid,
    Unsupported,
    Denied,
    NotFound,

    Busy,
    Conflict,
    OutputLimit,
    Timeout,
    Cancelled,
    PreDispatchTimeout,
    PreDispatchCancelled,
    UnknownOutcome,
    StartFailed,
    CleanupFailed,
    Internal,
}

#[derive(Clone)]
pub(crate) struct ExecutionManager {
    inner: Arc<ExecutionInner>,
}

struct ExecutionInner {
    state: Mutex<ManagerState>,
    retention: RetentionStore,
    command: CommandConfig,
    device_id: String,
    session_id: String,
    working_directory: eip::EIPPath,
    generation: u64,
    max_active: usize,
    max_records: usize,
    capacity: ResourceCapacity,
    max_operation_duration: Duration,
    max_stdin_bytes: u64,
    ids: ShortIdAllocator,
}

struct ManagerState {
    records: BTreeMap<String, Arc<ProcessRecord>>,
    active: usize,
    residual_active: usize,
    starts_in_progress: usize,
    draining: bool,
}

struct ProcessRecord {
    handle: ProcessHandle,
    exposed: bool,
    state: Mutex<RecordState>,
    stdout: StreamOutput,
    stderr: StreamOutput,
    control: Arc<AsyncMutex<ChildStdin>>,
    stdin_operation: AsyncMutex<()>,
    signal_operation: AsyncMutex<()>,
    changed: Notify,
    manager: Weak<ExecutionInner>,
}

struct RecordState {
    capacity: ResourcePermit,
    last_access: Instant,
    status: ProcessStatus,
    stdin_open: bool,
    stdin_bytes: u64,
    stdin_limit: u64,
    terminal_recorded: bool,
    active_released: bool,
    stdin_ack: u64,
    stdin_result: (u64, bool),
    signal_ack: u64,
    signal_result: bool,
    output_limit_crossed: bool,
    output_write_failed: bool,
}

struct StreamOutput {
    output: LiveOutput,
}

#[derive(Clone)]
pub(crate) struct StartedProcess {
    record: Arc<ProcessRecord>,
}

pub(crate) struct StartFailure {
    pub(crate) error: ProcessError,
    pub(crate) started: Option<StartedProcess>,
}

pub(crate) struct StartedRecordRelease {
    record: Option<Arc<ProcessRecord>>,
    retention: RetentionStore,
}

struct PreparedCommand {
    plan: LaunchPlan,
    stdin_limit: u64,
}

struct ResolvedCommand {
    executable: PathBuf,
    arguments: Vec<String>,
    roots: Vec<PathBuf>,
    environment: BTreeMap<String, String>,
}

struct StartReservation {
    manager: Arc<ExecutionInner>,
    capacity: Option<ResourcePermit>,
    active: bool,
}

impl ExecutionManager {
    pub(crate) fn new(
        config: &Config,
        generation: u64,
        session_id: String,
        working_directory: String,
        ids: ShortIdAllocator,
        retention: RetentionStore,
        capacity: ResourceCapacity,
    ) -> Result<Option<Self>, ProcessError> {
        let Some(command) = config.command.clone() else {
            return Ok(None);
        };
        let max_active =
            usize::try_from(config.limits.max_processes).map_err(|_| ProcessError::Internal)?;
        let max_records = usize::try_from(config.limits.max_process_records)
            .map_err(|_| ProcessError::Internal)?;
        if max_active == 0 || max_records < max_active {
            return Err(ProcessError::Internal);
        }
        Ok(Some(Self {
            inner: Arc::new(ExecutionInner {
                state: Mutex::new(ManagerState {
                    records: BTreeMap::new(),
                    active: 0,
                    residual_active: 0,
                    starts_in_progress: 0,
                    draining: false,
                }),
                retention,
                command,
                device_id: config.device_id.clone(),
                session_id,
                working_directory: eip::EIPPath {
                    path: working_directory,
                },
                generation,
                max_active,
                max_records,
                capacity,
                max_operation_duration: Duration::from_millis(
                    config.limits.max_operation_duration_ms,
                ),
                max_stdin_bytes: config.limits.max_request_bytes,
                ids,
            }),
        }))
    }

    pub(crate) fn shell_profiles(&self) -> Vec<eip::ShellProfileDescriptor> {
        self.inner
            .command
            .shell_profiles
            .iter()
            .map(|profile| eip::ShellProfileDescriptor {
                profile_id: profile.profile_id.clone(),
                display_name: profile.display_name.clone(),
                supports_login_mode: profile.allow_login_mode,
                max_script_bytes: profile.max_script_bytes,
            })
            .collect()
    }

    pub(crate) async fn start<F>(
        &self,
        filesystem: &Arc<DeviceFilesystem>,
        request: &CommandRequest,
        exposed: bool,
        dispatch_guard: F,
    ) -> Result<StartedProcess, StartFailure>
    where
        F: Fn() -> Result<(), ProcessError>,
    {
        let prepared = self.prepare_request(filesystem, request)?;
        let mut reservation = self.reserve_start()?;
        self.start_reserved(prepared, exposed, &mut reservation, dispatch_guard)
            .await
    }

    async fn start_reserved<F>(
        &self,
        prepared: PreparedCommand,
        exposed: bool,
        reservation: &mut StartReservation,
        dispatch_guard: F,
    ) -> Result<StartedProcess, StartFailure>
    where
        F: Fn() -> Result<(), ProcessError>,
    {
        let executable = std::env::current_exe().map_err(|_| ProcessError::Internal)?;
        let mut supervisor = tokio::process::Command::new(executable);
        supervisor.arg("--internal-supervisor");
        let mut supervisor = supervisor
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .map_err(|_| ProcessError::StartFailed)?;
        let mut supervisor_stdin = supervisor.stdin.take().ok_or(ProcessError::Internal)?;
        let supervisor_stdout = supervisor.stdout.take().ok_or(ProcessError::Internal)?;
        let mut reader = BufReader::new(supervisor_stdout);

        let booted = tokio::time::timeout(
            SUPERVISOR_HANDSHAKE_TIMEOUT,
            supervisor::read_event(&mut reader),
        )
        .await
        .map_err(|_| ProcessError::StartFailed)?
        .map_err(|_| ProcessError::StartFailed)?;
        if !matches!(booted, Some(SupervisorEvent::Booted { version: 1 })) {
            return Err(ProcessError::StartFailed.into());
        }
        supervisor::write_request(
            &mut supervisor_stdin,
            &SupervisorRequest::Prepare {
                version: 1,
                plan: prepared.plan.clone(),
            },
        )
        .await
        .map_err(|_| ProcessError::StartFailed)?;
        let ready = tokio::time::timeout(
            SUPERVISOR_HANDSHAKE_TIMEOUT,
            supervisor::read_event(&mut reader),
        )
        .await
        .map_err(|_| ProcessError::StartFailed)?
        .map_err(|_| ProcessError::StartFailed)?;
        if !matches!(ready, Some(SupervisorEvent::Prepared)) {
            return Err(ProcessError::StartFailed.into());
        }

        let handle = ProcessHandle(
            self.inner
                .ids
                .next("process")
                .map_err(|_| ProcessError::Internal)?,
        );
        let (retained_stdout, retained_stderr) = self
            .inner
            .retention
            .create_live_pair()
            .await
            .map_err(|error| match error {
                RetentionError::Busy => ProcessError::Busy,
                _ => ProcessError::Internal,
            })?;
        let stdout = StreamOutput::new(retained_stdout);
        let stderr = StreamOutput::new(retained_stderr);
        let control = Arc::new(AsyncMutex::new(supervisor_stdin));
        let record = Arc::new(ProcessRecord {
            handle: handle.clone(),
            exposed,
            state: Mutex::new(RecordState {
                capacity: reservation
                    .capacity
                    .take()
                    .expect("start reservation transfers once"),
                last_access: Instant::now(),
                status: ProcessStatus {
                    phase: ProcessPhase::Starting,
                    termination_reason: None,
                    exit_code: None,
                    signal: None,
                    started_at: None,
                    ended_at: None,
                    cleanup: CleanupOutcome::Pending,
                },
                stdin_open: false,
                stdin_bytes: 0,
                stdin_limit: prepared.stdin_limit,
                terminal_recorded: false,
                active_released: false,
                stdin_ack: 0,
                stdin_result: (0, false),
                signal_ack: 0,
                signal_result: false,
                output_limit_crossed: false,
                output_write_failed: false,
            }),
            stdout,
            stderr,
            control: Arc::clone(&control),
            stdin_operation: AsyncMutex::new(()),
            signal_operation: AsyncMutex::new(()),
            changed: Notify::new(),
            manager: Arc::downgrade(&self.inner),
        });

        let admission = {
            let mut state = self.state();
            if state.draining || state.records.len() >= self.inner.max_records {
                Err(ProcessError::Busy)
            } else {
                state.active = state.active.checked_add(1).ok_or(ProcessError::Internal)?;
                state.records.insert(handle.0.clone(), Arc::clone(&record));
                Ok(())
            }
        };
        if let Err(error) = admission {
            self.inner
                .retention
                .abort_pair([&record.stdout.output, &record.stderr.output])
                .await;
            return Err(error.into());
        }
        reservation.commit();
        if let Err(error) = dispatch_guard() {
            self.rollback_pre_dispatch_start(&handle).await;
            return Err(error.into());
        }

        let started = StartedProcess {
            record: Arc::clone(&record),
        };
        let (confirmation_tx, confirmation_rx) = oneshot::channel();
        tokio::spawn(run_event_pump(
            supervisor,
            reader,
            Arc::clone(&record),
            confirmation_tx,
        ));

        if supervisor::write_request(&mut *control.lock().await, &SupervisorRequest::Start)
            .await
            .is_err()
        {
            let _ = self.stop_started(&started, StopReason::Shutdown).await;
            return Err(StartFailure::with_evidence(
                ProcessError::UnknownOutcome,
                &started,
            ));
        }
        let handshake_deadline = Instant::now() + SUPERVISOR_HANDSHAKE_TIMEOUT;
        let mut confirmation_rx = confirmation_rx;
        loop {
            let remaining = handshake_deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(self
                    .finish_interrupted_start(&started, ProcessError::UnknownOutcome)
                    .await);
            }
            let slice = remaining.min(Duration::from_millis(25));
            match tokio::time::timeout(slice, &mut confirmation_rx).await {
                Ok(Ok(Ok(()))) => return Ok(started),
                Ok(Ok(Err(ProcessError::StartFailed))) => {
                    return Err(
                        self.release_or_preserve_start_failure(&started, ProcessError::StartFailed)
                    );
                }
                Ok(Ok(Err(error))) => {
                    return Err(self.finish_interrupted_start(&started, error).await);
                }
                Ok(Err(_)) => {
                    return Err(self
                        .finish_interrupted_start(&started, ProcessError::UnknownOutcome)
                        .await);
                }
                Err(_) => {
                    if let Err(error) = dispatch_guard() {
                        return Err(self.finish_interrupted_start(&started, error).await);
                    }
                }
            }
        }
    }

    fn prepare_request(
        &self,
        filesystem: &Arc<DeviceFilesystem>,
        request: &CommandRequest,
    ) -> Result<PreparedCommand, ProcessError> {
        if request.limits.memory_bytes.is_some()
            || request.limits.cpu_time_ms.is_some()
            || request.limits.process_count.is_some()
        {
            return Err(ProcessError::Unsupported);
        }
        let cwd = filesystem
            .resolve_command_cwd(
                request
                    .cwd
                    .as_ref()
                    .unwrap_or(&self.inner.working_directory),
            )
            .map_err(map_path_error)?;
        let ResolvedCommand {
            executable,
            arguments,
            roots,
            mut environment,
        } = self.resolve_command(filesystem, &request.command)?;
        validate_arguments(
            &arguments,
            self.inner.command.max_arguments,
            self.inner.command.max_argument_bytes,
        )?;
        if !self.inner.command.full_control && !roots.is_empty() {
            let path = std::env::join_paths(&roots).map_err(|_| ProcessError::Invalid)?;
            environment.insert("PATH".to_owned(), path.to_string_lossy().into_owned());
        }
        apply_request_environment(
            &mut environment,
            request,
            self.inner.command.max_environment_entries,
            self.inner.command.max_environment_bytes,
        )?;
        validate_final_environment(
            &environment,
            self.inner.command.max_environment_entries,
            self.inner.command.max_environment_bytes,
        )?;

        let wall_time_ms = request
            .limits
            .wall_time_ms
            .unwrap_or(self.inner.max_operation_duration.as_millis() as u64);
        if wall_time_ms == 0 || wall_time_ms > self.inner.max_operation_duration.as_millis() as u64
        {
            return Err(ProcessError::Invalid);
        }
        let stdin_limit = request
            .limits
            .stdin_bytes
            .unwrap_or(self.inner.max_stdin_bytes)
            .min(self.inner.max_stdin_bytes);
        if stdin_limit == 0 {
            return Err(ProcessError::Invalid);
        }
        let initial_stdin = request
            .initial_stdin
            .as_ref()
            .map(decode_eip_bytes)
            .transpose()?;
        if initial_stdin.as_ref().map_or(0, Vec::len) as u64 > stdin_limit {
            return Err(ProcessError::Invalid);
        }
        Ok(PreparedCommand {
            plan: LaunchPlan {
                drop_supervisor_capabilities: self.inner.command.drop_supervisor_capabilities,
                executable,
                arguments,
                cwd: cwd.native_path,
                environment,
                initial_stdin: initial_stdin
                    .map(|bytes| base64::engine::general_purpose::STANDARD.encode(bytes)),
                keep_stdin_open: request.keep_stdin_open,
                wall_time_ms,
            },
            stdin_limit,
        })
    }

    fn resolve_command(
        &self,
        filesystem: &Arc<DeviceFilesystem>,
        command: &CommandSpec,
    ) -> Result<ResolvedCommand, ProcessError> {
        match command {
            CommandSpec::Argv(argv) => {
                let executable = match &argv.executable_spec {
                    eip::ExecutableSpec::Name(selector) => {
                        if !valid_bare_executable_name(&selector.name) {
                            return Err(ProcessError::Invalid);
                        }
                        resolve_bare_executable(
                            &selector.name,
                            &self.inner.command.trusted_executable_roots,
                            self.inner.command.full_control,
                        )?
                    }
                    eip::ExecutableSpec::Path(selector) => filesystem
                        .resolve_executable(&selector.path)
                        .map_err(map_path_error)?,
                };
                Ok(ResolvedCommand {
                    executable,
                    arguments: argv.arguments.clone(),
                    roots: self.inner.command.trusted_executable_roots.clone(),

                    environment: self.inner.command.base_environment.clone(),
                })
            }
            CommandSpec::Shell(shell) => {
                let profile = self
                    .inner
                    .command
                    .shell_profiles
                    .iter()
                    .find(|profile| profile.profile_id == shell.profile_id)
                    .ok_or(ProcessError::Unsupported)?;
                if shell.script.contains('\0')
                    || shell.script.len() as u64 > profile.max_script_bytes
                    || (shell.login && !profile.allow_login_mode)
                {
                    return Err(ProcessError::Invalid);
                }
                let mut arguments = profile.fixed_arguments.clone();
                if shell.login {
                    arguments.insert(0, "-l".to_owned());
                }
                let script = shell.script.clone();
                #[cfg(windows)]
                let script = if self.inner.command.full_control {
                    format!(
                        "$OutputEncoding = [Console]::InputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); {script}"
                    )
                } else {
                    script
                };
                arguments.push(script);
                let mut environment = self.inner.command.base_environment.clone();
                environment.extend(profile.safe_base_environment.clone());
                Ok(ResolvedCommand {
                    executable: profile.native_executable.clone(),
                    arguments,
                    roots: profile.executable_search_roots.clone(),

                    environment,
                })
            }
        }
    }

    pub(crate) fn inspect(&self, handle: &ProcessHandle) -> Result<ProcessInfo, ProcessError> {
        let record = self.record(handle)?;
        record.info(&self.inner)
    }

    pub(crate) async fn wait(
        &self,
        handle: &ProcessHandle,
        condition: ProcessWaitCondition,
        deadline: Instant,
    ) -> Result<ProcessInfo, ProcessError> {
        let record = self.record(handle)?;
        loop {
            let notified = record.changed.notified();
            let info = record.info(&self.inner)?;
            if wait_satisfied(&info.status, condition) {
                return Ok(info);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(ProcessError::Timeout);
            }
            tokio::time::timeout(remaining, notified)
                .await
                .map_err(|_| ProcessError::Timeout)?;
        }
    }

    pub(crate) async fn write_stdin(
        &self,
        handle: &ProcessHandle,
        data: &EncodedBytes,
        close_after_write: bool,
    ) -> Result<(u64, bool), ProcessError> {
        let record = self.record(handle)?;
        let bytes = decode_eip_bytes(data)?;
        let _operation = record.stdin_operation.lock().await;
        let previous_ack = {
            let state = record.record_state();
            if !state.stdin_open {
                return Err(ProcessError::Conflict);
            }
            if state.stdin_bytes.saturating_add(bytes.len() as u64) > state.stdin_limit {
                return Err(ProcessError::OutputLimit);
            }
            state.stdin_ack
        };
        {
            let mut control = record.control.lock().await;
            supervisor::write_request(
                &mut *control,
                &SupervisorRequest::WriteStdin {
                    data: base64::engine::general_purpose::STANDARD.encode(&bytes),
                    close_after_write,
                },
            )
            .await
            .map_err(|_| ProcessError::Internal)?;
        }
        self.await_stdin_ack(&record, previous_ack).await
    }

    pub(crate) async fn close_stdin(&self, handle: &ProcessHandle) -> Result<bool, ProcessError> {
        let record = self.record(handle)?;
        let _operation = record.stdin_operation.lock().await;
        let previous_ack = {
            let state = record.record_state();
            if !state.stdin_open {
                return Ok(false);
            }
            state.stdin_ack
        };
        {
            let mut control = record.control.lock().await;
            supervisor::write_request(&mut *control, &SupervisorRequest::CloseStdin)
                .await
                .map_err(|_| ProcessError::Internal)?;
        }
        self.await_stdin_ack(&record, previous_ack).await?;
        Ok(false)
    }

    pub(crate) async fn signal(
        &self,
        handle: &ProcessHandle,
        signal: RequestedProcessSignal,
    ) -> Result<(bool, ProcessInfo), ProcessError> {
        let record = self.record(handle)?;
        let _operation = record.signal_operation.lock().await;
        let previous_ack = {
            let state = record.record_state();
            if is_terminal_phase(state.status.phase) {
                drop(state);
                return Ok((false, record.info(&self.inner)?));
            }
            state.signal_ack
        };
        let signal = match signal {
            RequestedProcessSignal::Interrupt => ControlSignal::Interrupt,
            RequestedProcessSignal::Terminate => ControlSignal::Terminate,
        };
        {
            let mut control = record.control.lock().await;
            supervisor::write_request(&mut *control, &SupervisorRequest::Signal { signal })
                .await
                .map_err(|_| ProcessError::Internal)?;
        }
        let accepted = self.await_signal_ack(&record, previous_ack).await?;
        Ok((accepted, record.info(&self.inner)?))
    }

    pub(crate) async fn kill(
        &self,
        handle: &ProcessHandle,
        reason: StopReason,
        deadline: Instant,
    ) -> Result<ProcessInfo, ProcessError> {
        let record = self.record(handle)?;
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero()
            || !matches!(
                tokio::time::timeout(remaining, async {
                    if !record.cleanup_terminal() {
                        let mut control = record.control.lock().await;
                        supervisor::write_request(
                            &mut *control,
                            &SupervisorRequest::Kill { reason },
                        )
                        .await
                        .map_err(|_| ProcessError::Internal)?;
                    }
                    Ok::<(), ProcessError>(())
                })
                .await,
                Ok(Ok(()))
            )
        {
            return Err(ProcessError::UnknownOutcome);
        }
        let process = self
            .wait(handle, ProcessWaitCondition::TreeCleaned, deadline)
            .await
            .map_err(|_| ProcessError::UnknownOutcome)?;
        if process.status.cleanup == CleanupOutcome::Failed {
            Err(ProcessError::CleanupFailed)
        } else {
            Ok(process)
        }
    }

    pub(crate) fn release(&self, handle: &ProcessHandle) -> Result<bool, ProcessError> {
        let record = {
            let mut state = self.state();
            let Some(record) = state.records.get(&handle.0) else {
                return Err(ProcessError::NotFound);
            };
            if !record.fully_cleaned() {
                return Err(ProcessError::Conflict);
            }
            let record = Arc::clone(record);
            state.records.remove(&handle.0);
            record
        };
        record.detach_output();
        Ok(true)
    }

    pub(crate) async fn wait_started(
        &self,
        started: &StartedProcess,
        condition: ProcessWaitCondition,
        deadline: Instant,
    ) -> Result<ProcessInfo, ProcessError> {
        loop {
            let notified = started.record.changed.notified();
            let info = started.record.info(&self.inner)?;
            if wait_satisfied(&info.status, condition) {
                return Ok(info);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(ProcessError::Timeout);
            }
            tokio::time::timeout(remaining, notified)
                .await
                .map_err(|_| ProcessError::Timeout)?;
        }
    }

    pub(crate) async fn stop_started(
        &self,
        started: &StartedProcess,
        reason: StopReason,
    ) -> Result<(), ProcessError> {
        if started.record.cleanup_terminal() {
            return Ok(());
        }
        let mut control = started.record.control.lock().await;
        supervisor::write_request(&mut *control, &SupervisorRequest::Kill { reason })
            .await
            .map_err(|_| ProcessError::Internal)
    }

    pub(crate) async fn interrupt_started(
        &self,
        started: &StartedProcess,
        reason: StopReason,
        deadline: Instant,
    ) -> Result<ProcessInfo, ProcessError> {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero()
            || !matches!(
                tokio::time::timeout(remaining, self.stop_started(started, reason)).await,
                Ok(Ok(()))
            )
        {
            return Err(ProcessError::UnknownOutcome);
        }
        match self
            .wait_started(started, ProcessWaitCondition::TreeCleaned, deadline)
            .await
        {
            Ok(process)
                if matches!(
                    process.status.cleanup,
                    CleanupOutcome::Complete | CleanupOutcome::ResidualConfined
                ) =>
            {
                Ok(process)
            }
            Ok(_) | Err(_) => Err(ProcessError::UnknownOutcome),
        }
    }

    async fn finish_interrupted_start(
        &self,
        started: &StartedProcess,
        error: ProcessError,
    ) -> StartFailure {
        let (reason, reported_error) = match error {
            ProcessError::Cancelled | ProcessError::PreDispatchCancelled => {
                (StopReason::Cancelled, ProcessError::Cancelled)
            }
            ProcessError::Timeout | ProcessError::PreDispatchTimeout => {
                (StopReason::Timeout, ProcessError::Timeout)
            }
            _ => (StopReason::Shutdown, error),
        };
        match self
            .interrupt_started(started, reason, Instant::now() + Duration::from_secs(3))
            .await
        {
            Ok(_) => self.release_or_preserve_start_failure(started, reported_error),
            Err(_) => StartFailure::with_evidence(ProcessError::UnknownOutcome, started),
        }
    }

    fn release_or_preserve_start_failure(
        &self,
        started: &StartedProcess,
        error: ProcessError,
    ) -> StartFailure {
        if self.prepare_started_release(started).is_some() {
            StartFailure::without_evidence(error)
        } else {
            StartFailure::with_evidence(error, started)
        }
    }

    pub(crate) fn release_started(&self, started: &StartedProcess) {
        drop(self.prepare_started_release(started));
    }

    pub(crate) fn prepare_started_release(
        &self,
        started: &StartedProcess,
    ) -> Option<StartedRecordRelease> {
        if !started.record.fully_cleaned() {
            return None;
        }
        let record = {
            let mut state = self.state();
            state.records.remove(&started.record.handle.0)
        }?;
        Some(StartedRecordRelease {
            record: Some(record),
            retention: self.inner.retention.clone(),
        })
    }

    pub(crate) fn under_pressure(&self) -> bool {
        let state = self.state();
        state.records.len().saturating_add(state.starts_in_progress) >= self.inner.max_records
            || self.inner.capacity.under_pressure()
    }

    /// Called with the Session history gate held exclusively. Native owners keep
    /// their record Arc, so only completed, unobserved groups can be reclaimed.
    pub(crate) async fn collect(&self, ttl: Duration, pressure: bool) -> Vec<(String, String)> {
        let candidates = {
            let state = self.state();
            let mut candidates: Vec<_> = state
                .records
                .iter()
                .filter_map(|(handle, record)| {
                    if Arc::strong_count(record) != 1 {
                        return None;
                    }
                    let status = record.record_state();
                    let access = status
                        .last_access
                        .max(record.stdout.output.last_access())
                        .max(record.stderr.output.last_access());
                    (is_terminal_phase(status.status.phase)
                        && status.status.cleanup == CleanupOutcome::Complete
                        && (pressure || access.elapsed() >= ttl))
                        .then(|| (access, handle.clone()))
                })
                .collect();
            candidates.sort_by_key(|(access, _)| *access);
            candidates
        };
        let mut released = Vec::new();
        for (_, handle) in candidates {
            let record = {
                let state = self.state();
                let Some(record) = state.records.get(&handle) else {
                    continue;
                };
                if Arc::strong_count(record) != 1 {
                    continue;
                }
                record.clone()
            };
            record.detach_output();
            let mut complete = true;
            for output in [&record.stdout.output, &record.stderr.output] {
                match self
                    .inner
                    .retention
                    .release_reference(output.reference())
                    .await
                {
                    Ok(_) | Err(RetentionError::InvalidSelector) => {}
                    Err(_) => complete = false,
                }
            }
            if complete {
                self.state().records.remove(&handle);
                released.push(("process".to_owned(), handle));
                released.push((
                    "output".to_owned(),
                    record.stdout.output.reference().0.clone(),
                ));
                released.push((
                    "output".to_owned(),
                    record.stderr.output.reference().0.clone(),
                ));
            }
        }
        released
    }

    pub(crate) async fn drain(&self, budget: Duration) -> bool {
        let deadline = Instant::now() + budget;
        let records = {
            let mut state = self.state();
            state.draining = true;
            state.records.values().cloned().collect::<Vec<_>>()
        };
        for record in &records {
            if !record.cleanup_terminal() {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero()
                    || tokio::time::timeout(remaining, async {
                        let mut control = record.control.lock().await;
                        supervisor::write_request(
                            &mut *control,
                            &SupervisorRequest::Kill {
                                reason: StopReason::Shutdown,
                            },
                        )
                        .await
                    })
                    .await
                    .is_err()
                {
                    return false;
                }
            }
        }
        for record in records {
            while !record.cleanup_terminal() {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero()
                    || tokio::time::timeout(remaining, record.changed.notified())
                        .await
                        .is_err()
                {
                    return false;
                }
            }
            if !record.fully_cleaned() {
                return false;
            }
        }
        self.state().residual_active == 0
    }

    async fn await_stdin_ack(
        &self,
        record: &Arc<ProcessRecord>,
        previous: u64,
    ) -> Result<(u64, bool), ProcessError> {
        let deadline = Instant::now() + PROCESS_CONTROL_TIMEOUT;
        loop {
            let notified = record.changed.notified();
            {
                let state = record.record_state();
                if state.stdin_ack != previous {
                    return Ok(state.stdin_result);
                }
                if is_terminal_phase(state.status.phase) {
                    return Err(ProcessError::UnknownOutcome);
                }
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            tokio::time::timeout(remaining, notified)
                .await
                .map_err(|_| ProcessError::UnknownOutcome)?;
        }
    }

    async fn await_signal_ack(
        &self,
        record: &Arc<ProcessRecord>,
        previous: u64,
    ) -> Result<bool, ProcessError> {
        let deadline = Instant::now() + PROCESS_CONTROL_TIMEOUT;
        loop {
            let notified = record.changed.notified();
            {
                let state = record.record_state();
                if state.signal_ack != previous {
                    return Ok(state.signal_result);
                }
                if is_terminal_phase(state.status.phase) {
                    return Err(ProcessError::UnknownOutcome);
                }
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            tokio::time::timeout(remaining, notified)
                .await
                .map_err(|_| ProcessError::UnknownOutcome)?;
        }
    }

    fn record(&self, handle: &ProcessHandle) -> Result<Arc<ProcessRecord>, ProcessError> {
        let state = self.state();
        let record = state
            .records
            .get(&handle.0)
            .filter(|record| record.exposed)
            .cloned()
            .ok_or(ProcessError::NotFound)?;
        Ok(record)
    }

    fn reserve_start(&self) -> Result<StartReservation, ProcessError> {
        let mut state = self.state();
        if state.draining
            || state
                .active
                .saturating_add(state.residual_active)
                .saturating_add(state.starts_in_progress)
                >= self.inner.max_active
        {
            return Err(ProcessError::Busy);
        }
        if state.records.len().saturating_add(state.starts_in_progress) >= self.inner.max_records {
            return Err(ProcessError::Busy);
        }
        let capacity = self.inner.capacity.reserve().ok_or(ProcessError::Busy)?;
        state.starts_in_progress = state
            .starts_in_progress
            .checked_add(1)
            .ok_or(ProcessError::Internal)?;
        Ok(StartReservation {
            manager: Arc::clone(&self.inner),
            capacity: Some(capacity),
            active: true,
        })
    }

    async fn rollback_pre_dispatch_start(&self, handle: &ProcessHandle) {
        let record = {
            let mut state = self.state();
            state.active = state.active.saturating_sub(1);
            state.records.remove(&handle.0)
        };
        if let Some(record) = record {
            self.inner
                .retention
                .abort_pair([&record.stdout.output, &record.stderr.output])
                .await;
        }
    }

    fn state(&self) -> std::sync::MutexGuard<'_, ManagerState> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }
}

impl From<ProcessError> for StartFailure {
    fn from(error: ProcessError) -> Self {
        Self::without_evidence(error)
    }
}

impl StartFailure {
    fn without_evidence(error: ProcessError) -> Self {
        Self {
            error,
            started: None,
        }
    }

    fn with_evidence(error: ProcessError, started: &StartedProcess) -> Self {
        Self {
            error,
            started: Some(started.clone()),
        }
    }
}

impl StartReservation {
    fn commit(&mut self) {
        if self.active {
            let mut state = self
                .manager
                .state
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            state.starts_in_progress = state.starts_in_progress.saturating_sub(1);
            self.active = false;
        }
    }
}

impl Drop for StartReservation {
    fn drop(&mut self) {
        self.commit();
    }
}

impl StartedProcess {
    pub(crate) fn info(&self, manager: &ExecutionManager) -> Result<ProcessInfo, ProcessError> {
        self.record.info(&manager.inner)
    }
}

impl StartedRecordRelease {
    pub(crate) fn preserve_output(mut self) {
        if let Some(record) = self.record.take() {
            record.detach_output();
        }
    }
}

impl Drop for StartedRecordRelease {
    fn drop(&mut self) {
        let Some(record) = self.record.take() else {
            return;
        };
        let retention = self.retention.clone();
        let stdout = record.stdout.output.clone();
        let stderr = record.stderr.output.clone();
        if let Ok(runtime) = tokio::runtime::Handle::try_current() {
            runtime.spawn(async move {
                retention.abort_pair([&stdout, &stderr]).await;
            });
        }
    }
}

impl ProcessRecord {
    fn info(&self, manager: &ExecutionInner) -> Result<ProcessInfo, ProcessError> {
        let mut state = self.record_state();
        state.last_access = Instant::now();
        Ok(ProcessInfo {
            handle: self.handle.clone(),
            device_id: manager.device_id.clone(),
            session_id: manager.session_id.clone(),
            generation: manager.generation,
            status: state.status.clone(),
            stdin_open: state.stdin_open,
            output: ProcessOutput {
                stdout: self.stdout.snapshot()?,
                stderr: self.stderr.snapshot()?,
            },
        })
    }

    fn record_state(&self) -> std::sync::MutexGuard<'_, RecordState> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn cleanup_terminal(&self) -> bool {
        let state = self.record_state();
        is_terminal_phase(state.status.phase) && state.status.cleanup != CleanupOutcome::Pending
    }

    fn fully_cleaned(&self) -> bool {
        let state = self.record_state();
        is_terminal_phase(state.status.phase)
            && matches!(
                state.status.cleanup,
                CleanupOutcome::Complete | CleanupOutcome::ResidualConfined
            )
    }

    fn detach_output(&self) {
        self.stdout.output.detach();
        self.stderr.output.detach();
    }
}

impl StreamOutput {
    fn new(output: LiveOutput) -> Self {
        Self { output }
    }

    async fn append(&self, bytes: &[u8]) -> AppendOutcome {
        self.output.append(bytes).await
    }

    fn mark_incomplete(&self) {
        self.output.mark_incomplete();
    }

    async fn complete(&self) {
        self.output.complete().await;
    }

    fn snapshot(&self) -> Result<eip::OutputInfo, ProcessError> {
        self.output.snapshot().map_err(|error| match error {
            RetentionError::InvalidSelector => ProcessError::NotFound,
            _ => ProcessError::Internal,
        })
    }
}

async fn run_event_pump(
    mut supervisor_process: Child,
    mut reader: BufReader<tokio::process::ChildStdout>,
    record: Arc<ProcessRecord>,
    confirmation: oneshot::Sender<Result<(), ProcessError>>,
) {
    let mut confirmation = Some(confirmation);
    let mut saw_cleaned = false;
    loop {
        let event = match supervisor::read_event(&mut reader).await {
            Ok(Some(event)) => event,
            Ok(None) | Err(_) => break,
        };
        match event {
            SupervisorEvent::Output { stream, data } => {
                let bytes = match base64::engine::general_purpose::STANDARD.decode(data) {
                    Ok(bytes) => bytes,
                    Err(_) => break,
                };
                let output = match stream {
                    OutputStream::Stdout => &record.stdout,
                    OutputStream::Stderr => &record.stderr,
                };
                match output.append(&bytes).await {
                    AppendOutcome::LimitCrossed => {
                        mark_output_limit(&record);
                        let control = Arc::clone(&record.control);
                        tokio::spawn(async move {
                            let mut control = control.lock().await;
                            let _ = supervisor::write_request(
                                &mut *control,
                                &SupervisorRequest::Kill {
                                    reason: StopReason::OutputLimit,
                                },
                            )
                            .await;
                        });
                    }
                    AppendOutcome::Complete => {}
                    AppendOutcome::WriteFailed => {
                        mark_output_failure(&record);
                        let control = Arc::clone(&record.control);
                        tokio::spawn(async move {
                            let mut control = control.lock().await;
                            let _ = supervisor::write_request(
                                &mut *control,
                                &SupervisorRequest::Kill {
                                    reason: StopReason::Shutdown,
                                },
                            )
                            .await;
                        });
                    }
                }
            }
            SupervisorEvent::StreamClosed { stream } => match stream {
                OutputStream::Stdout => record.stdout.complete().await,
                OutputStream::Stderr => record.stderr.complete().await,
            },
            SupervisorEvent::StdinResult {
                accepted_bytes,
                open,
            } => {
                let mut state = record.record_state();
                state.stdin_open = open;
                state.stdin_bytes = state.stdin_bytes.saturating_add(accepted_bytes);
                state.stdin_result = (accepted_bytes, open);
                state.stdin_ack = state.stdin_ack.wrapping_add(1);
            }
            SupervisorEvent::SignalResult { accepted } => {
                let mut state = record.record_state();
                state.signal_result = accepted;
                state.signal_ack = state.signal_ack.wrapping_add(1);
            }
            SupervisorEvent::Started {
                stdin_open,
                accepted_stdin_bytes,
                initial_stdin_complete,
            } => {
                {
                    let mut state = record.record_state();
                    if state.status.phase == ProcessPhase::Starting {
                        state.status.phase = ProcessPhase::Running;
                        state.status.started_at = Some(chrono::Utc::now());
                        state.stdin_open = stdin_open;
                        state.stdin_bytes = accepted_stdin_bytes;
                    }
                }
                if let Some(confirmation) = confirmation.take() {
                    let result = if initial_stdin_complete {
                        Ok(())
                    } else {
                        Err(ProcessError::UnknownOutcome)
                    };
                    let _ = confirmation.send(result);
                }
            }
            SupervisorEvent::StartFailed { .. } => {
                {
                    let mut state = record.record_state();
                    state.status.phase = ProcessPhase::Failed;
                    state.status.termination_reason = Some(TerminationReason::BackendLost);
                    state.status.ended_at = Some(chrono::Utc::now());
                    state.status.cleanup = CleanupOutcome::Complete;
                    state.stdin_open = false;
                }
                record.stdout.complete().await;
                record.stderr.complete().await;
                release_active_and_mark_terminal(&record);
                saw_cleaned = true;
                record.changed.notify_waiters();
                if let Some(confirmation) = confirmation.take() {
                    let _ = confirmation.send(Err(ProcessError::StartFailed));
                }
                break;
            }
            SupervisorEvent::Terminal {
                exit_code,
                signal,
                native_signaled,
                stop_reason,
            } => {
                let mut state = record.record_state();
                apply_terminal_status(&mut state, exit_code, signal, native_signaled, stop_reason);
                state.stdin_open = false;
                if !state.terminal_recorded {
                    state.terminal_recorded = true;
                }
            }
            SupervisorEvent::Cleaned {
                cleanup,
                output_complete,
            } => {
                {
                    let mut state = record.record_state();
                    state.status.cleanup = match cleanup {
                        SupervisorCleanup::Complete => CleanupOutcome::Complete,
                        SupervisorCleanup::ResidualConfined => CleanupOutcome::ResidualConfined,
                        SupervisorCleanup::Failed => CleanupOutcome::Failed,
                    };
                    if !output_complete {
                        state.status.phase = ProcessPhase::Failed;
                        state.status.termination_reason = Some(TerminationReason::BackendLost);
                        state.status.exit_code = None;
                        state.status.signal = None;
                    }
                    state.stdin_open = false;
                }
                if !output_complete {
                    record.stdout.mark_incomplete();
                    record.stderr.mark_incomplete();
                }
                record.stdout.complete().await;
                record.stderr.complete().await;
                release_active_and_mark_terminal(&record);
                saw_cleaned = true;
                record.changed.notify_waiters();
                break;
            }
            SupervisorEvent::Booted { .. }
            | SupervisorEvent::Prepared
            | SupervisorEvent::ProtocolError { .. } => break,
        }
        record.changed.notify_waiters();
    }
    let mut supervisor_reaped = false;
    if !saw_cleaned {
        record.stdout.mark_incomplete();
        record.stderr.mark_incomplete();
        record.stdout.complete().await;
        record.stderr.complete().await;
        {
            let mut state = record.record_state();
            if !is_terminal_phase(state.status.phase) {
                state.status.phase = ProcessPhase::Failed;
                state.status.termination_reason = Some(TerminationReason::BackendLost);
                state.status.ended_at = Some(chrono::Utc::now());
            }
            state.status.cleanup = CleanupOutcome::Failed;
            state.stdin_open = false;
        }
        release_active_and_mark_terminal(&record);
        record.changed.notify_waiters();
        {
            let mut control = record.control.lock().await;
            let _ = supervisor::write_request(
                &mut *control,
                &SupervisorRequest::Kill {
                    reason: StopReason::Shutdown,
                },
            )
            .await;
        }
        supervisor_reaped =
            tokio::time::timeout(PROCESS_CONTROL_TIMEOUT, supervisor_process.wait())
                .await
                .is_ok();
    }
    if let Some(confirmation) = confirmation.take() {
        let _ = confirmation.send(Err(ProcessError::UnknownOutcome));
    }
    if !supervisor_reaped {
        let _ = supervisor_process.start_kill();
        let _ = supervisor_process.wait().await;
    }
}

fn release_active_and_mark_terminal(record: &ProcessRecord) {
    if !record.fully_cleaned() {
        return;
    }
    let Some(manager) = record.manager.upgrade() else {
        return;
    };
    let release = {
        let mut state = record.record_state();
        if state.active_released {
            None
        } else {
            state.active_released = true;
            state.last_access = Instant::now();
            let residual = state.status.cleanup == CleanupOutcome::ResidualConfined;
            if !residual {
                state.capacity.active.take();
            }
            Some(residual)
        }
    };
    if let Some(residual) = release {
        let mut state = manager.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.active = state.active.saturating_sub(1);
        if residual {
            state.residual_active = state.residual_active.saturating_add(1);
        }
    }
}

fn mark_output_limit(record: &ProcessRecord) {
    let mut state = record.record_state();
    state.output_limit_crossed = true;
    if is_terminal_phase(state.status.phase)
        && state.status.termination_reason != Some(TerminationReason::BackendLost)
    {
        state.status.phase = ProcessPhase::Failed;
        state.status.termination_reason = Some(TerminationReason::OutputLimit);
        state.status.exit_code = None;
        state.status.signal = None;
    }
}

fn mark_output_failure(record: &ProcessRecord) {
    let mut state = record.record_state();
    state.output_write_failed = true;
    state.status.phase = ProcessPhase::Failed;
    state.status.termination_reason = Some(TerminationReason::BackendLost);
    state.status.exit_code = None;
    state.status.signal = None;
}

fn apply_terminal_status(
    state: &mut RecordState,
    exit_code: Option<i32>,
    signal: Option<ControlSignal>,
    native_signaled: bool,
    stop_reason: Option<StopReason>,
) {
    let output_limited = state.output_limit_crossed;
    let output_write_failed = state.output_write_failed;
    state.status.ended_at = Some(chrono::Utc::now());
    state.status.exit_code = exit_code;
    state.status.cleanup = CleanupOutcome::Pending;
    if output_write_failed {
        state.status.phase = ProcessPhase::Failed;
        state.status.termination_reason = Some(TerminationReason::BackendLost);
        state.status.exit_code = None;
        state.status.signal = None;
        return;
    }
    if output_limited {
        state.status.phase = ProcessPhase::Failed;
        state.status.termination_reason = Some(TerminationReason::OutputLimit);
        state.status.exit_code = None;
        state.status.signal = None;
        return;
    }
    match stop_reason {
        Some(StopReason::Timeout) => {
            state.status.phase = ProcessPhase::TimedOut;
            state.status.termination_reason = Some(TerminationReason::Timeout);
            state.status.exit_code = None;
        }
        Some(StopReason::Cancelled) => {
            state.status.phase = ProcessPhase::Cancelled;
            state.status.termination_reason = Some(TerminationReason::Cancelled);
            state.status.exit_code = None;
        }
        Some(StopReason::OutputLimit) => {
            state.status.phase = ProcessPhase::Failed;
            state.status.termination_reason = Some(TerminationReason::OutputLimit);
            state.status.exit_code = None;
        }
        Some(StopReason::Kill | StopReason::Shutdown) => {
            state.status.phase = ProcessPhase::Signaled;
            state.status.termination_reason = Some(TerminationReason::Signal);
            state.status.signal = Some(ProcessSignal::Kill);
            state.status.exit_code = None;
        }
        None if native_signaled => {
            state.status.phase = ProcessPhase::Signaled;
            state.status.termination_reason = Some(TerminationReason::Signal);
            state.status.signal = signal.map(|signal| match signal {
                ControlSignal::Interrupt => ProcessSignal::Interrupt,
                ControlSignal::Terminate => ProcessSignal::Terminate,
                ControlSignal::Kill => ProcessSignal::Kill,
            });
            state.status.exit_code = None;
        }
        None => {
            state.status.phase = ProcessPhase::Exited;
            state.status.termination_reason = Some(TerminationReason::Exit);
        }
    }
}

fn apply_request_environment(
    environment: &mut BTreeMap<String, String>,
    request: &CommandRequest,
    max_entries: usize,
    max_bytes: usize,
) -> Result<(), ProcessError> {
    if request
        .environment
        .set
        .len()
        .saturating_add(request.environment.unset.len())
        > max_entries
        || request
            .environment
            .set
            .iter()
            .map(|(name, value)| name.len().saturating_add(value.len()))
            .sum::<usize>()
            > max_bytes
    {
        return Err(ProcessError::Invalid);
    }
    for name in &request.environment.unset {
        validate_request_environment_name(name)?;
        environment.remove(name);
    }
    for (name, value) in &request.environment.set {
        validate_request_environment_name(name)?;
        if value.contains('\0') {
            return Err(ProcessError::Invalid);
        }
        environment.insert(name.clone(), value.clone());
    }
    Ok(())
}

fn validate_final_environment(
    environment: &BTreeMap<String, String>,
    max_entries: usize,
    max_bytes: usize,
) -> Result<(), ProcessError> {
    if environment.len() > max_entries
        || environment
            .iter()
            .map(|(name, value)| name.len().saturating_add(value.len()))
            .sum::<usize>()
            > max_bytes
    {
        return Err(ProcessError::Invalid);
    }
    Ok(())
}

fn validate_request_environment_name(name: &str) -> Result<(), ProcessError> {
    if !valid_environment_name(name) || reserved_environment_name(name) {
        return Err(ProcessError::Invalid);
    }
    Ok(())
}

fn validate_arguments(
    arguments: &[String],
    max_arguments: usize,
    max_bytes: usize,
) -> Result<(), ProcessError> {
    if arguments.len() > max_arguments
        || arguments.iter().map(String::len).sum::<usize>() > max_bytes
        || arguments.iter().any(|argument| argument.contains('\0'))
    {
        return Err(ProcessError::Invalid);
    }
    Ok(())
}

fn valid_bare_executable_name(name: &str) -> bool {
    !name.is_empty()
        && !matches!(name, "." | "..")
        && !name
            .chars()
            .any(|character| matches!(character, '\0' | '/' | '\\' | ':'))
}

fn resolve_bare_executable(
    name: &str,
    roots: &[PathBuf],
    full_control: bool,
) -> Result<PathBuf, ProcessError> {
    for root in roots {
        let candidate = root.join(name);
        #[cfg(windows)]
        let candidate = if full_control && candidate.extension().is_none() {
            candidate.with_extension("exe")
        } else {
            candidate
        };
        let Ok(canonical) = std::fs::canonicalize(&candidate) else {
            continue;
        };
        if (!full_control && !canonical.starts_with(root)) || !canonical.is_file() {
            continue;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if std::fs::metadata(&canonical)
                .map_err(|_| ProcessError::Denied)?
                .permissions()
                .mode()
                & 0o111
                == 0
            {
                continue;
            }
        }
        return Ok(canonical);
    }
    Err(ProcessError::Denied)
}

fn decode_eip_bytes(value: &EncodedBytes) -> Result<Vec<u8>, ProcessError> {
    if value.encoding != "base64" {
        return Err(ProcessError::Invalid);
    }
    base64::engine::general_purpose::STANDARD_NO_PAD
        .decode(&value.data)
        .map_err(|_| ProcessError::Invalid)
}

fn wait_satisfied(status: &ProcessStatus, condition: ProcessWaitCondition) -> bool {
    match condition {
        ProcessWaitCondition::InitialTerminal => is_terminal_phase(status.phase),
        ProcessWaitCondition::TreeCleaned => {
            is_terminal_phase(status.phase) && status.cleanup != CleanupOutcome::Pending
        }
    }
}

fn is_terminal_phase(phase: ProcessPhase) -> bool {
    !matches!(phase, ProcessPhase::Starting | ProcessPhase::Running)
}

fn map_path_error(error: PathError) -> ProcessError {
    match error {
        PathError::Unsupported => ProcessError::Unsupported,
        PathError::Quota => ProcessError::Busy,
        PathError::Denied | PathError::NotFound | PathError::NotRegular => ProcessError::Denied,
        PathError::UnknownOutcome | PathError::Io | PathError::Internal => ProcessError::Internal,
        PathError::Invalid | PathError::AlreadyExists => ProcessError::Invalid,
    }
}

#[cfg(test)]
mod tests {
    use super::valid_bare_executable_name;

    #[test]
    fn executable_names_are_single_portable_path_components() {
        assert!(valid_bare_executable_name("python3"));
        assert!(valid_bare_executable_name("tool-name.exe"));
        for invalid in [
            "",
            ".",
            "..",
            "subdir/tool",
            "subdir\\tool",
            "../tool",
            "C:tool.exe",
            "C:\\tool.exe",
            "/bin/tool",
            "tool\0suffix",
        ] {
            assert!(!valid_bare_executable_name(invalid), "accepted {invalid:?}");
        }
    }
}
