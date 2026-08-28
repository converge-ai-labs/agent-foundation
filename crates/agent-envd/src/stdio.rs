use std::{collections::HashSet, future::Future, io, sync::Arc, time::Duration};

use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader},
    sync::{Semaphore, mpsc, oneshot, watch},
    task::JoinSet,
    time::{Instant, MissedTickBehavior, timeout, timeout_at},
};

use crate::{
    config::Config,
    daemon::Daemon,
    eip::{DataFrame, DataFrameKind, decode_data_frame, encode_data_frame},
    operation::ActiveResponseHandoff,
    transfer::reset_status,
};

const MAX_HEADER_BYTES: usize = 8 * 1024;
const MAX_HEADER_LINE_BYTES: usize = 4 * 1024;
const MAX_HEADER_COUNT: usize = 32;
const JSON_CONTENT_TYPE: &str = "application/json; charset=utf-8";
const DATA_CONTENT_TYPE: &str = "application/vnd.a13n.eip-data";
const SHUTDOWN_DRAIN_TIMEOUT: Duration = Duration::from_secs(1);
#[cfg(not(test))]
const SESSION_CLOSE_BARRIER_TIMEOUT: Duration = Duration::from_secs(5);
#[cfg(test)]
const SESSION_CLOSE_BARRIER_TIMEOUT: Duration = Duration::from_millis(250);
const MAX_CONTROL_BURST: usize = 8;

enum InboundFrame {
    Control(Vec<u8>),
    Data(DataFrame),
}

enum InboundData {
    Frame(DataFrame),
    Barrier(oneshot::Sender<()>),
}

struct ControlResponse {
    payload: Vec<u8>,
    handoff: Option<ActiveResponseHandoff>,
    closes_session: bool,
}

pub(crate) async fn serve(daemon: Arc<Daemon>, config: &Config) -> io::Result<()> {
    let reader = BufReader::new(tokio::io::stdin());
    let writer = tokio::io::stdout();
    serve_io(reader, writer, daemon, config, shutdown_signal()).await
}

