use std::{
    future::pending,
    io::{self, BufReader},
    sync::Arc,
    time::{Duration, Instant},
};

use futures_util::{SinkExt, StreamExt};
use tokio::{
    net::TcpStream,
    sync::{Semaphore, mpsc, watch},
    task::JoinSet,
    time::{MissedTickBehavior, timeout},
};
use tokio_tungstenite::{
    Connector, MaybeTlsStream, WebSocketStream, connect_async_tls_with_config,
    tungstenite::{
        Error as WebSocketError, Message,
        client::IntoClientRequest,
        http::{HeaderValue, StatusCode, header},
        protocol::WebSocketConfig,
    },
};

use crate::{
    config::{Config, ReverseWebSocketConfig},
    daemon::Daemon,
    eip::{DataFrame, DataFrameKind, decode_data_frame, encode_data_frame},
    operation::ActiveResponseHandoff,
    transfer::reset_status,
};

const EIP_SUBPROTOCOL: &str = "eip.v1";
const MAX_CREDENTIAL_BYTES: usize = 8 * 1024;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const PING_INTERVAL: Duration = Duration::from_secs(20);
const PONG_TIMEOUT: Duration = Duration::from_secs(10);
const RECONNECT_BASE: Duration = Duration::from_millis(250);
const RECONNECT_CAP: Duration = Duration::from_secs(30);
const STABILITY_INTERVAL: Duration = Duration::from_secs(60);
const SESSION_DRAIN_TIMEOUT: Duration = Duration::from_secs(1);
const MAX_CONTROL_BURST: usize = 8;

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

struct ControlResponse {
    payload: Vec<u8>,
    handoff: Option<ActiveResponseHandoff>,
    closes_session: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ConnectionFailure {
    Transient,
    Fatal(&'static str),
}

pub(crate) async fn serve(
    daemon: Arc<Daemon>,
    config: &Config,
    websocket: &ReverseWebSocketConfig,
) -> io::Result<()> {
    let connector = build_tls_connector(websocket)?;
    let (shutdown_sender, shutdown) = watch::channel(false);
    let signal_task = tokio::spawn(async move {
        let _ = shutdown_signal().await;
        shutdown_sender.send_replace(true);
    });

    let mut backoff = RECONNECT_BASE;
    let result = loop {
        if *shutdown.borrow() {
            break Ok(());
        }
        let credential = match read_credential(&websocket.credential_file).await {
            Ok(credential) => credential,
            Err(error) => break Err(error),
        };
        let connected = connect(
            websocket,
            &credential,
            connector.clone(),
            config,
            shutdown.clone(),
        )
        .await;
        drop(credential);

        match connected {
            Ok(None) => break Ok(()),
            Ok(Some(socket)) => {
                let started = Instant::now();
                match serve_connection(socket, Arc::clone(&daemon), config, shutdown.clone()).await
                {
                    Ok(()) | Err(ConnectionFailure::Transient) => {
                        if started.elapsed() >= STABILITY_INTERVAL {
                            backoff = RECONNECT_BASE;
                        }
                    }
                    Err(ConnectionFailure::Fatal(class)) => {
                        break Err(io::Error::new(io::ErrorKind::InvalidData, class));
                    }
                }
            }
            Err(ConnectionFailure::Transient) => {}
            Err(ConnectionFailure::Fatal(class)) => {
                break Err(io::Error::new(io::ErrorKind::InvalidData, class));
            }
        }

        let delay = full_jitter(backoff);
        backoff = backoff.saturating_mul(2).min(RECONNECT_CAP);
        let mut reconnect_shutdown = shutdown.clone();
        tokio::select! {
            _ = tokio::time::sleep(delay) => {}
            changed = reconnect_shutdown.changed() => {
                if changed.is_err() || *reconnect_shutdown.borrow() {
                    break Ok(());
                }
            }
        }
    };

    signal_task.abort();
    let _ = signal_task.await;
    let session_closed = daemon.transport_closed(SESSION_DRAIN_TIMEOUT).await;
    let processes_drained = daemon.drain_processes(SESSION_DRAIN_TIMEOUT).await;
    let operations_drained = daemon.drain_owned_operations(SESSION_DRAIN_TIMEOUT).await;
    if !session_closed || !processes_drained || !operations_drained {
        return Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "reverse WebSocket generation drain exceeded its deadline",
        ));
    }
    result
}

