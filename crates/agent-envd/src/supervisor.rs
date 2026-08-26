use std::{collections::BTreeMap, io, path::PathBuf, process::Stdio, time::Duration};

use base64::Engine as _;
use serde::{Deserialize, Serialize};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, Command},
    sync::mpsc,
};

use crate::isolation::LaunchIsolation;

const PROTOCOL_VERSION: u32 = 1;
const MAX_PROTOCOL_LINE_BYTES: usize = 32 * 1024 * 1024;
const CLEANUP_GRACE: Duration = Duration::from_secs(2);
const OUTPUT_CHUNK_BYTES: usize = 16 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct LaunchPlan {
    pub(crate) executable: PathBuf,
    pub(crate) arguments: Vec<String>,
    pub(crate) cwd: PathBuf,
    pub(crate) environment: BTreeMap<String, String>,
    pub(crate) initial_stdin: Option<String>,
    pub(crate) keep_stdin_open: bool,
    pub(crate) wall_time_ms: u64,
    pub(crate) isolation: LaunchIsolation,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ControlSignal {
    Interrupt,
    Terminate,
    Kill,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum StopReason {
    Kill,
    Cancelled,
    Timeout,
    OutputLimit,
    Shutdown,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum SupervisorRequest {
    Prepare {
        version: u32,
        plan: LaunchPlan,
    },
    Start,
    WriteStdin {
        data: String,
        close_after_write: bool,
    },
    CloseStdin,
    Signal {
        signal: ControlSignal,
    },
    Kill {
        reason: StopReason,
    },
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum SupervisorEvent {
    Booted {
        version: u32,
    },
    Prepared,
    Started {
        stdin_open: bool,
        accepted_stdin_bytes: u64,
        initial_stdin_complete: bool,
    },
    StartFailed {
        message: String,
    },
    Output {
        stream: OutputStream,
        data: String,
    },
    StreamClosed {
        stream: OutputStream,
    },
    StdinResult {
        accepted_bytes: u64,
        open: bool,
    },
    SignalResult {
        accepted: bool,
    },
    Terminal {
        exit_code: Option<i32>,
        signal: Option<ControlSignal>,
        native_signaled: bool,
        stop_reason: Option<StopReason>,
    },
    Cleaned {
        cleanup: SupervisorCleanup,
        output_complete: bool,
    },
    ProtocolError {
        message: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SupervisorCleanup {
    Complete,
    ResidualConfined,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum OutputStream {
    Stdout,
    Stderr,
}

enum StreamEvent {
    Data(OutputStream, Vec<u8>),
    Closed(OutputStream),
}

struct StdinDeliveryResult {
    stdin: Option<ChildStdin>,
    accepted_bytes: u64,
    complete: bool,
}

struct BoundedBuffer {
    bytes: Vec<u8>,
    limit: usize,
    exceeded: bool,
}

impl io::Write for BoundedBuffer {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        if data.len() > self.limit.saturating_sub(self.bytes.len()) {
            self.exceeded = true;
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "supervisor message exceeds its byte limit",
            ));
        }
        self.bytes.extend_from_slice(data);
        Ok(data.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[derive(Default)]
struct StreamClosures {
    stdout: bool,
    stderr: bool,
}

impl StreamClosures {
    fn observe(&mut self, event: &StreamEvent) {
        match event {
            StreamEvent::Closed(OutputStream::Stdout) => self.stdout = true,
            StreamEvent::Closed(OutputStream::Stderr) => self.stderr = true,
            StreamEvent::Data(_, _) => {}
        }
    }

    fn complete(&self) -> bool {
        self.stdout && self.stderr
    }
}

pub(crate) async fn run_internal() -> io::Result<()> {
    let stdin = tokio::io::stdin();
    let mut requests = BufReader::new(stdin);
    let mut stdout = tokio::io::stdout();
    write_event(
        &mut stdout,
        &SupervisorEvent::Booted {
            version: PROTOCOL_VERSION,
        },
    )
    .await?;

    let request = read_request(&mut requests).await?;
    let SupervisorRequest::Prepare { version, plan } = request else {
        return protocol_failure(&mut stdout, "prepare must be the first supervisor request").await;
    };
    if version != PROTOCOL_VERSION {
        return protocol_failure(&mut stdout, "unsupported supervisor protocol version").await;
    }
    validate_plan(&plan)?;
    #[cfg(target_os = "linux")]
    if plan.isolation == LaunchIsolation::LinuxBubblewrap {
        crate::isolation::isolate_linux_session_keyring()?;
    }
    write_event(&mut stdout, &SupervisorEvent::Prepared).await?;

    let request = read_request(&mut requests).await?;
    if !matches!(request, SupervisorRequest::Start) {
        return protocol_failure(&mut stdout, "start must follow prepare").await;
    }
    run_payload(plan, requests, stdout).await
}

async fn run_payload(
    plan: LaunchPlan,
    mut requests: BufReader<tokio::io::Stdin>,
    mut stdout: tokio::io::Stdout,
) -> io::Result<()> {
    let mut command = Command::new(&plan.executable);
    command
        .args(&plan.arguments)
        .current_dir(&plan.cwd)
        .env_clear()
        .envs(&plan.environment)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    configure_command_tree(&mut command);

    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            write_event(
                &mut stdout,
                &SupervisorEvent::StartFailed {
                    message: bounded_message(&error),
                },
            )
            .await?;
            return Ok(());
        }
    };
    let tree_id = child.id();
    let mut linux_orphan_reaper = tokio::time::interval(Duration::from_millis(50));
    linux_orphan_reaper.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut payload_stdin = child.stdin.take();
    let payload_stdout = child
        .stdout
        .take()
        .ok_or_else(|| io::Error::other("payload stdout pipe is unavailable"))?;
    let payload_stderr = child
        .stderr
        .take()
        .ok_or_else(|| io::Error::other("payload stderr pipe is unavailable"))?;

    let (stream_tx, mut stream_rx) = mpsc::channel(16);
    let mut stream_closures = StreamClosures::default();
    tokio::spawn(drain_stream(
        payload_stdout,
        OutputStream::Stdout,
        stream_tx.clone(),
    ));
    tokio::spawn(drain_stream(
        payload_stderr,
        OutputStream::Stderr,
        stream_tx,
    ));

    let wall_deadline = tokio::time::Instant::now() + Duration::from_millis(plan.wall_time_ms);
    let initial_stdin = plan
        .initial_stdin
        .as_deref()
        .map(decode_bytes)
        .transpose()?
        .unwrap_or_default();
    let initial_stdin_bytes = initial_stdin.len() as u64;
    let mut initial_delivery = tokio::spawn(deliver_stdin(
        payload_stdin.take(),
        initial_stdin,
        !plan.keep_stdin_open,
    ));
    let mut stop_reason = None;
    let mut forced_cleanup_proven = false;
    let mut start_allowed = true;
    let initial_result = loop {
        tokio::select! {
            delivered = &mut initial_delivery => {
                break delivered.unwrap_or(StdinDeliveryResult {
                    stdin: None,
                    accepted_bytes: 0,
                    complete: false,
                });
            }
            stream = stream_rx.recv() => {
                if let Some(stream) = stream {
                    stream_closures.observe(&stream);
                    forward_stream_event(&mut stdout, stream).await?;
                }
            }
            _ = linux_orphan_reaper.tick(), if plan.isolation == LaunchIsolation::LinuxBubblewrap => {
                reap_linux_namespace_orphans(tree_id);
            }
            request = read_optional_request(&mut requests) => {
                match request? {
                    Some(SupervisorRequest::Signal { signal }) => {
                        let accepted = signal_tree(tree_id, signal, &mut child).await;
                        write_event(&mut stdout, &SupervisorEvent::SignalResult { accepted }).await?;
                    }
                    Some(SupervisorRequest::Kill { reason }) => {
                        initial_delivery.abort();
                        let _ = initial_delivery.await;
                        stop_reason = Some(reason);
                        start_allowed = false;
                        forced_cleanup_proven |= force_tree(tree_id, &mut child).await;
                        break StdinDeliveryResult {
                            stdin: None,
                            accepted_bytes: 0,
                            complete: false,
                        };
                    }
                    None => {
                        initial_delivery.abort();
                        let _ = initial_delivery.await;
                        stop_reason = Some(StopReason::Shutdown);
                        start_allowed = false;
                        forced_cleanup_proven |= force_tree(tree_id, &mut child).await;
                        break StdinDeliveryResult {
                            stdin: None,
                            accepted_bytes: 0,
                            complete: false,
                        };
                    }
                    Some(
                        SupervisorRequest::Prepare { .. }
                        | SupervisorRequest::Start
                        | SupervisorRequest::WriteStdin { .. }
                        | SupervisorRequest::CloseStdin,
                    ) => {
                        initial_delivery.abort();
                        let _ = initial_delivery.await;
                        stop_reason = Some(StopReason::Shutdown);
                        start_allowed = false;
                        forced_cleanup_proven |= force_tree(tree_id, &mut child).await;
                        write_event(&mut stdout, &SupervisorEvent::ProtocolError {
                            message: "invalid request during initial stdin delivery".to_owned(),
                        }).await?;
                        break StdinDeliveryResult {
                            stdin: None,
                            accepted_bytes: 0,
                            complete: false,
                        };
                    }
                }
            }
            _ = tokio::time::sleep_until(wall_deadline) => {
                initial_delivery.abort();
                let _ = initial_delivery.await;
                stop_reason = Some(StopReason::Timeout);
                start_allowed = false;
                forced_cleanup_proven |= force_tree(tree_id, &mut child).await;
                break StdinDeliveryResult {
                    stdin: None,
                    accepted_bytes: 0,
                    complete: false,
                };
            }
        }
    };
    payload_stdin = initial_result.stdin;
    if start_allowed {
        write_event(
            &mut stdout,
            &SupervisorEvent::Started {
                stdin_open: payload_stdin.is_some(),
                accepted_stdin_bytes: initial_result.accepted_bytes,
                initial_stdin_complete: initial_result.complete
                    && initial_result.accepted_bytes == initial_stdin_bytes,
            },
        )
        .await?;
    }

    let timeout = tokio::time::sleep_until(wall_deadline);
    tokio::pin!(timeout);
    let mut stdin_delivery: Option<tokio::task::JoinHandle<StdinDeliveryResult>> = None;
    let status = loop {
        tokio::select! {
            biased;
            status = child.wait() => break status?,
            delivered = async {
                stdin_delivery
                    .as_mut()
                    .expect("stdin delivery branch requires a task")
                    .await
            }, if stdin_delivery.is_some() => {
                let delivered = delivered.unwrap_or(StdinDeliveryResult {
                    stdin: None,
                    accepted_bytes: 0,
                    complete: false,
                });
                stdin_delivery = None;
                payload_stdin = delivered.stdin;
                write_event(&mut stdout, &SupervisorEvent::StdinResult {
                    accepted_bytes: delivered.accepted_bytes,
                    open: payload_stdin.is_some(),
                }).await?;
            }
            _ = &mut timeout, if stop_reason.is_none() => {
                stop_reason = Some(StopReason::Timeout);
                abort_stdin_delivery(&mut stdin_delivery).await;
                close_payload_stdin(&mut payload_stdin).await;
                forced_cleanup_proven |= force_tree(tree_id, &mut child).await;
            }
            stream = stream_rx.recv() => {
                if let Some(stream) = stream {
                    stream_closures.observe(&stream);
                    forward_stream_event(&mut stdout, stream).await?;
                }
            }
            _ = linux_orphan_reaper.tick(), if plan.isolation == LaunchIsolation::LinuxBubblewrap => {
                reap_linux_namespace_orphans(tree_id);
            }
            request = read_optional_request(&mut requests) => {
                match request? {
                    Some(SupervisorRequest::WriteStdin { data, close_after_write }) => {
                        if stdin_delivery.is_some() {
                            write_event(&mut stdout, &SupervisorEvent::ProtocolError {
                                message: "concurrent stdin delivery is invalid".to_owned(),
                            }).await?;
                        } else {
                            let bytes = decode_bytes(&data)?;
                            stdin_delivery = Some(tokio::spawn(deliver_stdin(
                                payload_stdin.take(),
                                bytes,
                                close_after_write,
                            )));
                        }
                    }
                    Some(SupervisorRequest::CloseStdin) => {
                        abort_stdin_delivery(&mut stdin_delivery).await;
                        close_payload_stdin(&mut payload_stdin).await;
                        write_event(&mut stdout, &SupervisorEvent::StdinResult {
                            accepted_bytes: 0,
                            open: false,
                        }).await?;
                    }
                    Some(SupervisorRequest::Signal { signal }) => {
                        let accepted = signal_tree(tree_id, signal, &mut child).await;
                        write_event(&mut stdout, &SupervisorEvent::SignalResult { accepted }).await?;
                    }
                    Some(SupervisorRequest::Kill { reason }) => {
                        stop_reason = Some(reason);
                        abort_stdin_delivery(&mut stdin_delivery).await;
                        close_payload_stdin(&mut payload_stdin).await;
                        forced_cleanup_proven |= force_tree(tree_id, &mut child).await;
                    }
                    Some(SupervisorRequest::Prepare { .. } | SupervisorRequest::Start) => {
                        write_event(&mut stdout, &SupervisorEvent::ProtocolError {
                            message: "invalid request after payload start".to_owned(),
                        }).await?;
                    }
                    None => {
                        stop_reason = Some(StopReason::Shutdown);
                        abort_stdin_delivery(&mut stdin_delivery).await;
                        close_payload_stdin(&mut payload_stdin).await;
                        forced_cleanup_proven |= force_tree(tree_id, &mut child).await;
                    }
                }
            }
        }
    };
    abort_stdin_delivery(&mut stdin_delivery).await;
    close_payload_stdin(&mut payload_stdin).await;

    let (exit_code, signal, native_signaled) = portable_exit_status(status);
    write_event(
        &mut stdout,
        &SupervisorEvent::Terminal {
            exit_code,
            signal,
            native_signaled,
            stop_reason,
        },
    )
    .await?;

    let cleanup_complete = if plan.isolation == LaunchIsolation::LinuxBubblewrap {
        cleanup_linux_pid_namespace(&mut child).await
    } else {
        forced_cleanup_proven || cleanup_tree(tree_id, &mut child, stop_reason.is_none()).await
    };
    let drain_deadline = tokio::time::sleep(CLEANUP_GRACE);
    tokio::pin!(drain_deadline);
    while !stream_closures.complete() {
        tokio::select! {
            stream = stream_rx.recv() => {
                match stream {
                    Some(stream) => {
                        stream_closures.observe(&stream);
                        forward_stream_event(&mut stdout, stream).await?;
                    }
                    None => break,
                }
            }
            _ = &mut drain_deadline => break,
        }
    }
    let cleanup = classify_cleanup(plan.isolation, cleanup_complete);
    write_event(
        &mut stdout,
        &SupervisorEvent::Cleaned {
            cleanup,
            output_complete: stream_closures.complete(),
        },
    )
    .await
}

#[cfg(target_os = "linux")]
fn reap_linux_namespace_orphans(initial_pid: Option<u32>) {
    let initial_pid = initial_pid.and_then(|pid| i32::try_from(pid).ok());
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return;
    };
    for entry in entries.flatten() {
        let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        let Ok(pid) = name.parse::<i32>() else {
            continue;
        };
        if pid <= 1 || Some(pid) == initial_pid {
            continue;
        }
        unsafe {
            libc::waitpid(pid, std::ptr::null_mut(), libc::WNOHANG);
        }
    }
}

#[cfg(not(target_os = "linux"))]
fn reap_linux_namespace_orphans(_initial_pid: Option<u32>) {}

#[cfg(target_os = "linux")]
async fn cleanup_linux_pid_namespace(child: &mut Child) -> bool {
    let _ = child.start_kill();
    let deadline = tokio::time::Instant::now() + CLEANUP_GRACE;
    loop {
        reap_linux_namespace_children();
        let mut remaining = Vec::new();
        let Ok(entries) = std::fs::read_dir("/proc") else {
            return false;
        };
        for entry in entries.flatten() {
            let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            let Ok(pid) = name.parse::<i32>() else {
                continue;
            };
            if pid > 1 {
                remaining.push(pid);
            }
        }
        if remaining.is_empty() {
            return true;
        }
        for pid in remaining {
            unsafe {
                libc::kill(pid, libc::SIGKILL);
            }
        }
        if tokio::time::Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

#[cfg(target_os = "linux")]
fn reap_linux_namespace_children() {
    loop {
        let result = unsafe { libc::waitpid(-1, std::ptr::null_mut(), libc::WNOHANG) };
        if result <= 0 {
            return;
        }
    }
}

#[cfg(not(target_os = "linux"))]
async fn cleanup_linux_pid_namespace(_child: &mut Child) -> bool {
    false
}

fn classify_cleanup(isolation: LaunchIsolation, cleanup_complete: bool) -> SupervisorCleanup {
    if cleanup_complete {
        SupervisorCleanup::Complete
    } else if isolation == LaunchIsolation::MacosSeatbelt {
        SupervisorCleanup::ResidualConfined
    } else {
        SupervisorCleanup::Failed
    }
}

fn validate_plan(plan: &LaunchPlan) -> io::Result<()> {
    if !plan.executable.is_absolute()
        || !plan.cwd.is_absolute()
        || plan.wall_time_ms == 0
        || plan.arguments.iter().any(|value| value.contains('\0'))
        || plan
            .environment
            .iter()
            .any(|(name, value)| name.contains(['\0', '=']) || value.contains('\0'))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid supervisor launch plan",
        ));
    }
    Ok(())
}

async fn drain_stream<R>(mut reader: R, stream: OutputStream, sender: mpsc::Sender<StreamEvent>)
where
    R: AsyncRead + Unpin,
{
    let mut buffer = vec![0_u8; OUTPUT_CHUNK_BYTES];
    loop {
        match reader.read(&mut buffer).await {
            Ok(0) | Err(_) => break,
            Ok(read) => {
                if sender
                    .send(StreamEvent::Data(stream, buffer[..read].to_vec()))
                    .await
                    .is_err()
                {
                    return;
                }
            }
        }
    }
    let _ = sender.send(StreamEvent::Closed(stream)).await;
}

async fn forward_stream_event<W: AsyncWriteExt + Unpin>(
    writer: &mut W,
    event: StreamEvent,
) -> io::Result<()> {
    match event {
        StreamEvent::Data(stream, data) => {
            write_event(
                writer,
                &SupervisorEvent::Output {
                    stream,
                    data: base64::engine::general_purpose::STANDARD.encode(data),
                },
            )
            .await
        }
        StreamEvent::Closed(stream) => {
            write_event(writer, &SupervisorEvent::StreamClosed { stream }).await
        }
    }
}

async fn deliver_stdin(
    mut stdin: Option<ChildStdin>,
    bytes: Vec<u8>,
    close_after_write: bool,
) -> StdinDeliveryResult {
    let mut accepted = 0_usize;
    let mut complete = true;
    while accepted < bytes.len() {
        let Some(open) = stdin.as_mut() else {
            complete = false;
            break;
        };
        match open.write(&bytes[accepted..]).await {
            Ok(0) | Err(_) => {
                complete = false;
                close_payload_stdin(&mut stdin).await;
                break;
            }
            Ok(written) => accepted = accepted.saturating_add(written),
        }
    }
    if close_after_write {
        close_payload_stdin(&mut stdin).await;
    }
    StdinDeliveryResult {
        stdin,
        accepted_bytes: accepted as u64,
        complete,
    }
}

async fn abort_stdin_delivery(delivery: &mut Option<tokio::task::JoinHandle<StdinDeliveryResult>>) {
    if let Some(delivery) = delivery.take() {
        delivery.abort();
        let _ = delivery.await;
    }
}

async fn close_payload_stdin(stdin: &mut Option<ChildStdin>) {
    if let Some(mut open) = stdin.take() {
        let _ = open.shutdown().await;
    }
}

async fn read_request<R: AsyncBufRead + Unpin>(reader: &mut R) -> io::Result<SupervisorRequest> {
    read_optional_request(reader)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::UnexpectedEof, "supervisor control EOF"))
}

async fn read_optional_request<R: AsyncBufRead + Unpin>(
    reader: &mut R,
) -> io::Result<Option<SupervisorRequest>> {
    let Some(line) = read_bounded_line(
        reader,
        MAX_PROTOCOL_LINE_BYTES,
        "supervisor request exceeds its byte limit",
    )
    .await?
    else {
        return Ok(None);
    };
    serde_json::from_slice(&line)
        .map(Some)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid supervisor request"))
}

pub(crate) async fn write_request<W: AsyncWriteExt + Unpin>(
    writer: &mut W,
    request: &SupervisorRequest,
) -> io::Result<()> {
    write_json_line(writer, request).await
}

pub(crate) async fn read_event<R: AsyncBufRead + Unpin>(
    reader: &mut R,
) -> io::Result<Option<SupervisorEvent>> {
    let Some(line) = read_bounded_line(
        reader,
        MAX_PROTOCOL_LINE_BYTES,
        "supervisor event exceeds its byte limit",
    )
    .await?
    else {
        return Ok(None);
    };
    serde_json::from_slice(&line)
        .map(Some)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid supervisor event"))
}

async fn read_bounded_line<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    max_payload_bytes: usize,
    limit_message: &'static str,
) -> io::Result<Option<Vec<u8>>> {
    let max_wire_bytes = max_payload_bytes
        .checked_add(2)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid line byte limit"))?;
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().await?;
        if available.is_empty() {
            if line.is_empty() {
                return Ok(None);
            }
            break;
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let consumed = newline.map_or(available.len(), |index| index + 1);
        if line.len().saturating_add(consumed) > max_wire_bytes {
            return Err(io::Error::new(io::ErrorKind::InvalidData, limit_message));
        }
        line.extend_from_slice(&available[..consumed]);
        reader.consume(consumed);
        if newline.is_some() {
            break;
        }
        if line.len() > max_payload_bytes
            && !(line.len() == max_payload_bytes + 1 && line.last() == Some(&b'\r'))
        {
            return Err(io::Error::new(io::ErrorKind::InvalidData, limit_message));
        }
    }
    if line.last() == Some(&b'\n') {
        line.pop();
        if line.last() == Some(&b'\r') {
            line.pop();
        }
    }
    if line.len() > max_payload_bytes {
        return Err(io::Error::new(io::ErrorKind::InvalidData, limit_message));
    }
    Ok(Some(line))
}