async fn serve_io<R, W, S>(
    mut reader: R,
    writer: W,
    daemon: Arc<Daemon>,
    config: &Config,
    shutdown: S,
) -> io::Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin + Send + 'static,
    S: Future<Output = io::Result<()>>,
{
    let max_request_bytes = usize::try_from(config.limits.max_request_bytes)
        .map_err(|_| invalid_data("max_request_bytes does not fit this platform"))?;
    let max_response_bytes = usize::try_from(config.limits.max_response_bytes)
        .map_err(|_| invalid_data("max_response_bytes does not fit this platform"))?;
    let max_data_bytes = usize::try_from(config.limits.max_transfer_frame_bytes)
        .map_err(|_| invalid_data("max_transfer_frame_bytes does not fit this platform"))?;
    let max_concurrency = usize::try_from(config.limits.max_concurrent_operations)
        .map_err(|_| invalid_data("max_concurrent_operations does not fit this platform"))?;
    let max_transfers = usize::try_from(config.limits.max_concurrent_file_transfers)
        .map_err(|_| invalid_data("max_concurrent_file_transfers does not fit this platform"))?;
    let transfer_timeout = Duration::from_millis(config.limits.max_file_transfer_duration_ms);

    let (control_tx, control_rx) = mpsc::channel::<ControlResponse>(max_concurrency);
    let (rearm_tx, mut rearm_rx) = mpsc::unbounded_channel::<()>();
    let data_capacity = max_transfers.saturating_mul(2).max(2);
    let (data_tx, data_rx) = mpsc::channel::<DataFrame>(data_capacity);
    let (inbound_data_tx, mut inbound_data_rx) = mpsc::channel::<InboundData>(data_capacity);
    daemon
        .begin_session(data_tx.clone())
        .map_err(|error| invalid_data_owned(error.to_string()))?;

    let (writer_stopped, mut writer_stopped_rx) = watch::channel(false);
    let mut writer_task = tokio::spawn(async move {
        let result = writer_loop(
            writer,
            control_rx,
            data_rx,
            max_response_bytes,
            max_data_bytes,
            rearm_tx,
        )
        .await;
        writer_stopped.send_replace(true);
        result
    });

    let inbound_daemon = Arc::clone(&daemon);
    let inbound_responses = data_tx.clone();
    let (inbound_stopped, mut inbound_stopped_rx) = watch::channel(false);
    let mut inbound_data_task = tokio::spawn(async move {
        let result = async {
            while let Some(message) = inbound_data_rx.recv().await {
                let InboundData::Frame(frame) = message else {
                    let InboundData::Barrier(completed) = message else {
                        unreachable!();
                    };
                    let _ = completed.send(());
                    continue;
                };
                let handled = timeout(
                    transfer_timeout,
                    inbound_daemon.handle_data_frame(frame.clone()),
                )
                .await
                .map_err(|_| {
                    io::Error::new(
                        io::ErrorKind::TimedOut,
                        "stdio transfer handling exceeded its deadline",
                    )
                })?;
                if let Err(error) = handled {
                    let reset = DataFrame {
                        kind: DataFrameKind::Reset,
                        handle: frame.handle,
                        offset: frame.offset,
                        payload: Vec::new(),
                        reset_status: Some(reset_status(error)),
                    };
                    timeout(SHUTDOWN_DRAIN_TIMEOUT, inbound_responses.send(reset))
                        .await
                        .map_err(|_| {
                            io::Error::new(
                                io::ErrorKind::TimedOut,
                                "stdio transfer RESET enqueue exceeded its deadline",
                            )
                        })?
                        .map_err(|_| {
                            io::Error::new(io::ErrorKind::BrokenPipe, "stdio data writer stopped")
                        })?;
                }
            }
            Ok(())
        }
        .await;
        inbound_stopped.send_replace(true);
        result
    });

    let maintenance_daemon = Arc::clone(&daemon);
    let maintenance_task = tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_millis(100));
        interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            interval.tick().await;
            maintenance_daemon.maintenance().await;
        }
    });

    let admission = Arc::new(Semaphore::new(max_concurrency));
    let mut requests = JoinSet::new();
    let mut closed = daemon.subscribe_closed();
    let mut shutdown = std::pin::pin!(shutdown);
    let mut first_frame = true;
    let mut initial_carrier_session = true;
    let mut request_task_error = None;
    let mut session_close_deadline = None;

    'carrier: loop {
        while let Some(result) = requests.try_join_next() {
            if let Err(error) = result {
                request_task_error = Some(io::Error::other(format!(
                    "stdio request task failed: {error}"
                )));
                break 'carrier;
            }
        }
        if *closed.borrow() {
            let close_deadline = *session_close_deadline
                .get_or_insert_with(|| Instant::now() + SESSION_CLOSE_BARRIER_TIMEOUT);
            let rearmed = tokio::select! {
                biased;
                changed = writer_stopped_rx.changed() => {
                    match changed {
                        Ok(()) | Err(_) => break,
                    }
                }
                changed = inbound_stopped_rx.changed() => {
                    match changed {
                        Ok(()) | Err(_) => break,
                    }
                }
                signal = &mut shutdown => {
                    signal?;
                    break;
                }
                rearmed = timeout_at(close_deadline, rearm_rx.recv()) => {
                    match rearmed {
                        Ok(rearmed) => rearmed,
                        Err(_) => {
                            request_task_error = Some(io::Error::new(
                                io::ErrorKind::TimedOut,
                                "stdio session close response flush exceeded its deadline",
                            ));
                            break 'carrier;
                        }
                    }
                }
            };
            let Some(()) = rearmed else {
                break;
            };
            daemon
                .begin_session(data_tx.clone())
                .map_err(|error| invalid_data_owned(error.to_string()))?;
            session_close_deadline = None;
            first_frame = true;
            initial_carrier_session = false;
            continue;
        }

        let read_timeout = if daemon.has_active_file_transfers() {
            Duration::from_millis(config.limits.max_file_transfer_duration_ms)
        } else {
            config.session_idle_timeout
        };
        let waiting_for_initialize = first_frame;
        let initial_session_frame = waiting_for_initialize && initial_carrier_session;
        let read = async {
            if initial_session_frame {
                match timeout(
                    config.initialization_timeout,
                    read_frame(&mut reader, max_request_bytes, max_data_bytes),
                )
                .await
                {
                    Ok(frame) => frame,
                    Err(_) => Ok(None),
                }
            } else if waiting_for_initialize {
                read_frame(&mut reader, max_request_bytes, max_data_bytes).await
            } else {
                match timeout(
                    read_timeout,
                    read_frame(&mut reader, max_request_bytes, max_data_bytes),
                )
                .await
                {
                    Ok(frame) => frame,
                    Err(_) => Ok(None),
                }
            }
        };
        tokio::pin!(read);
        let frame = tokio::select! {
            biased;
            changed = closed.changed() => {
                match changed {
                    Ok(()) => continue,
                    Err(_) => break,
                }
            }
            changed = writer_stopped_rx.changed() => {
                match changed {
                    Ok(()) | Err(_) => break,
                }
            }
            changed = inbound_stopped_rx.changed() => {
                match changed {
                    Ok(()) | Err(_) => break,
                }
            }
            signal = &mut shutdown => {
                signal?;
                break;
            }
            frame = &mut read => frame?,
        };
        let Some(frame) = frame else {
            break;
        };

        if first_frame {
            let InboundFrame::Control(frame) = frame else {
                return Err(invalid_data("initialize must be the first stdio frame"));
            };
            let payload = String::from_utf8(frame)
                .map_err(|_| invalid_data("stdio control body must be UTF-8 JSON"))?;
            let response = daemon.handle_payload_for_carrier(&payload).await;
            let (payload, handoff, closes_session) = response.into_parts();
            if control_tx
                .send(ControlResponse {
                    payload,
                    handoff,
                    closes_session,
                })
                .await
                .is_err()
            {
                break;
            }
            if !daemon.session_initialized() {
                break;
            }
            first_frame = false;
            continue;
        }

        match frame {
            InboundFrame::Control(frame) => {
                let payload = String::from_utf8(frame)
                    .map_err(|_| invalid_data("stdio control body must be UTF-8 JSON"))?;
                if is_session_close_request(&payload) {
                    let close_deadline = Instant::now() + SESSION_CLOSE_BARRIER_TIMEOUT;
                    session_close_deadline = Some(close_deadline);
                    let pending_operation = daemon.track_pending_payload(&payload);
                    let close_daemon = Arc::clone(&daemon);
                    let close_request = async move {
                        let _pending_operation = pending_operation;
                        close_daemon.handle_payload_for_carrier(&payload).await
                    };
                    let (inbound_barrier_tx, inbound_barrier_rx) = oneshot::channel();
                    let inbound_barrier = async {
                        inbound_data_tx
                            .send(InboundData::Barrier(inbound_barrier_tx))
                            .await
                            .map_err(|_| {
                                io::Error::new(
                                    io::ErrorKind::BrokenPipe,
                                    "stdio inbound data task stopped before session close",
                                )
                            })?;
                        inbound_barrier_rx.await.map_err(|_| {
                            io::Error::new(
                                io::ErrorKind::BrokenPipe,
                                "stdio inbound data barrier was not completed",
                            )
                        })
                    };
                    let close_barrier = async {
                        let (response, drained, inbound_drained) = tokio::join!(
                            close_request,
                            drain_request_tasks(&mut requests),
                            inbound_barrier,
                        );
                        drained?;
                        inbound_drained?;
                        Ok::<_, io::Error>(response)
                    };
                    tokio::pin!(close_barrier);
                    let response = tokio::select! {
                        biased;
                        changed = writer_stopped_rx.changed() => {
                            match changed {
                                Ok(()) | Err(_) => break 'carrier,
                            }
                        }
                        changed = inbound_stopped_rx.changed() => {
                            match changed {
                                Ok(()) | Err(_) => break 'carrier,
                            }
                        }
                        signal = &mut shutdown => {
                            signal?;
                            break 'carrier;
                        }
                        result = timeout_at(close_deadline, &mut close_barrier) => {
                            match result {
                                Ok(Ok(response)) => response,
                                Ok(Err(error)) => {
                                    request_task_error = Some(error);
                                    break 'carrier;
                                }
                                Err(_) => {
                                    request_task_error = Some(io::Error::new(
                                        io::ErrorKind::TimedOut,
                                        "stdio session close barrier exceeded its deadline",
                                    ));
                                    break 'carrier;
                                }
                            }
                        }
                    };
                    let (payload, handoff, closes_session) = response.into_parts();
                    let response = ControlResponse {
                        payload,
                        handoff,
                        closes_session,
                    };
                    let sent = tokio::select! {
                        biased;
                        changed = writer_stopped_rx.changed() => {
                            match changed {
                                Ok(()) | Err(_) => break 'carrier,
                            }
                        }
                        changed = inbound_stopped_rx.changed() => {
                            match changed {
                                Ok(()) | Err(_) => break 'carrier,
                            }
                        }
                        signal = &mut shutdown => {
                            signal?;
                            break 'carrier;
                        }
                        sent = timeout_at(close_deadline, control_tx.send(response)) => sent,
                    };
                    match sent {
                        Ok(Ok(())) => {}
                        Ok(Err(_)) => {
                            request_task_error = Some(io::Error::new(
                                io::ErrorKind::BrokenPipe,
                                "stdio session close response writer stopped",
                            ));
                            break 'carrier;
                        }
                        Err(_) => {
                            request_task_error = Some(io::Error::new(
                                io::ErrorKind::TimedOut,
                                "stdio session close response enqueue exceeded its deadline",
                            ));
                            break 'carrier;
                        }
                    }
                    continue;
                }
                let permit = tokio::select! {
                    biased;
                    changed = closed.changed() => {
                        match changed {
                            Ok(()) | Err(_) => break,
                        }
                    }
                    changed = writer_stopped_rx.changed() => {
                        match changed {
                            Ok(()) | Err(_) => break,
                        }
                    }
                    signal = &mut shutdown => {
                        signal?;
                        break;
                    }
                    permit = admission.clone().acquire_owned() => {
                        match permit {
                            Ok(permit) => permit,
                            Err(_) => break,
                        }
                    }
                };
                let pending_operation = daemon.track_pending_payload(&payload);
                let daemon = Arc::clone(&daemon);
                let responses = control_tx.clone();
                requests.spawn(async move {
                    let _pending_operation = pending_operation;
                    let response = daemon.handle_payload_for_carrier(&payload).await;
                    let (payload, handoff, closes_session) = response.into_parts();
                    let _ = responses
                        .send(ControlResponse {
                            payload,
                            handoff,
                            closes_session,
                        })
                        .await;
                    drop(permit);
                });
            }
            InboundFrame::Data(frame) => {
                let sent = timeout(
                    transfer_timeout,
                    inbound_data_tx.send(InboundData::Frame(frame)),
                )
                .await;
                if !matches!(sent, Ok(Ok(()))) {
                    break;
                }
            }
        }
    }

    maintenance_task.abort();
    let _ = maintenance_task.await;
    drop(inbound_data_tx);
    let inbound_data_result = match timeout(SHUTDOWN_DRAIN_TIMEOUT, &mut inbound_data_task).await {
        Ok(Ok(result)) => result,
        Ok(Err(error)) => Err(io::Error::other(format!("stdio data task failed: {error}"))),
        Err(_) => {
            inbound_data_task.abort();
            Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "stdio data drain exceeded its shutdown deadline",
            ))
        }
    };
    let session_result = if daemon.transport_closed(SHUTDOWN_DRAIN_TIMEOUT).await {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "session transfer drain exceeded its shutdown deadline",
        ))
    };
    let process_result = if daemon.drain_processes(SHUTDOWN_DRAIN_TIMEOUT).await {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "owned process drain exceeded its shutdown deadline",
        ))
    };
    let operation_result = if daemon.drain_owned_operations(SHUTDOWN_DRAIN_TIMEOUT).await {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "owned operation drain exceeded its shutdown deadline",
        ))
    };
    let request_result = if let Some(error) = request_task_error {
        Err(error)
    } else {
        match timeout(SHUTDOWN_DRAIN_TIMEOUT, async {
            while let Some(result) = requests.join_next().await {
                result.map_err(|error| {
                    io::Error::other(format!("stdio request task failed: {error}"))
                })?;
            }
            Ok(())
        })
        .await
        {
            Ok(result) => result,
            Err(_) => Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "stdio request drain exceeded its shutdown deadline",
            )),
        }
    };
    if request_result.is_err() {
        requests.abort_all();
        while requests.join_next().await.is_some() {}
    }

    drop(control_tx);
    drop(data_tx);
    let writer_result = match timeout(SHUTDOWN_DRAIN_TIMEOUT, &mut writer_task).await {
        Ok(result) => result
            .map_err(|error| io::Error::other(format!("stdio writer task failed: {error}")))?,
        Err(_) => {
            writer_task.abort();
            Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "stdio response drain exceeded its shutdown deadline",
            ))
        }
    };
    inbound_data_result?;
    session_result?;
    process_result?;
    operation_result?;
    request_result?;
    writer_result
}