async fn connect(
    websocket: &ReverseWebSocketConfig,
    credential: &str,
    connector: Connector,
    config: &Config,
    mut shutdown: watch::Receiver<bool>,
) -> Result<Option<Socket>, ConnectionFailure> {
    let mut request = websocket
        .endpoint
        .as_str()
        .into_client_request()
        .map_err(|_| ConnectionFailure::Fatal("invalid reverse WebSocket endpoint"))?;
    let mut authorization = HeaderValue::from_str(&format!("Bearer {credential}"))
        .map_err(|_| ConnectionFailure::Fatal("invalid attachment credential"))?;
    authorization.set_sensitive(true);
    request
        .headers_mut()
        .insert(header::AUTHORIZATION, authorization);
    request.headers_mut().insert(
        header::SEC_WEBSOCKET_PROTOCOL,
        HeaderValue::from_static(EIP_SUBPROTOCOL),
    );

    let max_message_bytes = usize::try_from(
        config
            .limits
            .max_request_bytes
            .max(config.limits.max_response_bytes)
            .max(config.limits.max_transfer_frame_bytes),
    )
    .map_err(|_| ConnectionFailure::Fatal("WebSocket message limit does not fit this platform"))?;
    let socket_config = WebSocketConfig::default()
        .max_message_size(Some(max_message_bytes))
        .max_frame_size(Some(max_message_bytes))
        .max_write_buffer_size(
            max_message_bytes
                .saturating_mul(2)
                .max(max_message_bytes + 1),
        );
    let connecting = timeout(
        CONNECT_TIMEOUT,
        connect_async_tls_with_config(request, Some(socket_config), false, Some(connector)),
    );
    let connected = tokio::select! {
        result = connecting => result,
        changed = shutdown.changed() => {
            if changed.is_err() || *shutdown.borrow() {
                return Ok(None);
            }
            return Err(ConnectionFailure::Transient);
        }
    };
    let (socket, response) = match connected {
        Ok(Ok(connected)) => connected,
        Ok(Err(error)) => return Err(classify_connect_error(&error)),
        Err(_) => return Err(ConnectionFailure::Transient),
    };
    let selected = response
        .headers()
        .get(header::SEC_WEBSOCKET_PROTOCOL)
        .and_then(|value| value.to_str().ok());
    if selected != Some(EIP_SUBPROTOCOL) {
        return Err(ConnectionFailure::Fatal(
            "reverse WebSocket peer did not select eip.v1",
        ));
    }
    if response
        .headers()
        .contains_key(header::SEC_WEBSOCKET_EXTENSIONS)
    {
        return Err(ConnectionFailure::Fatal(
            "reverse WebSocket peer selected an unsupported extension",
        ));
    }
    Ok(Some(socket))
}

async fn receive_initialize_message(
    socket: &mut Socket,
    max_request_bytes: usize,
) -> Result<String, ConnectionFailure> {
    loop {
        match socket.next().await {
            Some(Ok(Message::Text(payload))) if payload.len() <= max_request_bytes => {
                return Ok(payload.to_string());
            }
            Some(Ok(Message::Ping(payload))) => socket
                .send(Message::Pong(payload))
                .await
                .map_err(|_| ConnectionFailure::Transient)?,
            Some(Ok(Message::Pong(_))) => {}
            Some(Ok(Message::Close(_))) | None => return Err(ConnectionFailure::Transient),
            Some(Ok(Message::Frame(_))) => {
                return Err(ConnectionFailure::Fatal("unexpected raw WebSocket frame"));
            }
            Some(Ok(_)) => {
                return Err(ConnectionFailure::Fatal(
                    "initialize must be the first EIP WebSocket message",
                ));
            }
            Some(Err(error)) => return Err(classify_session_error(&error)),
        }
    }
}