async fn write_event<W: AsyncWriteExt + Unpin>(
    writer: &mut W,
    event: &SupervisorEvent,
) -> io::Result<()> {
    write_json_line(writer, event).await
}

async fn write_json_line<W, T>(writer: &mut W, value: &T) -> io::Result<()>
where
    W: AsyncWriteExt + Unpin,
    T: Serialize,
{
    let mut encoded = encode_bounded_json(value, MAX_PROTOCOL_LINE_BYTES)?;
    encoded.push(b'\n');
    writer.write_all(&encoded).await?;
    writer.flush().await
}

fn encode_bounded_json<T: Serialize>(value: &T, limit: usize) -> io::Result<Vec<u8>> {
    let mut output = BoundedBuffer {
        bytes: Vec::new(),
        limit,
        exceeded: false,
    };
    let result = serde_json::to_writer(&mut output, value);
    if output.exceeded {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "supervisor message exceeds its byte limit",
        ));
    }
    result.map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "supervisor encoding failed"))?;
    Ok(output.bytes)
}

async fn protocol_failure<W: AsyncWriteExt + Unpin>(
    writer: &mut W,
    message: &str,
) -> io::Result<()> {
    write_event(
        writer,
        &SupervisorEvent::ProtocolError {
            message: message.to_owned(),
        },
    )
    .await
}