fn is_session_close_request(payload: &str) -> bool {
    serde_json::from_str::<serde_json::Value>(payload)
        .ok()
        .and_then(|value| {
            value
                .get("method")
                .and_then(serde_json::Value::as_str)
                .map(str::to_owned)
        })
        .is_some_and(|method| method == "session.close")
}

async fn drain_request_tasks(requests: &mut JoinSet<()>) -> io::Result<()> {
    while let Some(result) = requests.join_next().await {
        result.map_err(|error| io::Error::other(format!("stdio request task failed: {error}")))?;
    }
    Ok(())
}

async fn writer_loop<W>(
    mut writer: W,
    mut controls: mpsc::Receiver<ControlResponse>,
    mut data: mpsc::Receiver<DataFrame>,
    max_control_bytes: usize,
    max_data_bytes: usize,
    rearm: mpsc::UnboundedSender<()>,
) -> io::Result<()>
where
    W: AsyncWrite + Unpin,
{
    let mut control_open = true;
    let mut data_open = true;
    let mut control_burst = 0_usize;
    while control_open || data_open {
        if control_burst >= MAX_CONTROL_BURST {
            match data.try_recv() {
                Ok(frame) => {
                    write_data_frame(&mut writer, &frame, max_data_bytes).await?;
                    control_burst = 0;
                    continue;
                }
                Err(mpsc::error::TryRecvError::Disconnected) => data_open = false,
                Err(mpsc::error::TryRecvError::Empty) => {}
            }
        }
        if !control_open && !data_open {
            break;
        }
        tokio::select! {
            biased;
            control = controls.recv(), if control_open => {
                match control {
                    Some(response) => {
                        if response.closes_session {
                            loop {
                                match data.try_recv() {
                                    Ok(_old_session_frame) => {}
                                    Err(mpsc::error::TryRecvError::Disconnected) => {
                                        data_open = false;
                                        break;
                                    }
                                    Err(mpsc::error::TryRecvError::Empty) => break,
                                }
                            }
                        }
                        write_outer_frame(
                            &mut writer,
                            JSON_CONTENT_TYPE,
                            &response.payload,
                            max_control_bytes,
                        )
                        .await?;
                        if response.closes_session {
                            writer.flush().await?;
                        }
                        if let Some(handoff) = response.handoff {
                            handoff.complete();
                        }
                        if response.closes_session {
                            rearm.send(()).map_err(|_| {
                                io::Error::new(
                                    io::ErrorKind::BrokenPipe,
                                    "stdio session rearm receiver stopped",
                                )
                            })?;
                        }
                        control_burst = control_burst.saturating_add(1);
                    }
                    None => control_open = false,
                }
            }
            frame = data.recv(), if data_open => {
                match frame {
                    Some(frame) => {
                        write_data_frame(&mut writer, &frame, max_data_bytes).await?;
                        control_burst = 0;
                    }
                    None => data_open = false,
                }
            }
        }
    }
    writer.flush().await
}