async fn serve_connection(
    mut socket: Socket,
    daemon: Arc<Daemon>,
    config: &Config,
    mut shutdown: watch::Receiver<bool>,
) -> Result<(), ConnectionFailure> {
    let max_request_bytes = usize::try_from(config.limits.max_request_bytes)
        .map_err(|_| ConnectionFailure::Fatal("request limit does not fit this platform"))?;
    let max_response_bytes = usize::try_from(config.limits.max_response_bytes)
        .map_err(|_| ConnectionFailure::Fatal("response limit does not fit this platform"))?;
    let max_data_bytes = usize::try_from(config.limits.max_transfer_frame_bytes)
        .map_err(|_| ConnectionFailure::Fatal("data limit does not fit this platform"))?;
    let max_concurrency = usize::try_from(config.limits.max_concurrent_operations)
        .map_err(|_| ConnectionFailure::Fatal("operation limit does not fit this platform"))?;
    let max_transfers = usize::try_from(config.limits.max_concurrent_file_transfers)
        .map_err(|_| ConnectionFailure::Fatal("transfer limit does not fit this platform"))?;
    let transfer_timeout = Duration::from_millis(config.limits.max_file_transfer_duration_ms);

    let (control_tx, mut control_rx) = mpsc::channel::<ControlResponse>(max_concurrency);
    let data_capacity = max_transfers.saturating_mul(2).max(2);
    let (data_tx, mut data_rx) = mpsc::channel::<DataFrame>(data_capacity);
    let (inbound_data_tx, mut inbound_data_rx) = mpsc::channel::<DataFrame>(data_capacity);
    daemon
        .begin_session(data_tx.clone())
        .map_err(|_| ConnectionFailure::Fatal("cannot begin a fresh EIP session"))?;

    let inbound_daemon = Arc::clone(&daemon);
    let inbound_responses = data_tx.clone();
    let mut inbound_data_task = tokio::spawn(async move {
        while let Some(frame) = inbound_data_rx.recv().await {
            let handled = timeout(
                transfer_timeout,
                inbound_daemon.handle_data_frame(frame.clone()),
            )
            .await;
            match handled {
                Ok(Ok(())) => {}
                Ok(Err(error)) => {
                    let reset = DataFrame {
                        kind: DataFrameKind::Reset,
                        handle: frame.handle,
                        offset: frame.offset,
                        payload: Vec::new(),
                        reset_status: Some(reset_status(error)),
                    };
                    if !matches!(
                        timeout(SESSION_DRAIN_TIMEOUT, inbound_responses.send(reset)).await,
                        Ok(Ok(()))
                    ) {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });

    let maintenance_daemon = Arc::clone(&daemon);
    let mut maintenance_task = tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_millis(100));
        interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
        loop {
            interval.tick().await;
            maintenance_daemon.maintenance().await;
        }
    });

    let first = tokio::select! {
        message = timeout(
            config.initialization_timeout,
            receive_initialize_message(&mut socket, max_request_bytes),
        ) => message.map_err(|_| ConnectionFailure::Transient).and_then(|message| message),
        changed = shutdown.changed() => {
            if changed.is_err() || *shutdown.borrow() {
                cleanup_connection(
                    &mut socket,
                    &daemon,
                    inbound_data_tx,
                    &mut inbound_data_task,
                    &mut maintenance_task,
                    JoinSet::new(),
                )
                .await;
                return Ok(());
            }
            return Err(ConnectionFailure::Transient);
        }
    };
    let first = match first {
        Ok(payload) => payload,
        Err(failure) => {
            cleanup_connection(
                &mut socket,
                &daemon,
                inbound_data_tx,
                &mut inbound_data_task,
                &mut maintenance_task,
                JoinSet::new(),
            )
            .await;
            return Err(failure);
        }
    };
    let response = daemon.handle_payload_for_carrier(first.as_str()).await;
    let (payload, handoff, _) = response.into_parts();
    if let Err(failure) = send_control(&mut socket, payload, handoff, max_response_bytes).await {
        cleanup_connection(
            &mut socket,
            &daemon,
            inbound_data_tx,
            &mut inbound_data_task,
            &mut maintenance_task,
            JoinSet::new(),
        )
        .await;
        return Err(failure);
    }
    if !daemon.session_initialized() {
        cleanup_connection(
            &mut socket,
            &daemon,
            inbound_data_tx,
            &mut inbound_data_task,
            &mut maintenance_task,
            JoinSet::new(),
        )
        .await;
        return Err(ConnectionFailure::Transient);
    }

    let admission = Arc::new(Semaphore::new(max_concurrency));
    let mut requests = JoinSet::new();
    let mut ping_interval = tokio::time::interval(PING_INTERVAL);
    ping_interval.set_missed_tick_behavior(MissedTickBehavior::Delay);
    ping_interval.tick().await;
    let mut expected_pong: Option<(Vec<u8>, tokio::time::Instant)> = None;
    let mut control_burst = 0_usize;
    let outcome = loop {
        if control_burst >= MAX_CONTROL_BURST {
            match data_rx.try_recv() {
                Ok(frame) => {
                    if send_data(&mut socket, frame, max_data_bytes).await.is_err() {
                        break Err(ConnectionFailure::Transient);
                    }
                    control_burst = 0;
                    continue;
                }
                Err(mpsc::error::TryRecvError::Disconnected) => {
                    break Err(ConnectionFailure::Transient);
                }
                Err(mpsc::error::TryRecvError::Empty) => {}
            }
        }
        let pong_deadline = async {
            match &expected_pong {
                Some((_, deadline)) => tokio::time::sleep_until(*deadline).await,
                None => pending::<()>().await,
            }
        };
        tokio::select! {
            changed = shutdown.changed() => {
                if changed.is_err() || *shutdown.borrow() {
                    break Ok(());
                }
            }
            _ = pong_deadline => break Err(ConnectionFailure::Transient),
            _ = ping_interval.tick(), if expected_pong.is_none() => {
                let nonce = random_bytes().to_vec();
                if socket.send(Message::Ping(nonce.clone().into())).await.is_err() {
                    break Err(ConnectionFailure::Transient);
                }
                expected_pong = Some((nonce, tokio::time::Instant::now() + PONG_TIMEOUT));
            }
            response = control_rx.recv() => {
                let Some(response) = response else {
                    break Err(ConnectionFailure::Transient);
                };
                let closes_session = response.closes_session;
                if send_control(
                    &mut socket,
                    response.payload,
                    response.handoff,
                    max_response_bytes,
                )
                .await
                .is_err()
                {
                    break Err(ConnectionFailure::Transient);
                }
                control_burst = control_burst.saturating_add(1);
                if closes_session {
                    break Ok(());
                }
            }
            frame = data_rx.recv() => {
                let Some(frame) = frame else {
                    break Err(ConnectionFailure::Transient);
                };
                if send_data(&mut socket, frame, max_data_bytes).await.is_err() {
                    break Err(ConnectionFailure::Transient);
                }
                control_burst = 0;
            }
            completed = requests.join_next(), if !requests.is_empty() => {
                if completed.is_none() {
                    break Err(ConnectionFailure::Transient);
                }
            }
            message = socket.next() => {
                match message {
                    Some(Ok(Message::Text(payload))) if payload.len() <= max_request_bytes => {
                        let permit = match admission.clone().try_acquire_owned() {
                            Ok(permit) => permit,
                            Err(_) => {
                                break Err(ConnectionFailure::Fatal(
                                    "reverse WebSocket request concurrency exceeded",
                                ));
                            }
                        };
                        let payload = payload.to_string();
                        let pending_operation = daemon.track_pending_payload(&payload);
                        let request_daemon = Arc::clone(&daemon);
                        let responses = control_tx.clone();
                        requests.spawn(async move {
                            let _pending_operation = pending_operation;
                            let response = request_daemon.handle_payload_for_carrier(&payload).await;
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
                    Some(Ok(Message::Binary(payload))) if payload.len() <= max_data_bytes => {
                        let frame = match decode_data_frame(&payload, max_data_bytes) {
                            Ok(frame) => frame,
                            Err(_) => break Err(ConnectionFailure::Fatal("invalid EIP WebSocket data frame")),
                        };
                        if !matches!(
                            timeout(transfer_timeout, inbound_data_tx.send(frame)).await,
                            Ok(Ok(()))
                        ) {
                            break Err(ConnectionFailure::Transient);
                        }
                    }
                    Some(Ok(Message::Ping(payload))) => {
                        if socket.send(Message::Pong(payload)).await.is_err() {
                            break Err(ConnectionFailure::Transient);
                        }
                    }
                    Some(Ok(Message::Pong(payload))) => {
                        if expected_pong.as_ref().is_some_and(|(expected, _)| expected.as_slice() == payload.as_ref()) {
                            expected_pong = None;
                        }
                    }
                    Some(Ok(Message::Close(_))) | None => break Err(ConnectionFailure::Transient),
                    Some(Ok(Message::Frame(_))) => break Err(ConnectionFailure::Fatal("unexpected raw WebSocket frame")),
                    Some(Ok(_)) => break Err(ConnectionFailure::Fatal("WebSocket message exceeds its EIP limit")),
                    Some(Err(error)) => break Err(classify_session_error(&error)),
                }
            }
        }
    };

    cleanup_connection(
        &mut socket,
        &daemon,
        inbound_data_tx,
        &mut inbound_data_task,
        &mut maintenance_task,
        requests,
    )
    .await;
    outcome
}

async fn cleanup_connection(
    socket: &mut Socket,
    daemon: &Daemon,
    inbound_data_tx: mpsc::Sender<DataFrame>,
    inbound_data_task: &mut tokio::task::JoinHandle<()>,
    maintenance_task: &mut tokio::task::JoinHandle<()>,
    mut requests: JoinSet<()>,
) {
    maintenance_task.abort();
    let _ = maintenance_task.await;
    drop(inbound_data_tx);
    if timeout(SESSION_DRAIN_TIMEOUT, &mut *inbound_data_task)
        .await
        .is_err()
    {
        inbound_data_task.abort();
        let _ = inbound_data_task.await;
    }
    let _ = daemon.transport_closed(SESSION_DRAIN_TIMEOUT).await;
    if timeout(SESSION_DRAIN_TIMEOUT, async {
        while requests.join_next().await.is_some() {}
    })
    .await
    .is_err()
    {
        requests.abort_all();
        while requests.join_next().await.is_some() {}
    }
    let _ = timeout(SESSION_DRAIN_TIMEOUT, socket.close(None)).await;
}

async fn send_control(
    socket: &mut Socket,
    payload: Vec<u8>,
    handoff: Option<ActiveResponseHandoff>,
    max_response_bytes: usize,
) -> Result<(), ConnectionFailure> {
    if payload.len() > max_response_bytes {
        return Err(ConnectionFailure::Fatal(
            "EIP response exceeds its WebSocket limit",
        ));
    }
    let payload = String::from_utf8(payload)
        .map_err(|_| ConnectionFailure::Fatal("EIP response is not UTF-8 JSON"))?;
    socket
        .send(Message::Text(payload.into()))
        .await
        .map_err(|_| ConnectionFailure::Transient)?;
    if let Some(handoff) = handoff {
        handoff.complete();
    }
    Ok(())
}

async fn send_data(
    socket: &mut Socket,
    frame: DataFrame,
    max_data_bytes: usize,
) -> Result<(), ConnectionFailure> {
    let payload = encode_data_frame(&frame, max_data_bytes)
        .map_err(|_| ConnectionFailure::Fatal("invalid outbound EIP data frame"))?;
    socket
        .send(Message::Binary(payload.into()))
        .await
        .map_err(|_| ConnectionFailure::Transient)
}

fn build_tls_connector(config: &ReverseWebSocketConfig) -> io::Result<Connector> {
    if config.endpoint.starts_with("ws://") {
        return Ok(Connector::Plain);
    }
    let native = rustls_native_certs::load_native_certs();
    let mut roots = rustls::RootCertStore::empty();
    let (accepted, _) = roots.add_parsable_certificates(native.certs);
    if let Some(path) = &config.tls_ca_file {
        let file = std::fs::File::open(path).map_err(|_| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "cannot read configured TLS CA file",
            )
        })?;
        let certificates = rustls_pemfile::certs(&mut BufReader::new(file))
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "configured TLS CA file is not valid PEM",
                )
            })?;
        if certificates.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "configured TLS CA file contains no certificates",
            ));
        }
        roots.add_parsable_certificates(certificates);
    } else if accepted == 0 && !native.errors.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "no platform TLS trust roots are available",
        ));
    }
    let tls = rustls::ClientConfig::builder()
        .with_root_certificates(roots)
        .with_no_client_auth();
    Ok(Connector::Rustls(Arc::new(tls)))
}