fn decode_bytes(value: &str) -> io::Result<Vec<u8>> {
    base64::engine::general_purpose::STANDARD
        .decode(value)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid supervisor bytes"))
}

fn bounded_message(error: &io::Error) -> String {
    let message = error.to_string();
    message.chars().take(512).collect()
}

#[cfg(unix)]
fn configure_command_tree(command: &mut Command) {
    unsafe {
        command.pre_exec(|| {
            if libc::setpgid(0, 0) == 0 {
                Ok(())
            } else {
                Err(io::Error::last_os_error())
            }
        });
    }
}

#[cfg(not(unix))]
fn configure_command_tree(_command: &mut Command) {}

#[cfg(unix)]
async fn signal_tree(tree_id: Option<u32>, signal: ControlSignal, _child: &mut Child) -> bool {
    let Some(tree_id) = tree_id else {
        return false;
    };
    let native_signal = match signal {
        ControlSignal::Interrupt => libc::SIGINT,
        ControlSignal::Terminate => libc::SIGTERM,
        ControlSignal::Kill => libc::SIGKILL,
    };
    let result = unsafe { libc::kill(-(tree_id as i32), native_signal) };
    result == 0
}

#[cfg(not(unix))]
async fn signal_tree(_tree_id: Option<u32>, _signal: ControlSignal, _child: &mut Child) -> bool {
    false
}