async fn read_frame<R>(
    reader: &mut R,
    max_control_bytes: usize,
    max_data_bytes: usize,
) -> io::Result<Option<InboundFrame>>
where
    R: AsyncRead + Unpin,
{
    let mut header = Vec::with_capacity(256);
    let mut line_bytes = 0_usize;
    let mut header_count = 0_usize;
    let mut byte = [0_u8; 1];
    loop {
        let read = reader.read(&mut byte).await?;
        if read == 0 {
            if header.is_empty() {
                return Ok(None);
            }
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "EOF inside stdio frame header",
            ));
        }
        if header.len() == MAX_HEADER_BYTES {
            return Err(invalid_data("stdio frame header exceeds its byte limit"));
        }
        if line_bytes == MAX_HEADER_LINE_BYTES {
            return Err(invalid_data(
                "stdio frame header line exceeds its byte limit",
            ));
        }
        header.push(byte[0]);
        line_bytes += 1;
        if header.ends_with(b"\r\n") {
            if header.ends_with(b"\r\n\r\n") {
                break;
            }
            header_count += 1;
            if header_count > MAX_HEADER_COUNT {
                return Err(invalid_data("stdio frame header count exceeds its limit"));
            }
            line_bytes = 0;
        }
    }

    let header = std::str::from_utf8(&header)
        .map_err(|_| invalid_data("stdio frame header must be ASCII"))?;
    if !header.is_ascii() {
        return Err(invalid_data("stdio frame header must be ASCII"));
    }

    let mut names = HashSet::new();
    let mut content_length = None;
    let mut content_type = None;
    for line in header[..header.len() - 4].split("\r\n") {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| invalid_data("malformed stdio frame header"))?;
        if !valid_header_name(name) {
            return Err(invalid_data("malformed stdio frame header name"));
        }
        let normalized_name = name.to_ascii_lowercase();
        if !names.insert(normalized_name.clone()) {
            return Err(invalid_data("duplicate stdio frame header"));
        }
        let value = value.trim_matches([' ', '\t']);
        if value.is_empty() || value.bytes().any(|byte| byte.is_ascii_control()) {
            return Err(invalid_data("malformed stdio frame header value"));
        }
        match normalized_name.as_str() {
            "content-length" => {
                if !canonical_decimal(value) {
                    return Err(invalid_data("Content-Length must be canonical decimal"));
                }
                content_length = Some(
                    value
                        .parse::<usize>()
                        .map_err(|_| invalid_data("Content-Length does not fit this platform"))?,
                );
            }
            "content-type" => content_type = Some(classify_content_type(value)?),
            "authorization" | "content-encoding" | "eip-session" | "transfer-encoding" => {
                return Err(invalid_data("security-sensitive stdio header is forbidden"));
            }
            _ if normalized_name.starts_with("eip-") => {
                return Err(invalid_data("reserved stdio header is forbidden"));
            }
            _ => {}
        }
    }

    let content_length =
        content_length.ok_or_else(|| invalid_data("Content-Length header is required"))?;
    let kind = content_type.unwrap_or(FrameContentType::Json);
    let maximum = match kind {
        FrameContentType::Json => max_control_bytes,
        FrameContentType::Data => max_data_bytes,
    };
    if content_length > maximum {
        return Err(invalid_data(
            "stdio frame body exceeds its applicable byte limit",
        ));
    }
    let mut body = vec![0_u8; content_length];
    reader.read_exact(&mut body).await?;
    match kind {
        FrameContentType::Json => Ok(Some(InboundFrame::Control(body))),
        FrameContentType::Data => decode_data_frame(&body, max_data_bytes)
            .map(InboundFrame::Data)
            .map(Some)
            .map_err(|_| invalid_data("invalid EIP data frame")),
    }
}

