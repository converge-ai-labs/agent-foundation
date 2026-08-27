use std::{collections::HashSet, future::Future, io, sync::Arc, time::Duration};

use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader},
    sync::{Semaphore, mpsc, watch},
    task::JoinSet,
    time::{MissedTickBehavior, timeout},
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
const MAX_CONTROL_BURST: usize = 8;

enum InboundFrame {
    Control(Vec<u8>),
    Data(DataFrame),
}

struct ControlResponse {
    payload: Vec<u8>,
    handoff: Option<ActiveResponseHandoff>,
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
    let data_capacity = max_transfers.saturating_mul(2).max(2);
    let (data_tx, data_rx) = mpsc::channel::<DataFrame>(data_capacity);
    let (inbound_data_tx, mut inbound_data_rx) = mpsc::channel::<DataFrame>(data_capacity);
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
            while let Some(frame) = inbound_data_rx.recv().await {
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
    let mut maintenance_closed = daemon.subscribe_closed();
    let maintenance_task = tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_millis(100));
        interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            tokio::select! {
                changed = maintenance_closed.changed() => {
                    if changed.is_err() || *maintenance_closed.borrow() {
                        break;
                    }
                }
                _ = interval.tick() => maintenance_daemon.maintenance().await,
            }
        }
    });

    let admission = Arc::new(Semaphore::new(max_concurrency));
    let mut requests = JoinSet::new();
    let mut closed = daemon.subscribe_closed();
    let mut shutdown = std::pin::pin!(shutdown);
    let mut first_frame = true;

    loop {
        if *closed.borrow() {
            break;
        }
        let read_timeout = if first_frame {
            config.initialization_timeout
        } else if daemon.has_active_file_transfers() {
            Duration::from_millis(config.limits.max_file_transfer_duration_ms)
        } else {
            config.session_idle_timeout
        };
        let frame = tokio::select! {
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
            changed = inbound_stopped_rx.changed() => {
                match changed {
                    Ok(()) | Err(_) => break,
                }
            }
            signal = &mut shutdown => {
                signal?;
                break;
            }
            frame = timeout(
                read_timeout,
                read_frame(&mut reader, max_request_bytes, max_data_bytes),
            ) => {
                match frame {
                    Ok(frame) => frame?,
                    Err(_) => break,
                }
            }
        };
        let Some(frame) = frame else {
            break;
        };

        if first_frame {
            let InboundFrame::Control(frame) = frame else {
                return Err(invalid_data("initialize must be the first stdio frame"));
            };
            first_frame = false;
            let payload = String::from_utf8(frame)
                .map_err(|_| invalid_data("stdio control body must be UTF-8 JSON"))?;
            let response = daemon.handle_payload_for_carrier(&payload).await;
            let (payload, handoff, _) = response.into_parts();
            if control_tx
                .send(ControlResponse { payload, handoff })
                .await
                .is_err()
            {
                break;
            }
            continue;
        }

        match frame {
            InboundFrame::Control(frame) => {
                let payload = String::from_utf8(frame)
                    .map_err(|_| invalid_data("stdio control body must be UTF-8 JSON"))?;
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
                    let (payload, handoff, _) = response.into_parts();
                    let _ = responses.send(ControlResponse { payload, handoff }).await;
                    drop(permit);
                });
            }
            InboundFrame::Data(frame) => {
                let sent = timeout(transfer_timeout, inbound_data_tx.send(frame)).await;
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
    let request_result = match timeout(SHUTDOWN_DRAIN_TIMEOUT, async {
        while let Some(result) = requests.join_next().await {
            result
                .map_err(|error| io::Error::other(format!("stdio request task failed: {error}")))?;
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

async fn writer_loop<W>(
    mut writer: W,
    mut controls: mpsc::Receiver<ControlResponse>,
    mut data: mpsc::Receiver<DataFrame>,
    max_control_bytes: usize,
    max_data_bytes: usize,
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
                        write_outer_frame(
                            &mut writer,
                            JSON_CONTENT_TYPE,
                            &response.payload,
                            max_control_bytes,
                        )
                        .await?;
                        if let Some(handoff) = response.handoff {
                            handoff.complete();
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
    use std::{future::pending, io, sync::Arc};

    use tokio::io::{AsyncReadExt, AsyncWriteExt, duplex};

    use crate::{config::Config, daemon::Daemon};

    use super::{InboundFrame, read_frame, serve_io, write_outer_frame};

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