#[cfg(unix)]
async fn force_tree(tree_id: Option<u32>, child: &mut Child) -> bool {
    if let Some(tree_id) = tree_id {
        unsafe {
            libc::kill(-(tree_id as i32), libc::SIGKILL);
        }
    }
    let _ = child.start_kill();
    false
}

#[cfg(not(unix))]
async fn force_tree(tree_id: Option<u32>, child: &mut Child) -> bool {
    let complete = if let Some(tree_id) = tree_id {
        let pid = tree_id.to_string();
        Command::new("taskkill")
            .args(["/PID", pid.as_str(), "/T", "/F"])
            .env_clear()
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .await
            .is_ok_and(|status| status.success())
    } else {
        false
    };
    let _ = child.start_kill();
    complete
}

#[cfg(unix)]
async fn cleanup_tree(tree_id: Option<u32>, child: &mut Child, _natural_completion: bool) -> bool {
    let Some(tree_id) = tree_id else {
        return false;
    };
    let group = -(tree_id as i32);
    let present = unsafe { libc::kill(group, 0) } == 0
        || io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH);
    if !present {
        return true;
    }
    unsafe {
        libc::kill(group, libc::SIGKILL);
    }
    let _ = child.start_kill();
    let deadline = tokio::time::Instant::now() + CLEANUP_GRACE;
    loop {
        let result = unsafe { libc::kill(group, 0) };
        if result != 0 && io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            return true;
        }
        if tokio::time::Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
}