async fn write_data_frame<W>(
    writer: &mut W,
    frame: &DataFrame,
    max_body_bytes: usize,
) -> io::Result<()>
where
    W: AsyncWrite + Unpin,
{
    let payload = encode_data_frame(frame, max_body_bytes)
        .map_err(|_| invalid_data("outbound EIP data frame is invalid"))?;
    write_outer_frame(writer, DATA_CONTENT_TYPE, &payload, max_body_bytes).await
}

async fn write_outer_frame<W>(
    writer: &mut W,
    content_type: &str,
    payload: &[u8],
    max_body_bytes: usize,
) -> io::Result<()>
where
    W: AsyncWrite + Unpin,
{
    if payload.len() > max_body_bytes {
        return Err(invalid_data("stdio outbound frame exceeds its byte limit"));
    }
    let header = format!(
        "Content-Length: {}\r\nContent-Type: {content_type}\r\n\r\n",
        payload.len()
    );
    writer.write_all(header.as_bytes()).await?;
    writer.write_all(payload).await?;
    writer.flush().await
}

#[derive(Clone, Copy)]
enum FrameContentType {
    Json,
    Data,
}

fn classify_content_type(value: &str) -> io::Result<FrameContentType> {
    if valid_json_content_type(value) {
        return Ok(FrameContentType::Json);
    }
    if value.eq_ignore_ascii_case(DATA_CONTENT_TYPE) {
        return Ok(FrameContentType::Data);
    }
    Err(invalid_data("unsupported stdio Content-Type"))
}

fn canonical_decimal(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().all(|byte| byte.is_ascii_digit())
        && (value == "0" || !value.starts_with('0'))
}