async fn read_credential(path: &std::path::Path) -> io::Result<String> {
    let metadata = tokio::fs::symlink_metadata(path).await.map_err(|_| {
        io::Error::new(
            io::ErrorKind::NotFound,
            "attachment credential file is unavailable",
        )
    })?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "attachment credential source is not a regular file",
        ));
    }
    if metadata.len() > MAX_CREDENTIAL_BYTES as u64 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "attachment credential exceeds its byte limit",
        ));
    }
    let bytes = tokio::fs::read(path).await.map_err(|_| {
        io::Error::new(
            io::ErrorKind::PermissionDenied,
            "attachment credential cannot be read",
        )
    })?;
    let credential = std::str::from_utf8(&bytes)
        .map_err(|_| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "attachment credential is not UTF-8",
            )
        })?
        .strip_suffix('\n')
        .unwrap_or_else(|| std::str::from_utf8(&bytes).expect("validated UTF-8"));
    let credential = credential.strip_suffix('\r').unwrap_or(credential);
    if credential.is_empty()
        || credential.len() > MAX_CREDENTIAL_BYTES
        || credential
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "attachment credential is empty or malformed",
        ));
    }
    Ok(credential.to_owned())
}

fn classify_connect_error(error: &WebSocketError) -> ConnectionFailure {
    match error {
        WebSocketError::Http(response) => match response.status() {
            StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => {
                ConnectionFailure::Fatal("reverse WebSocket attachment authorization failed")
            }
            StatusCode::REQUEST_TIMEOUT
            | StatusCode::TOO_EARLY
            | StatusCode::TOO_MANY_REQUESTS
            | StatusCode::INTERNAL_SERVER_ERROR
            | StatusCode::BAD_GATEWAY
            | StatusCode::SERVICE_UNAVAILABLE
            | StatusCode::GATEWAY_TIMEOUT => ConnectionFailure::Transient,
            status if status.is_redirection() => {
                ConnectionFailure::Fatal("reverse WebSocket redirect is forbidden")
            }
            _ => ConnectionFailure::Fatal("reverse WebSocket upgrade was rejected"),
        },
        WebSocketError::Io(_) => ConnectionFailure::Transient,
        WebSocketError::Tls(_) => {
            ConnectionFailure::Fatal("reverse WebSocket TLS validation failed")
        }
        WebSocketError::Url(_) | WebSocketError::HttpFormat(_) => {
            ConnectionFailure::Fatal("invalid reverse WebSocket endpoint")
        }
        _ => ConnectionFailure::Fatal("reverse WebSocket upgrade is incompatible"),
    }
}

