use std::{collections::HashSet, future::Future, io, sync::Arc, time::Duration};

use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader},
    sync::mpsc,
    task::JoinSet,
    time::{MissedTickBehavior, timeout},
};

use crate::{
    config::Config,
    daemon::{Daemon, ResponseHandoff},
    data_dispatch::DataDispatcher,
    eip::{DataFrame, DataFrameKind, decode_data_frame, encode_data_frame},
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
    handoff: Option<ResponseHandoff>,
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
    let max_request_bytes = config.limits.max_request_bytes as usize;
    let max_response_bytes = config.limits.max_response_bytes as usize;
    let max_data_bytes = config.limits.max_transfer_frame_bytes as usize;
    let capacity = config.limits.max_concurrent_operations as usize;
    let (control_tx, control_rx) = mpsc::channel::<ControlResponse>(capacity);
    let (data_tx, data_rx) =
        mpsc::channel::<DataFrame>(config.limits.max_concurrent_file_transfers as usize * 2);
    let carrier = daemon.carrier(data_tx.clone());
    let mut inbound_data =
        DataDispatcher::new(daemon.clone(), carrier.clone(), data_tx.clone(), config);
    let mut writer_task = tokio::spawn(writer_loop(
        writer,
        control_rx,
        data_rx,
        max_response_bytes,
        max_data_bytes,
    ));
    let maintenance_daemon = daemon.clone();
    let maintenance = tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_millis(100));
        interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            interval.tick().await;
            maintenance_daemon.maintenance().await;
        }
    });
    let mut requests = JoinSet::new();
    let mut shutdown = std::pin::pin!(shutdown);
    let outcome = async {
        let first = timeout(
            config.initialization_timeout,
            read_frame(&mut reader, max_request_bytes, max_data_bytes),
        )
        .await
        .map_err(|_| {
            io::Error::new(io::ErrorKind::TimedOut, "Device initialization timed out")
        })??;
        let first = match first {
            None => return Ok(()),
            Some(InboundFrame::Control(first)) => first,
            Some(InboundFrame::Data(_)) => {
                return Err(invalid_data("initialize must be the first frame"));
            }
        };
        let payload =
            String::from_utf8(first).map_err(|_| invalid_data("control must be UTF-8"))?;
        let (payload, handoff) = daemon
            .handle_payload_for_carrier(&carrier, &payload)
            .await
            .into_parts();
        control_tx
            .send(ControlResponse { payload, handoff })
            .await
            .map_err(|_| io::ErrorKind::BrokenPipe)?;
        if !carrier.initialized() {
            return Ok(());
        }
        loop {
            while let Some(result) = requests.try_join_next() {
                result.map_err(io::Error::other)?;
            }
            // The outer shutdown select cancels the entire carrier, never restarts a partial frame.
            let frame = read_frame(&mut reader, max_request_bytes, max_data_bytes).await?;
            match frame {
                None => break,
                Some(InboundFrame::Control(bytes)) => {
                    let payload = String::from_utf8(bytes)
                        .map_err(|_| invalid_data("control must be UTF-8"))?;
                    let permit = daemon.admit_payload(&payload);
                    if permit.is_none() {
                        let payload = daemon.busy_response(&payload);
                        timeout(
                            SHUTDOWN_DRAIN_TIMEOUT,
                            control_tx.send(ControlResponse {
                                payload,
                                handoff: None,
                            }),
                        )
                        .await
                        .map_err(|_| io::ErrorKind::TimedOut)?
                        .map_err(|_| io::ErrorKind::BrokenPipe)?;
                        continue;
                    }
                    let pending = daemon.track_pending_payload(&payload);
                    let daemon = daemon.clone();
                    let carrier = carrier.clone();
                    let responses = control_tx.clone();
                    requests.spawn(async move {
                        let (_permit, _pending) = (permit, pending);
                        let (payload, handoff) = daemon
                            .handle_payload_for_carrier(&carrier, &payload)
                            .await
                            .into_parts();
                        let _ = responses.send(ControlResponse { payload, handoff }).await;
                    });
                }
                Some(InboundFrame::Data(frame)) => {
                    if let Err(error) = inbound_data.enqueue(frame.clone()) {
                        let reset = DataFrame {
                            kind: DataFrameKind::Reset,
                            session_id: frame.session_id,
                            handle: frame.handle,
                            offset: frame.offset,
                            payload: Vec::new(),
                            reset_status: Some(reset_status(error)),
                        };
                        timeout(SHUTDOWN_DRAIN_TIMEOUT, data_tx.send(reset))
                            .await
                            .map_err(|_| io::ErrorKind::TimedOut)?
                            .map_err(|_| io::ErrorKind::BrokenPipe)?;
                    }
                }
            }
        }
        Ok(())
    };
    let outcome = tokio::select! {
        signal = &mut shutdown => signal,
        result = outcome => result,
    };
    carrier.close();
    drop(inbound_data);
    maintenance.abort();
    let _ = maintenance.await;
    let clean = daemon.drain(Duration::from_secs(10)).await;
    requests.abort_all();
    while requests.join_next().await.is_some() {}
    drop(control_tx);
    drop(data_tx);
    drop(carrier);
    let written = match timeout(SHUTDOWN_DRAIN_TIMEOUT, &mut writer_task).await {
        Ok(result) => result.map_err(io::Error::other)?,
        Err(_) => {
            writer_task.abort();
            Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "response drain timed out",
            ))
        }
    };
    outcome?;
    if !clean {
        return Err(io::Error::other("Device cleanup incomplete"));
    }
    written
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
        config::Config,
        daemon::Daemon,
        eip::{DataFrame, DataFrameKind},
        operation::random_selector,
    };

    use super::{
        ControlResponse, InboundFrame, encode_data_frame, read_frame, serve_io, write_outer_frame,
        writer_loop,
    };

    struct TempTree(PathBuf);

    impl TempTree {
        fn new() -> Self {
            let path = std::env::temp_dir()
                .join(random_selector("a13n-envd-stdio-test").expect("random temporary directory"));
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

    #[tokio::test]
    async fn writer_preserves_sibling_data_when_control_responses_are_queued() {
        let (control_tx, control_rx) = tokio::sync::mpsc::channel(2);
        let (data_tx, data_rx) = tokio::sync::mpsc::channel(2);
        let frame = DataFrame {
            session_id: "session-b".to_owned(),
            kind: DataFrameKind::Chunk,
            handle: "reader-b".to_owned(),
            offset: 0,
            payload: b"sibling".to_vec(),
            reset_status: None,
        };
        data_tx.send(frame.clone()).await.unwrap();
        control_tx
            .send(ControlResponse {
                payload: br#"{"eip_session":"session-a","result":{"closed":true}}"#.to_vec(),
                handoff: None,
            })
            .await
            .unwrap();
        drop(control_tx);
        drop(data_tx);
        let (writer, mut reader) = duplex(4096);
        writer_loop(writer, control_rx, data_rx, 1024, 1024)
            .await
            .unwrap();
        assert!(matches!(
            read_frame(&mut reader, 1024, 1024).await.unwrap(),
            Some(InboundFrame::Control(_))
        ));
        let Some(InboundFrame::Data(received)) = read_frame(&mut reader, 1024, 1024).await.unwrap()
        else {
            panic!("sibling data remains queued")
        };
        assert_eq!(received, frame);
        assert!(read_frame(&mut reader, 1024, 1024).await.unwrap().is_none());
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
        .expect_err("an uninitialized carrier expires");

        let mut response = Vec::new();
        output_client
            .read_to_end(&mut response)
            .await
            .expect("output pipe closes");
        assert!(response.is_empty());
    }

    async fn read_json<R: tokio::io::AsyncRead + Unpin>(reader: &mut R) -> serde_json::Value {
        let Some(InboundFrame::Control(bytes)) =
            read_frame(reader, 64 * 1024, 64 * 1024).await.unwrap()
        else {
            panic!("control response expected")
        };
        serde_json::from_slice(&bytes).unwrap()
    }

    #[tokio::test]
    async fn multiplexed_sessions_keep_sibling_transfer_and_allow_open_without_reinitialize() {
        let tree = TempTree::new();
        let source = tree.child("source.bin");
        fs::write(&source, b"sibling-transfer").unwrap();
        let config = Config::for_test("env-test");
        let cwd = config.default_working_directory.clone();
        let daemon = Arc::new(Daemon::new(&config).unwrap());
        let (mut client, server) = duplex(128 * 1024);
        let (reader, writer) = tokio::io::split(server);
        let serving = tokio::spawn(async move {
            serve_io(reader, writer, daemon, &config, pending::<io::Result<()>>()).await
        });
        write_json_frame(&mut client, serde_json::json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
            "supported_protocol_versions":["0.1"],"client":{"name":"stdio-test","version":"1"},"expected_device_id":"env-test"
        }})).await;
        let initialized = read_json(&mut client).await;
        let generation = initialized["result"]["descriptor"]["generation"].clone();
        let mut sessions = Vec::new();
        for id in [2, 3] {
            write_json_frame(&mut client, serde_json::json!({"jsonrpc":"2.0","id":id,"method":"session.open","params":{
                "expected_device_id":"env-test","expected_generation":generation,"protocol_version":"0.1","working_directory":cwd,"required_methods":[]
            }})).await;
            let opened = read_json(&mut client).await;
            let session = opened["result"]["descriptor"]["session_id"]
                .as_str()
                .unwrap()
                .to_owned();
            write_json_frame(&mut client, serde_json::json!({"jsonrpc":"2.0","id":id+10,"eip_session":session,"method":"environment.readiness","params":{"context":{"operation_id":"ready"}}})).await;
            assert_eq!(read_json(&mut client).await["result"]["ready"], true);
            sessions.push(session);
        }
        let mut handles = Vec::new();
        for session in &sessions {
            write_json_frame(&mut client, serde_json::json!({"jsonrpc":"2.0","id":20,"eip_session":session,"method":"file.open_reader","params":{
                "context":{"operation_id":"open"},"path":{"path":crate::device_path::from_native(&source).unwrap()}
            }})).await;
            handles.push(
                read_json(&mut client).await["result"]["reader"]
                    .as_str()
                    .unwrap()
                    .to_owned(),
            );
        }
        write_json_frame(&mut client, serde_json::json!({"jsonrpc":"2.0","id":30,"eip_session":sessions[0],"method":"session.close","params":{}})).await;
        assert_eq!(read_json(&mut client).await["result"]["closed"], true);
        // A's old handle is fenced without disrupting B on the same data carrier.
        for index in [0, 1] {
            let frame = DataFrame {
                session_id: sessions[index].clone(),
                kind: DataFrameKind::Attach,
                handle: handles[index].clone(),
                offset: 0,
                payload: Vec::new(),
                reset_status: None,
            };
            let bytes = encode_data_frame(&frame, 65536).unwrap();
            write_outer_frame(&mut client, super::DATA_CONTENT_TYPE, &bytes, 65536)
                .await
                .unwrap();
            let mut received = Vec::new();
            loop {
                let Some(InboundFrame::Data(frame)) =
                    read_frame(&mut client, 65536, 65536).await.unwrap()
                else {
                    panic!("data frame expected")
                };
                assert_eq!(frame.session_id, sessions[index]);
                if index == 0 {
                    assert_eq!(frame.kind, DataFrameKind::Reset);
                    break;
                }
                match frame.kind {
                    DataFrameKind::Attached => {}
                    DataFrameKind::Chunk => {
                        received.extend(frame.payload);
                        let credit = DataFrame {
                            session_id: frame.session_id,
                            kind: DataFrameKind::Credit,
                            handle: frame.handle,
                            offset: received.len() as u64,
                            payload: Vec::new(),
                            reset_status: None,
                        };
                        let bytes = encode_data_frame(&credit, 65536).unwrap();
                        write_outer_frame(&mut client, super::DATA_CONTENT_TYPE, &bytes, 65536)
                            .await
                            .unwrap();
                    }
                    DataFrameKind::End => break,
                    other => panic!("unexpected {other:?}"),
                }
            }
            if index == 1 {
                assert_eq!(received, b"sibling-transfer");
            }
        }
        write_json_frame(&mut client, serde_json::json!({"jsonrpc":"2.0","id":40,"method":"session.open","params":{
            "expected_device_id":"env-test","expected_generation":generation,"protocol_version":"0.1","working_directory":cwd,"required_methods":[]
        }})).await;
        let opened = read_json(&mut client).await;
        assert!(opened["result"]["descriptor"]["session_id"].is_string());
        assert_ne!(opened["result"]["descriptor"]["session_id"], sessions[0]);
        client.shutdown().await.unwrap();
        serving.await.unwrap().unwrap();
    }

    async fn run_shutdown_under_stdout_backpressure(signal_shutdown: bool, data_storm: bool) {
        let mut config = Config::for_test("env-test");
        config.limits.max_device_concurrent_operations = 2;
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
                "expected_device_id": "env-test"
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

        for index in 0..512 {
            if data_storm {
                super::write_data_frame(
                    &mut input_client,
                    &DataFrame {
                        kind: DataFrameKind::Attach,
                        session_id: "session-unknown".into(),
                        handle: "transfer-unknown".into(),
                        offset: 0,
                        payload: Vec::new(),
                        reset_status: None,
                    },
                    64 * 1024,
                )
                .await
                .unwrap();
                continue;
            }
            let describe = serde_json::json!({
                "jsonrpc": "2.0",
                "id": index + 2,
                "method": "device.describe",
                "params": {}
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
        tokio::time::sleep(Duration::from_millis(20)).await;
        if signal_shutdown {
            shutdown_tx.send(()).expect("signals shutdown");
        } else {
            input_client.shutdown().await.unwrap();
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
    async fn shutdown_interrupts_stdout_backpressure() {
        run_shutdown_under_stdout_backpressure(true, false).await;
    }

    #[tokio::test]
    async fn eof_drain_deadline_covers_response_flush() {
        run_shutdown_under_stdout_backpressure(false, false).await;
    }

    #[tokio::test]
    async fn reset_queue_saturation_observes_signal_and_eof() {
        run_shutdown_under_stdout_backpressure(true, true).await;
        run_shutdown_under_stdout_backpressure(false, true).await;
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