fn valid_header_name(name: &str) -> bool {
    !name.is_empty()
        && name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn valid_json_content_type(value: &str) -> bool {
    let mut parts = value.split(';').map(str::trim);
    if !parts
        .next()
        .is_some_and(|media_type| media_type.eq_ignore_ascii_case("application/json"))
    {
        return false;
    }
    match (parts.next(), parts.next()) {
        (None, None) => true,
        (Some(charset), None) => charset.eq_ignore_ascii_case("charset=utf-8"),
        _ => false,
    }
}

fn invalid_data(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn invalid_data_owned(message: String) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(unix)]
async fn shutdown_signal() -> io::Result<()> {
    use tokio::signal::unix::{SignalKind, signal};

    let mut terminate = signal(SignalKind::terminate())?;
    tokio::select! {
        result = tokio::signal::ctrl_c() => result,
        _ = terminate.recv() => Ok(()),
    }
}

#[cfg(not(unix))]
async fn shutdown_signal() -> io::Result<()> {
    tokio::signal::ctrl_c().await
}

#[cfg(test)]
mod tests {
    use std::{fs, future::pending, io, path::PathBuf, sync::Arc, time::Duration};

    use tokio::io::{AsyncReadExt, AsyncWriteExt, duplex};

    use crate::{
        config::{Config, TrustedMountConfig},
        daemon::Daemon,
        eip::{DataFrame, DataFrameKind},
        operation::random_selector,
    };

    use super::{
        ControlResponse, InboundFrame, drain_request_tasks, encode_data_frame,
        is_session_close_request, read_frame, serve_io, write_outer_frame, writer_loop,
    };

    struct TempTree(PathBuf);

    impl TempTree {
        fn new() -> Self {
            let path = std::env::temp_dir().join(
                random_selector("agent-envd-stdio-test").expect("random temporary directory"),
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

    async fn write_json_frame<W>(writer: &mut W, value: serde_json::Value)
    where
        W: tokio::io::AsyncWrite + Unpin,
    {
        let payload = value.to_string();
        write_outer_frame(
            writer,
            super::JSON_CONTENT_TYPE,
            payload.as_bytes(),
            64 * 1024,
        )
        .await
        .expect("JSON frame writes");
    }

    #[test]
    fn recognizes_only_explicit_session_close_requests_as_reuse_barriers() {
        assert!(is_session_close_request(
            r#"{"jsonrpc":"2.0","id":1,"method":"session.close","params":{}}"#
        ));
        assert!(!is_session_close_request(
            r#"{"jsonrpc":"2.0","id":1,"method":"environment.describe","params":{}}"#
        ));
        assert!(!is_session_close_request("not-json"));
    }

    #[tokio::test]
    async fn session_close_barrier_enqueues_after_earlier_request_responses() {
        let (responses, mut received) = tokio::sync::mpsc::channel(2);
        let mut requests = tokio::task::JoinSet::new();
        let earlier_responses = responses.clone();
        requests.spawn(async move {
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            earlier_responses
                .send("earlier")
                .await
                .expect("response receiver remains open");
        });

        let close_request = async { "close" };
        let (close, drained) = tokio::join!(close_request, drain_request_tasks(&mut requests));
        drained.expect("earlier request tasks drain");
        responses
            .send(close)
            .await
            .expect("response receiver remains open");

        assert_eq!(received.recv().await, Some("earlier"));
        assert_eq!(received.recv().await, Some("close"));
    }

    #[tokio::test]
    async fn writer_discards_queued_session_data_before_the_close_response() {
        let (control_tx, control_rx) = tokio::sync::mpsc::channel(2);
        let (data_tx, data_rx) = tokio::sync::mpsc::channel(2);
        data_tx
            .send(DataFrame {
                kind: DataFrameKind::Reset,
                handle: "reader-old".to_owned(),
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            })
            .await
            .expect("queues old session data");
        control_tx
            .send(ControlResponse {
                payload: b"{}".to_vec(),
                handoff: None,
                closes_session: true,
            })
            .await
            .expect("queues close response");
        drop(control_tx);
        drop(data_tx);

        let (writer, mut reader) = duplex(4096);
        let (rearm_tx, mut rearm_rx) = tokio::sync::mpsc::unbounded_channel();
        writer_loop(writer, control_rx, data_rx, 1024, 1024, rearm_tx)
            .await
            .expect("writer completes");
        assert_eq!(rearm_rx.recv().await, Some(()));

        let frame = read_frame(&mut reader, 1024, 1024)
            .await
            .expect("close response framing is valid")
            .expect("close response is present");
        assert!(matches!(frame, InboundFrame::Control(payload) if payload == b"{}"));
        assert!(
            read_frame(&mut reader, 1024, 1024)
                .await
                .expect("writer closes cleanly")
                .is_none()
        );
    }

    #[tokio::test]
    async fn initialization_timeout_closes_an_idle_transport() {
        let config = Config::for_test("env-test");
        let daemon = Arc::new(Daemon::new(&config).expect("daemon builds"));
        let (_input_client, input_server) = duplex(1024);
        let (output_server, mut output_client) = duplex(1024);

        serve_io(
            input_server,
            output_server,
            daemon,
            &config,
            pending::<io::Result<()>>(),
        )
        .await
        .expect("idle initialization timeout is a clean session close");

        let mut response = Vec::new();
        output_client
            .read_to_end(&mut response)
            .await
            .expect("output pipe closes");
        assert!(response.is_empty());
    }

    #[tokio::test]
    async fn reuses_one_stdio_carrier_after_the_initialization_timeout() {
        let config = Config::for_test("env-test");
        let initialization_timeout = config.initialization_timeout;
        let daemon = Arc::new(Daemon::new(&config).expect("daemon builds"));
        let (mut client, server) = duplex(128 * 1024);
        let (server_reader, server_writer) = tokio::io::split(server);
        let serving = tokio::spawn(async move {
            serve_io(
                server_reader,
                server_writer,
                daemon,
                &config,
                pending::<io::Result<()>>(),
            )
            .await
        });

        let mut generation = None;
        for session in 0..2 {
            if session == 1 {
                tokio::time::sleep(initialization_timeout * 3).await;
                assert!(!serving.is_finished());
            }
            let initialize = serde_json::json!({
                "jsonrpc": "2.0",
                "id": session * 2 + 1,
                "method": "initialize",
                "params": {
                    "supported_protocol_versions": ["0.1"],
                    "client": {"name": "stdio-test", "version": "1"},
                    "expected_environment_id": "env-test",
                    "required_methods": []
                }
            })
            .to_string();
            write_outer_frame(
                &mut client,
                super::JSON_CONTENT_TYPE,
                initialize.as_bytes(),
                64 * 1024,
            )
            .await
            .expect("initialize frame writes");
            let initialized = read_frame(&mut client, 64 * 1024, 64 * 1024)
                .await
                .expect("initialize response frame is valid")
                .expect("initialize response is present");
            let InboundFrame::Control(initialized) = initialized else {
                panic!("initialize response is a control frame");
            };
            let initialized: serde_json::Value =
                serde_json::from_slice(&initialized).expect("initialize response is JSON");
            let current_generation = initialized["result"]["descriptor"]["generation"]
                .as_u64()
                .expect("initialize returns a generation");
            assert_eq!(
                *generation.get_or_insert(current_generation),
                current_generation
            );

            let close = serde_json::json!({
                "jsonrpc": "2.0",
                "id": session * 2 + 2,
                "method": "session.close",
                "params": {
                    "context": {"operation_id": format!("close-{session}")}
                }
            })
            .to_string();
            write_outer_frame(
                &mut client,
                super::JSON_CONTENT_TYPE,
                close.as_bytes(),
                64 * 1024,
            )
            .await
            .expect("session.close frame writes");
            let closed = read_frame(&mut client, 64 * 1024, 64 * 1024)
                .await
                .expect("session.close response frame is valid")
                .expect("session.close response is present");
            let InboundFrame::Control(closed) = closed else {
                panic!("session.close response is a control frame");
            };
            let closed: serde_json::Value =
                serde_json::from_slice(&closed).expect("session.close response is JSON");
            assert_eq!(closed["result"]["closed"], true);
        }

        client.shutdown().await.expect("client closes carrier");
        serving
            .await
            .expect("stdio task joins")
            .expect("carrier EOF shuts down cleanly");
    }

    #[tokio::test]
    async fn sequential_session_starts_after_all_old_transfer_frames_are_fenced() {
        let tree = TempTree::new();
        let native_root = tree.child("native");
        fs::create_dir(&native_root).expect("creates native root");
        fs::write(native_root.join("source.bin"), vec![b'x'; 1024 * 1024])
            .expect("writes transfer source");
        let mut config = Config::for_test("env-test");
        config.root_mount_id = Some("workspace".to_owned());
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root,
            writable: false,
            allow_command_execution: false,
            max_file_bytes: 2 * 1024 * 1024,
            allowed_operations: vec!["open_reader".to_owned()],
        });
        let daemon = Arc::new(Daemon::new(&config).expect("daemon builds"));
        let (mut input_client, input_server) = duplex(2 * 1024 * 1024);
        let (output_server, mut output_client) = duplex(8 * 1024);
        let serving = tokio::spawn(async move {
            serve_io(
                input_server,
                output_server,
                daemon,
                &config,
                pending::<io::Result<()>>(),
            )
            .await
        });

        write_json_frame(
            &mut input_client,
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "supported_protocol_versions": ["0.1"],
                    "client": {"name": "stdio-test", "version": "1"},
                    "expected_environment_id": "env-test",
                    "required_methods": [
                        "environment.readiness",
                        "file.open_reader",
                        "session.close"
                    ]
                }
            }),
        )
        .await;
        let initialized = read_frame(&mut output_client, 64 * 1024, 4 * 1024 * 1024)
            .await
            .expect("initialize response is valid")
            .expect("initialize response is present");
        assert!(matches!(initialized, InboundFrame::Control(_)));

        write_json_frame(
            &mut input_client,
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 20,
                "method": "environment.readiness",
                "params": {
                    "context": {
                        "operation_id": "readiness-old-session",
                        "timeout_ms": 2_000
                    }
                }
            }),
        )
        .await;
        let readiness = read_frame(&mut output_client, 64 * 1024, 4 * 1024 * 1024)
            .await
            .expect("readiness response is valid")
            .expect("readiness response is present");
        let InboundFrame::Control(readiness) = readiness else {
            panic!("readiness response is control JSON");
        };
        let readiness: serde_json::Value =
            serde_json::from_slice(&readiness).expect("readiness response is JSON");
        assert_eq!(readiness["result"]["ready"], true);

        write_json_frame(
            &mut input_client,
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "file.open_reader",
                "params": {
                    "context": {"operation_id": "open-old-reader"},
                    "path": {"mount_id": "workspace", "path": "/source.bin"}
                }
            }),
        )
        .await;
        let opened = read_frame(&mut output_client, 64 * 1024, 4 * 1024 * 1024)
            .await
            .expect("open response is valid")
            .expect("open response is present");
        let InboundFrame::Control(opened) = opened else {
            panic!("open response is control JSON");
        };
        let opened: serde_json::Value =
            serde_json::from_slice(&opened).expect("open response is JSON");
        let handle = opened["result"]["reader"]
            .as_str()
            .expect("open response contains a reader handle")
            .to_owned();
        let attach = encode_data_frame(
            &DataFrame {
                kind: DataFrameKind::Attach,
                handle,
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            },
            4 * 1024 * 1024,
        )
        .expect("attach frame encodes");
        write_outer_frame(
            &mut input_client,
            super::DATA_CONTENT_TYPE,
            &attach,
            4 * 1024 * 1024,
        )
        .await
        .expect("attach frame writes");
        write_json_frame(
            &mut input_client,
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session.close",
                "params": {"context": {"operation_id": "close-old-session"}}
            }),
        )
        .await;

        loop {
            match read_frame(&mut output_client, 64 * 1024, 4 * 1024 * 1024)
                .await
                .expect("old session output is framed")
                .expect("close response is present")
            {
                InboundFrame::Data(_) => {}
                InboundFrame::Control(payload) => {
                    let response: serde_json::Value =
                        serde_json::from_slice(&payload).expect("control response is JSON");
                    if response["id"] == 3 {
                        assert_eq!(response["result"]["closed"], true);
                        break;
                    }
                }
            }
        }

        write_json_frame(
            &mut input_client,
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 4,
                "method": "initialize",
                "params": {
                    "supported_protocol_versions": ["0.1"],
                    "client": {"name": "stdio-test", "version": "1"},
                    "expected_environment_id": "env-test",
                    "required_methods": ["session.close"]
                }
            }),
        )
        .await;
        let next = read_frame(&mut output_client, 64 * 1024, 4 * 1024 * 1024)
            .await
            .expect("next session output is framed")
            .expect("next initialize response is present");
        let InboundFrame::Control(next) = next else {
            panic!("no old data frame follows the close response");
        };
        let next: serde_json::Value =
            serde_json::from_slice(&next).expect("next initialize response is JSON");
        assert_eq!(next["id"], 4);
        assert!(next.get("result").is_some());

        write_json_frame(
            &mut input_client,
            serde_json::json!({
                "jsonrpc": "2.0",
                "id": 5,
                "method": "session.close",
                "params": {"context": {"operation_id": "close-next-session"}}
            }),
        )
        .await;
        let closed = read_frame(&mut output_client, 64 * 1024, 4 * 1024 * 1024)
            .await
            .expect("second close response is framed")
            .expect("second close response is present");
        assert!(matches!(closed, InboundFrame::Control(_)));

        input_client
            .shutdown()
            .await
            .expect("client closes carrier");
        serving
            .await
            .expect("stdio task joins")
            .expect("carrier EOF shuts down cleanly");
    }

    async fn run_close_barrier_under_stdout_backpressure(signal_shutdown: bool) {
        let config = Config::for_test("env-test");
        let daemon = Arc::new(Daemon::new(&config).expect("daemon builds"));
        let (mut input_client, input_server) = duplex(512 * 1024);
        let (output_server, mut output_client) = duplex(1024);
        let (shutdown_tx, shutdown_rx) = tokio::sync::oneshot::channel();
        let serving = tokio::spawn(async move {
            serve_io(input_server, output_server, daemon, &config, async move {
                shutdown_rx.await.map_err(|_| {
                    io::Error::new(io::ErrorKind::BrokenPipe, "test shutdown sender dropped")
                })
            })
            .await
        });

        let initialize = serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "supported_protocol_versions": ["0.1"],
                "client": {"name": "stdio-test", "version": "1"},
                "expected_environment_id": "env-test",
                "required_methods": []
            }
        })
        .to_string();
        write_outer_frame(
            &mut input_client,
            super::JSON_CONTENT_TYPE,
            initialize.as_bytes(),
            64 * 1024,
        )
        .await
        .expect("initialize frame writes");
        read_frame(&mut output_client, 64 * 1024, 64 * 1024)
            .await
            .expect("initialize response is valid")
            .expect("initialize response is present");

        for index in 0..128 {
            let describe = serde_json::json!({
                "jsonrpc": "2.0",
                "id": index + 2,
                "method": "environment.describe",
                "params": {"context": {"operation_id": format!("describe-{index}")}}
            })
            .to_string();
            write_outer_frame(
                &mut input_client,
                super::JSON_CONTENT_TYPE,
                describe.as_bytes(),
                64 * 1024,
            )
            .await
            .expect("describe frame writes");
        }
        let close = serde_json::json!({
            "jsonrpc": "2.0",
            "id": 1000,
            "method": "session.close",
            "params": {"context": {"operation_id": "close-under-backpressure"}}
        })
        .to_string();
        write_outer_frame(
            &mut input_client,
            super::JSON_CONTENT_TYPE,
            close.as_bytes(),
            64 * 1024,
        )
        .await
        .expect("close frame writes");
        tokio::time::sleep(Duration::from_millis(20)).await;
        if signal_shutdown {
            shutdown_tx.send(()).expect("signals shutdown");
        }

        let result = tokio::time::timeout(Duration::from_secs(4), serving)
            .await
            .expect("shutdown remains bounded")
            .expect("stdio task joins");
        assert!(
            result.is_err(),
            "stalled stdout cannot complete a clean carrier drain"
        );
    }

    #[tokio::test]
    async fn shutdown_interrupts_a_close_barrier_under_stdout_backpressure() {
        run_close_barrier_under_stdout_backpressure(true).await;
    }

    #[tokio::test]
    async fn close_barrier_deadline_covers_response_flush_and_rearm() {
        run_close_barrier_under_stdout_backpressure(false).await;
    }

    #[tokio::test]
    async fn reads_content_length_control_frame() {
        let (mut client, mut server) = duplex(1024);
        client
            .write_all(
                b"Content-Length: 2\r\nContent-Type: application/json; charset=utf-8\r\n\r\n{}",
            )
            .await
            .expect("fixture writes");

        let frame = read_frame(&mut server, 16, 64)
            .await
            .expect("frame is valid")
            .expect("frame is present");
        assert!(matches!(frame, InboundFrame::Control(body) if body == b"{}"));
    }

    #[tokio::test]
    async fn rejects_noncanonical_or_duplicate_lengths() {
        for header in [
            b"Content-Length: 02\r\n\r\n{}".as_slice(),
            b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}".as_slice(),
        ] {
            let (mut client, mut server) = duplex(1024);
            client.write_all(header).await.expect("fixture writes");
            assert!(read_frame(&mut server, 16, 64).await.is_err());
        }
    }

    #[tokio::test]
    async fn enforces_independent_control_and_data_limits() {
        let (mut client, mut server) = duplex(1024);
        client
            .write_all(b"Content-Length: 17\r\nContent-Type: application/json\r\n\r\n")
            .await
            .expect("fixture writes");
        assert!(read_frame(&mut server, 16, 64).await.is_err());

        let (mut client, mut server) = duplex(1024);
        client
            .write_all(b"Content-Length: 65\r\nContent-Type: application/vnd.a13n.eip-data\r\n\r\n")
            .await
            .expect("fixture writes");
        assert!(read_frame(&mut server, 128, 64).await.is_err());
    }

    #[tokio::test]
    async fn rejects_an_overlong_header_line_without_waiting_for_eof() {
        let (mut client, mut server) = duplex(8 * 1024);
        client
            .write_all(&vec![b'X'; super::MAX_HEADER_LINE_BYTES + 1])
            .await
            .expect("fixture writes");

        let result = tokio::time::timeout(
            std::time::Duration::from_millis(100),
            read_frame(&mut server, 16, 64),
        )
        .await
        .expect("line limit is enforced before EOF");
        let Err(error) = result else {
            panic!("overlong line is rejected");
        };
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }

    #[tokio::test]
    async fn rejects_excessive_header_count_while_reading() {
        let (mut client, mut server) = duplex(8 * 1024);
        let headers = (0..=super::MAX_HEADER_COUNT)
            .map(|index| format!("X-{index}: value\r\n"))
            .collect::<String>();
        client
            .write_all(headers.as_bytes())
            .await
            .expect("fixture writes");

        let result = read_frame(&mut server, 16, 64).await;
        let Err(error) = result else {
            panic!("excessive header count is rejected");
        };
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }

    #[tokio::test]
    async fn writes_canonical_control_frame() {
        let (mut client, mut server) = duplex(1024);
        write_outer_frame(&mut client, super::JSON_CONTENT_TYPE, b"{}", 16)
            .await
            .expect("response writes");
        client.shutdown().await.expect("writer shuts down");

        let mut bytes = Vec::new();
        server
            .read_to_end(&mut bytes)
            .await
            .expect("response reads");
        assert_eq!(
            bytes,
            b"Content-Length: 2\r\nContent-Type: application/json; charset=utf-8\r\n\r\n{}"
        );
    }
}