#[cfg(not(unix))]
async fn cleanup_tree(tree_id: Option<u32>, child: &mut Child, natural_completion: bool) -> bool {
    let Some(tree_id) = tree_id else {
        return false;
    };
    let pid = tree_id.to_string();
    let complete = Command::new("taskkill")
        .args(["/PID", pid.as_str(), "/T", "/F"])
        .env_clear()
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .await
        .is_ok_and(|status| status.success());
    let _ = child.start_kill();
    complete || natural_completion
}

#[cfg(unix)]
fn portable_exit_status(
    status: std::process::ExitStatus,
) -> (Option<i32>, Option<ControlSignal>, bool) {
    use std::os::unix::process::ExitStatusExt;
    let native_signal = status.signal();
    let signal = match native_signal {
        Some(libc::SIGINT) => Some(ControlSignal::Interrupt),
        Some(libc::SIGTERM) => Some(ControlSignal::Terminate),
        Some(libc::SIGKILL) => Some(ControlSignal::Kill),
        _ => None,
    };
    (status.code(), signal, native_signal.is_some())
}

#[cfg(not(unix))]
fn portable_exit_status(
    status: std::process::ExitStatus,
) -> (Option<i32>, Option<ControlSignal>, bool) {
    (status.code(), None, false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn bounded_line_rejects_a_silent_overlong_peer() {
        let (mut writer, reader) = tokio::io::duplex(32);
        writer
            .write_all(b"012345678")
            .await
            .expect("writes overlong line without closing the peer");
        let mut reader = BufReader::new(reader);

        let error = tokio::time::timeout(
            Duration::from_millis(100),
            read_bounded_line(&mut reader, 8, "line too long"),
        )
        .await
        .expect("line limit is enforced without waiting for EOF")
        .expect_err("overlong line is rejected");
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert_eq!(error.to_string(), "line too long");
    }

    #[test]
    fn macos_cleanup_preserves_residual_confinement_when_group_exit_is_unproven() {
        assert_eq!(
            classify_cleanup(LaunchIsolation::MacosSeatbelt, true),
            SupervisorCleanup::Complete
        );
        assert_eq!(
            classify_cleanup(LaunchIsolation::MacosSeatbelt, false),
            SupervisorCleanup::ResidualConfined
        );
        assert_eq!(
            classify_cleanup(LaunchIsolation::Disabled, true),
            SupervisorCleanup::Complete
        );
        assert_eq!(
            classify_cleanup(LaunchIsolation::Disabled, false),
            SupervisorCleanup::Failed
        );
    }

    #[test]
    fn bounded_json_encoding_rejects_before_growing_past_its_limit() {
        let error = encode_bounded_json(&"oversized", 4).expect_err("encoding exceeds limit");
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert_eq!(
            error.to_string(),
            "supervisor message exceeds its byte limit"
        );
    }
}