fn classify_session_error(error: &WebSocketError) -> ConnectionFailure {
    match error {
        WebSocketError::ConnectionClosed
        | WebSocketError::Io(_)
        | WebSocketError::Protocol(
            tokio_tungstenite::tungstenite::error::ProtocolError::ResetWithoutClosingHandshake,
        ) => ConnectionFailure::Transient,
        WebSocketError::Tls(_) => ConnectionFailure::Fatal("reverse WebSocket TLS session failed"),
        WebSocketError::Capacity(_)
        | WebSocketError::Protocol(_)
        | WebSocketError::Utf8(_)
        | WebSocketError::AttackAttempt => {
            ConnectionFailure::Fatal("reverse WebSocket peer violated the carrier profile")
        }
        _ => ConnectionFailure::Transient,
    }
}

fn full_jitter(cap: Duration) -> Duration {
    let random = u64::from_le_bytes(random_bytes());
    let cap_millis = u64::try_from(cap.as_millis()).unwrap_or(u64::MAX);
    Duration::from_millis(random % cap_millis.saturating_add(1))
}

fn random_bytes() -> [u8; 8] {
    let mut bytes = [0_u8; 8];
    if getrandom::fill(&mut bytes).is_err() {
        bytes.copy_from_slice(&Instant::now().elapsed().as_nanos().to_le_bytes()[..8]);
    }
    bytes
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
    use std::time::Duration;

    use super::{RECONNECT_CAP, classify_connect_error, classify_session_error, full_jitter};
    use tokio_tungstenite::tungstenite::{Error, error::ProtocolError, http::Response};

    #[test]
    fn abrupt_disconnect_reconnects_but_invalid_frames_remain_fatal() {
        assert!(matches!(
            classify_session_error(&Error::Protocol(
                ProtocolError::ResetWithoutClosingHandshake
            )),
            super::ConnectionFailure::Transient
        ));
        assert!(matches!(
            classify_session_error(&Error::Protocol(ProtocolError::UnmaskedFrameFromClient)),
            super::ConnectionFailure::Fatal(_)
        ));
    }

    #[test]
    fn unauthorized_upgrade_is_fatal() {
        let response = Response::builder()
            .status(401)
            .body(Some(Vec::new()))
            .expect("response builds");
        assert!(matches!(
            classify_connect_error(&Error::Http(Box::new(response))),
            super::ConnectionFailure::Fatal(_)
        ));
    }

    #[test]
    fn reconnect_jitter_never_exceeds_its_cap() {
        for _ in 0..100 {
            assert!(full_jitter(RECONNECT_CAP) <= RECONNECT_CAP);
            assert!(full_jitter(Duration::ZERO).is_zero());
        }
    }
}
