use std::{
    collections::VecDeque,
    future::pending,
    io::{self, BufReader},
    sync::Arc,
    time::{Duration, Instant},
};

use futures_util::{Sink, SinkExt, StreamExt, future::BoxFuture, stream::SplitSink};
use tokio::{
    net::TcpStream,
    sync::{mpsc, watch},
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
    daemon::{Carrier, Daemon, ResponseHandoff},
    data_dispatch::DataDispatcher,
    eip::{DataFrame, DataFrameKind, decode_data_frame, encode_data_frame},
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
// A successful socket flush is not peer consumption. Fence each bounded data batch
// with Ping/Pong so bulk bytes cannot indefinitely bury liveness/control frames in TCP.
const DATA_BATCH_BYTES: usize = 64 * 1024;

type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

struct ControlResponse {
    payload: Vec<u8>,
    handoff: Option<ResponseHandoff>,
}

enum Outbound {
    Control(ControlResponse),
    Data(DataFrame),
    Ping(Vec<u8>),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Sent {
    Control,
    Data(usize),
    Ping,
}

type SocketSink<S> = SplitSink<WebSocketStream<S>, Message>;
type WriteCompletion<S> = (SocketSink<S>, Result<Sent, ConnectionFailure>);

/// One application send owner. The future survives every read/timer/dispatch turn;
/// split releases its socket lock on Pending, allowing the physical reader to run.
struct ConnectionWriter<S> {
    sink: Option<SocketSink<S>>,
    sending: Option<BoxFuture<'static, WriteCompletion<S>>>,
    stop: watch::Sender<bool>,
    max_response_bytes: usize,
    max_data_bytes: usize,
}

impl<S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send + 'static> ConnectionWriter<S> {
    fn new(sink: SocketSink<S>, max_response_bytes: usize, max_data_bytes: usize) -> Self {
        let (stop, _) = watch::channel(false);
        Self {
            sink: Some(sink),
            sending: None,
            stop,
            max_response_bytes,
            max_data_bytes,
        }
    }

    fn idle(&self) -> bool {
        self.sending.is_none()
    }

    fn start(&mut self, message: Outbound) {
        let mut sink = self.sink.take().expect("one pending application write");
        let mut stop = self.stop.subscribe();
        let (max_response_bytes, max_data_bytes) = (self.max_response_bytes, self.max_data_bytes);
        self.sending = Some(Box::pin(async move {
            let result = match message {
                Outbound::Control(response) => send_control(
                    &mut sink,
                    response.payload,
                    response.handoff,
                    max_response_bytes,
                    &mut stop,
                )
                .await
                .map(|()| Sent::Control),
                Outbound::Data(frame) => {
                    let bytes = crate::eip::EIP_DATA_FRAME_HEADER_BYTES
                        + frame.session_id.len()
                        + frame.handle.len()
                        + frame.payload.len();
                    send_data(&mut sink, frame, max_data_bytes, &mut stop)
                        .await
                        .map(|()| Sent::Data(bytes))
                }
                Outbound::Ping(nonce) => send_message(
                    &mut sink,
                    Message::Ping(nonce.into()),
                    &mut stop,
                    PONG_TIMEOUT,
                )
                .await
                .map(|()| Sent::Ping),
            };
            (sink, result)
        }));
    }

    async fn finish(&mut self) -> Result<Sent, ConnectionFailure> {
        let (sink, result) = match &mut self.sending {
            Some(sending) => sending.await,
            None => pending().await,
        };
        self.sending = None;
        self.sink = Some(sink);
        result
    }
}

/// A Pong may be read before the corresponding send reports its flush complete.
#[derive(Default)]
struct Heartbeat {
    nonce: Option<Vec<u8>>,
    queued: bool,
    pong_seen: bool,
    deadline: Option<tokio::time::Instant>,
}

impl Heartbeat {
    fn idle(&self) -> bool {
        self.nonce.is_none()
    }

    fn start(&mut self) {
        self.nonce = Some(random_bytes().to_vec());
        self.queued = true;
        self.pong_seen = false;
    }

    fn take_ping(&mut self) -> Option<Vec<u8>> {
        if !self.queued {
            return None;
        }
        self.queued = false;
        self.nonce.clone()
    }

    fn sent(&mut self) {
        if self.pong_seen {
            *self = Self::default();
        } else {
            self.deadline = Some(tokio::time::Instant::now() + PONG_TIMEOUT);
        }
    }

    fn receive(&mut self, payload: &[u8]) -> bool {
        if self.nonce.as_deref() == Some(payload) {
            if self.deadline.is_some() {
                *self = Self::default();
                return true;
            }
            self.pong_seen = true;
        }
        false
    }
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
    let supervised = config.supervised;
    let signal_task = tokio::spawn(async move {
        let _ = crate::shutdown::wait(supervised).await;
        shutdown_sender.send_replace(true);
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

    let mut backoff = RECONNECT_BASE;
    let result = loop {
        if *shutdown.borrow() {
            break Ok(());
        }
        let credential = match read_credential(&websocket.credential_file).await {
            Ok(credential) => credential,
            Err(error) => break Err(error),
        };
        crate::connect::launcher::emit("connecting", serde_json::json!({}));
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

        crate::connect::launcher::emit("reconnecting", serde_json::json!({}));
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
    maintenance_task.abort();
    let _ = maintenance_task.await;
    if !daemon.drain(SESSION_DRAIN_TIMEOUT).await {
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

async fn receive_initialize_message<S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin>(
    socket: &mut WebSocketStream<S>,
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

async fn serve_connection<
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send + 'static,
>(
    mut socket: WebSocketStream<S>,
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
    let max_transfers = usize::try_from(config.limits.max_device_file_transfers)
        .map_err(|_| ConnectionFailure::Fatal("transfer limit does not fit this platform"))?;

    let (control_tx, mut control_rx) = mpsc::channel::<ControlResponse>(max_concurrency);
    let data_capacity = max_transfers.saturating_mul(crate::eip::EIP_TRANSFER_WINDOW_CHUNKS + 2);

    let (data_tx, mut data_rx) = mpsc::channel::<DataFrame>(data_capacity);
    let (inbound_data_tx, mut inbound_data_rx) = mpsc::channel::<DataFrame>(data_capacity);
    let _connection = daemon.connection_guard();
    let carrier = daemon.carrier(data_tx.clone());

    let mut inbound_dispatch =
        DataDispatcher::new(daemon.clone(), carrier.clone(), data_tx.clone(), config);
    let inbound_responses = data_tx.clone();
    let mut inbound_data_task = tokio::spawn(async move {
        while let Some(frame) = inbound_data_rx.recv().await {
            match inbound_dispatch.enqueue(frame.clone()) {
                Ok(()) => {}
                Err(error) => {
                    let reset = DataFrame {
                        session_id: frame.session_id,
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
            }
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
                    &carrier,
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
                &carrier,
                JoinSet::new(),
            )
            .await;
            return Err(failure);
        }
    };
    let response = daemon
        .handle_payload_for_carrier(&carrier, first.as_str())
        .await;
    let (payload, handoff) = response.into_parts();
    if let Err(failure) = send_control(
        &mut socket,
        payload,
        handoff,
        max_response_bytes,
        &mut shutdown,
    )
    .await
    {
        cleanup_connection(
            &mut socket,
            &daemon,
            inbound_data_tx,
            &mut inbound_data_task,
            &carrier,
            JoinSet::new(),
        )
        .await;
        return Err(failure);
    }
    if !carrier.initialized() {
        cleanup_connection(
            &mut socket,
            &daemon,
            inbound_data_tx,
            &mut inbound_data_task,
            &carrier,
            JoinSet::new(),
        )
        .await;
        return Err(ConnectionFailure::Transient);
    }

    crate::connect::launcher::emit("online", serde_json::json!({}));
    let mut requests = JoinSet::new();
    let mut ping_interval = tokio::time::interval(PING_INTERVAL);
    ping_interval.set_missed_tick_behavior(MissedTickBehavior::Delay);
    ping_interval.tick().await;
    let (sink, mut reader) = socket.split();
    let mut writer = ConnectionWriter::new(sink, max_response_bytes, max_data_bytes);
    let mut heartbeat = Heartbeat::default();
    let mut immediate = VecDeque::new();
    let mut control_burst = 0_usize;
    let mut data_bytes = 0_usize;
    let outcome = loop {
        if heartbeat.idle() && data_bytes >= DATA_BATCH_BYTES {
            heartbeat.start();
        }
        let data_ready = heartbeat.idle();
        if writer.idle() {
            let next = if let Some(nonce) = heartbeat.take_ping() {
                Some(Outbound::Ping(nonce))
            } else if let Some(message) = immediate.pop_front() {
                Some(message)
            } else if control_burst >= MAX_CONTROL_BURST && data_ready {
                data_rx
                    .try_recv()
                    .ok()
                    .map(Outbound::Data)
                    .or_else(|| control_rx.try_recv().ok().map(Outbound::Control))
            } else {
                control_rx
                    .try_recv()
                    .ok()
                    .map(Outbound::Control)
                    .or_else(|| {
                        data_ready
                            .then(|| data_rx.try_recv().ok().map(Outbound::Data))
                            .flatten()
                    })
            };
            if let Some(message) = next {
                writer.start(message);
            }
        }
        let writing = !writer.idle();
        let pong_deadline = async {
            match heartbeat.deadline {
                Some(deadline) => tokio::time::sleep_until(deadline).await,
                None => pending::<()>().await,
            }
        };
        tokio::select! {
            changed = shutdown.changed() => {
                if changed.is_err() || *shutdown.borrow() {
                    break Ok(());
                }
            }
            sent = writer.finish(), if writing => {
                match sent {
                    Ok(Sent::Ping) => {
                        heartbeat.sent();
                        if heartbeat.idle() { data_bytes = 0; }
                    },
                    Ok(Sent::Control) => control_burst = control_burst.saturating_add(1),
                    Ok(Sent::Data(bytes)) => {
                        control_burst = 0;
                        data_bytes = data_bytes.saturating_add(bytes);
                    },
                    Err(error) => break Err(error),
                }
            }
            _ = pong_deadline => break Err(ConnectionFailure::Transient),
            _ = ping_interval.tick(), if heartbeat.idle() => heartbeat.start(),
            response = control_rx.recv(), if !writing => {
                let Some(response) = response else {
                    break Err(ConnectionFailure::Transient);
                };
                writer.start(Outbound::Control(response));
            }
            frame = data_rx.recv(), if !writing && data_ready => {
                let Some(frame) = frame else {
                    break Err(ConnectionFailure::Transient);
                };
                writer.start(Outbound::Data(frame));
            }
            completed = requests.join_next(), if !requests.is_empty() => {
                if completed.is_none() {
                    break Err(ConnectionFailure::Transient);
                }
            }
            message = reader.next() => {
                match message {
                    Some(Ok(Message::Text(payload))) if payload.len() <= max_request_bytes => {
                        let Some(permit) = daemon.admit_payload(payload.as_str()) else {
                            if immediate.len() >= MAX_CONTROL_BURST {
                                break Err(ConnectionFailure::Transient);
                            }
                            immediate.push_back(Outbound::Control(ControlResponse {
                                payload: daemon.busy_response(payload.as_str()), handoff: None,
                            }));
                            continue;
                        };
                        let payload = payload.to_string();
                        let pending_operation = daemon.track_pending_payload(&payload);
                        let request_daemon = Arc::clone(&daemon);
                        let request_carrier = carrier.clone();
                        let responses = control_tx.clone();
                        requests.spawn(async move {
                            let _pending_operation = pending_operation;
                            let response = request_daemon.handle_payload_for_carrier(&request_carrier, &payload).await;
                            let (payload, handoff) = response.into_parts();
                            let _ = responses
                                .send(ControlResponse {
                                    payload,
                                    handoff,
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
                        if let Err(error) = inbound_data_tx.try_send(frame) {
                            let frame = error.into_inner();
                            daemon.fail_data_session(&carrier, &frame.session_id);
                            let reset = DataFrame { kind: DataFrameKind::Reset, session_id: frame.session_id,
                                handle: frame.handle, offset: frame.offset, payload: Vec::new(),
                                reset_status: Some(reset_status(crate::transfer::TransferError::Busy)) };
                            if immediate.len() >= MAX_CONTROL_BURST {
                                break Err(ConnectionFailure::Transient);
                            }
                            immediate.push_back(Outbound::Data(reset));
                        }
                    }
                    // Tungstenite queues and flushes its bounded automatic Pong even when a
                    // data write is pending. Keep polling the reader; do not queue a second Pong.
                    Some(Ok(Message::Ping(_))) => {}
                    Some(Ok(Message::Pong(payload))) => {
                        if heartbeat.receive(&payload) {
                            data_bytes = 0;
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

    // Cancellation abandons this attachment, never reconstructs/retries its partial write.
    // Recover the same sink only to run the existing bounded close/Session cleanup.
    writer.stop.send_replace(true);
    if !writer.idle() {
        let _ = writer.finish().await;
    }
    let mut socket = writer
        .sink
        .take()
        .expect("settled writer")
        .reunite(reader)
        .expect("halves of the same connection");
    cleanup_connection(
        &mut socket,
        &daemon,
        inbound_data_tx,
        &mut inbound_data_task,
        &carrier,
        requests,
    )
    .await;
    outcome
}

async fn cleanup_connection<S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin>(
    socket: &mut WebSocketStream<S>,
    daemon: &Daemon,
    inbound_data_tx: mpsc::Sender<DataFrame>,
    inbound_data_task: &mut tokio::task::JoinHandle<()>,
    carrier: &Carrier,
    mut requests: JoinSet<()>,
) {
    carrier.close();
    drop(inbound_data_tx);
    if timeout(SESSION_DRAIN_TIMEOUT, &mut *inbound_data_task)
        .await
        .is_err()
    {
        inbound_data_task.abort();
        let _ = inbound_data_task.await;
    }
    daemon.detach(carrier).await;
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

async fn send_control<S: Sink<Message, Error = WebSocketError> + Unpin>(
    socket: &mut S,
    payload: Vec<u8>,
    handoff: Option<ResponseHandoff>,
    max_response_bytes: usize,
    shutdown: &mut watch::Receiver<bool>,
) -> Result<(), ConnectionFailure> {
    if payload.len() > max_response_bytes {
        return Err(ConnectionFailure::Fatal(
            "EIP response exceeds its WebSocket limit",
        ));
    }
    let payload = String::from_utf8(payload)
        .map_err(|_| ConnectionFailure::Fatal("EIP response is not UTF-8 JSON"))?;
    send_message(
        socket,
        Message::Text(payload.into()),
        shutdown,
        PONG_TIMEOUT,
    )
    .await?;
    if let Some(handoff) = handoff {
        handoff.complete();
    }
    Ok(())
}

async fn send_data<S: Sink<Message, Error = WebSocketError> + Unpin>(
    socket: &mut S,
    frame: DataFrame,
    max_data_bytes: usize,
    shutdown: &mut watch::Receiver<bool>,
) -> Result<(), ConnectionFailure> {
    let payload = encode_data_frame(&frame, max_data_bytes)
        .map_err(|_| ConnectionFailure::Fatal("invalid outbound EIP data frame"))?;
    send_message(
        socket,
        Message::Binary(payload.into()),
        shutdown,
        PONG_TIMEOUT,
    )
    .await
}

// Cancellation or timeout abandons this attachment; a partially sent message is never retried.
async fn send_message<S: Sink<Message, Error = WebSocketError> + Unpin>(
    socket: &mut S,
    message: Message,
    shutdown: &mut watch::Receiver<bool>,
    budget: Duration,
) -> Result<(), ConnectionFailure> {
    if *shutdown.borrow() {
        return Err(ConnectionFailure::Transient);
    }
    tokio::select! {
        _ = shutdown.changed() => Err(ConnectionFailure::Transient),
        result = timeout(budget, socket.send(message)) => {
            result.map_err(|_| ConnectionFailure::Transient)?
                .map_err(|_| ConnectionFailure::Transient)
        }
    }
}

fn build_tls_connector(config: &ReverseWebSocketConfig) -> io::Result<Connector> {
    if config.endpoint.starts_with("ws://") {
        return Ok(Connector::Plain);
    }
    Ok(Connector::Rustls(tls_client_config(
        config.tls_ca_file.as_deref(),
    )?))
}

pub(crate) fn tls_client_config(
    ca_file: Option<&std::path::Path>,
) -> io::Result<Arc<rustls::ClientConfig>> {
    let native = rustls_native_certs::load_native_certs();
    let mut roots = rustls::RootCertStore::empty();
    let (accepted, _) = roots.add_parsable_certificates(native.certs);
    if let Some(path) = ca_file {
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
    let tls = rustls::ClientConfig::builder_with_provider(Arc::new(
        rustls::crypto::ring::default_provider(),
    ))
    .with_safe_default_protocol_versions()
    .map_err(io::Error::other)?
    .with_root_certificates(roots)
    .with_no_client_auth();
    Ok(Arc::new(tls))
}

pub(crate) async fn read_credential(path: &std::path::Path) -> io::Result<String> {
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
        // tokio-tungstenite carries rustls handshake failures inside I/O errors.
        WebSocketError::Io(error)
            if error
                .get_ref()
                .is_some_and(|source| source.is::<rustls::Error>()) =>
        {
            ConnectionFailure::Fatal("reverse WebSocket TLS validation failed")
        }
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

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{RECONNECT_CAP, classify_connect_error, classify_session_error, full_jitter};
    use tokio_tungstenite::tungstenite::{Error, error::ProtocolError, http::Response};

    #[tokio::test]
    async fn stalled_socket_sends_observe_shutdown_and_deadline() {
        use tokio::io::{AsyncReadExt, duplex};
        use tokio_tungstenite::{
            WebSocketStream,
            tungstenite::{Message, protocol::Role},
        };

        for signal in [true, false] {
            let (writer, mut reader) = duplex(1);
            let mut socket = WebSocketStream::from_raw_socket(writer, Role::Server, None).await;
            let (shutdown_tx, mut shutdown) = tokio::sync::watch::channel(false);
            let sending = tokio::spawn(async move {
                super::send_message(
                    &mut socket,
                    Message::Binary(vec![0; 1024].into()),
                    &mut shutdown,
                    if signal {
                        Duration::from_secs(60)
                    } else {
                        Duration::from_millis(100)
                    },
                )
                .await
            });
            // One observed byte proves the sender reached the full, deliberately unread buffer.
            reader.read_u8().await.unwrap();
            assert!(!sending.is_finished());
            if signal {
                shutdown_tx.send(true).unwrap();
            }
            let result = tokio::time::timeout(Duration::from_secs(1), sending)
                .await
                .unwrap()
                .unwrap();
            assert_eq!(result, Err(super::ConnectionFailure::Transient));
        }
    }

    #[tokio::test]
    async fn cancelling_a_partial_frame_abandons_it_without_replay() {
        use super::*;
        use tokio::io::AsyncReadExt;
        use tokio_tungstenite::tungstenite::protocol::Role;
        let (io, mut peer) = tokio::io::duplex(4);
        let socket = WebSocketStream::from_raw_socket(io, Role::Server, None).await;
        let (sink, reader) = socket.split();
        let mut writer = ConnectionWriter::new(sink, 8192, 8192);
        writer.start(Outbound::Control(ControlResponse {
            payload: vec![b'a'; 4096],
            handoff: None,
        }));
        assert!(futures_util::poll!(std::pin::pin!(writer.finish())).is_pending());
        let first = peer.read_u8().await.unwrap();
        writer.stop.send_replace(true);
        assert_eq!(writer.finish().await, Err(ConnectionFailure::Transient));
        drop(writer);
        drop(reader);
        let mut wire = vec![first];
        peer.read_to_end(&mut wire).await.unwrap();
        let mut expected = vec![0x81, 126, 16, 0];
        expected.extend(vec![b'a'; 4096]);
        assert!(wire.len() < expected.len());
        assert_eq!(
            wire,
            expected[..wire.len()],
            "the partial frame was not restarted"
        );
    }

    #[derive(Default)]
    struct WriteGate {
        blocked: std::sync::atomic::AtomicBool,
        reached: std::sync::atomic::AtomicBool,
        waker: futures_util::task::AtomicWaker,
    }

    impl WriteGate {
        fn block(&self) {
            self.reached
                .store(false, std::sync::atomic::Ordering::SeqCst);
            self.blocked
                .store(true, std::sync::atomic::Ordering::SeqCst);
        }

        fn release(&self) {
            self.blocked
                .store(false, std::sync::atomic::Ordering::SeqCst);
            self.waker.wake();
        }

        fn poll(&self, cx: &std::task::Context<'_>) -> bool {
            self.waker.register(cx.waker());
            let blocked = self.blocked.load(std::sync::atomic::Ordering::SeqCst);
            if blocked {
                self.reached
                    .store(true, std::sync::atomic::Ordering::SeqCst);
            }
            blocked
        }
    }

    struct GatedIo {
        stream: tokio::io::DuplexStream,
        gate: std::sync::Arc<WriteGate>,
        flush_only: bool,
    }

    impl tokio::io::AsyncRead for GatedIo {
        fn poll_read(
            mut self: std::pin::Pin<&mut Self>,
            cx: &mut std::task::Context<'_>,
            buf: &mut tokio::io::ReadBuf<'_>,
        ) -> std::task::Poll<std::io::Result<()>> {
            std::pin::Pin::new(&mut self.stream).poll_read(cx, buf)
        }
    }

    impl tokio::io::AsyncWrite for GatedIo {
        fn poll_write(
            mut self: std::pin::Pin<&mut Self>,
            cx: &mut std::task::Context<'_>,
            buf: &[u8],
        ) -> std::task::Poll<std::io::Result<usize>> {
            if !self.flush_only && self.gate.poll(cx) {
                return std::task::Poll::Pending;
            }
            std::pin::Pin::new(&mut self.stream).poll_write(cx, buf)
        }
        fn poll_flush(
            mut self: std::pin::Pin<&mut Self>,
            cx: &mut std::task::Context<'_>,
        ) -> std::task::Poll<std::io::Result<()>> {
            if self.gate.poll(cx) {
                return std::task::Poll::Pending;
            }
            std::pin::Pin::new(&mut self.stream).poll_flush(cx)
        }
        fn poll_shutdown(
            mut self: std::pin::Pin<&mut Self>,
            cx: &mut std::task::Context<'_>,
        ) -> std::task::Poll<std::io::Result<()>> {
            std::pin::Pin::new(&mut self.stream).poll_shutdown(cx)
        }
    }

    async fn spin_until(mut condition: impl FnMut() -> bool) {
        for _ in 0..1000 {
            if condition() {
                return;
            }
            tokio::task::yield_now().await;
        }
        panic!("expected progress without completing the blocked write");
    }

    #[tokio::test]
    async fn pending_response_keeps_dispatching_sessions_and_ping_bursts() {
        use super::*;
        use std::sync::atomic::Ordering;
        use tokio_tungstenite::tungstenite::protocol::Role;
        let config = Config::for_test("env-test");
        let daemon = Arc::new(Daemon::new(&config).unwrap());
        let observed = daemon.clone();
        let cwd = config.default_working_directory.clone();
        let (io, peer) = tokio::io::duplex(128 * 1024);
        let gate = Arc::new(WriteGate::default());
        let socket = WebSocketStream::from_raw_socket(
            GatedIo {
                stream: io,
                gate: gate.clone(),
                flush_only: false,
            },
            Role::Server,
            None,
        )
        .await;
        let mut peer = WebSocketStream::from_raw_socket(peer, Role::Client, None).await;
        let (stop, shutdown) = watch::channel(false);
        let serving =
            tokio::spawn(async move { serve_connection(socket, daemon, &config, shutdown).await });
        peer.send(Message::text(serde_json::json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"client":{"name":"test","version":"1"},"expected_device_id":"env-test"}}).to_string())).await.unwrap();
        let initialized: serde_json::Value =
            serde_json::from_str(peer.next().await.unwrap().unwrap().to_text().unwrap()).unwrap();
        let generation = initialized["result"]["descriptor"]["generation"].clone();
        gate.block();
        peer.send(Message::text(
            r#"{"jsonrpc":"2.0","id":2,"method":"device.describe","params":{}}"#,
        ))
        .await
        .unwrap();
        spin_until(|| gate.reached.load(Ordering::SeqCst)).await;
        for n in 0..9 {
            peer.send(Message::Ping(vec![n].into())).await.unwrap();
        }
        peer.send(Message::text(serde_json::json!({"jsonrpc":"2.0","id":3,"method":"session.open","params":{"expected_device_id":"env-test","expected_generation":generation,"configuration":{"working_directory":cwd},"required_methods":[]}}).to_string())).await.unwrap();
        let mut opened = false;
        for _ in 0..1000 {
            if observed.resource_metrics().await.unwrap().sessions == 1 {
                opened = true;
                break;
            }
            tokio::task::yield_now().await;
        }
        gate.release();
        assert!(
            opened,
            "session.open must dispatch while the earlier response is blocked"
        );
        let mut ids = Vec::new();
        while ids.len() < 2 {
            match tokio::time::timeout(Duration::from_secs(1), peer.next())
                .await
                .unwrap()
                .unwrap()
                .unwrap()
            {
                Message::Text(text) => ids.push(
                    serde_json::from_str::<serde_json::Value>(&text).unwrap()["id"]
                        .as_u64()
                        .unwrap(),
                ),
                Message::Pong(_) => {}
                other => panic!("unexpected {other:?}"),
            }
        }
        assert_eq!(
            ids,
            [2, 3],
            "one ordered response per request, without retries"
        );
        stop.send_replace(true);
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(4), serving)
                .await
                .unwrap()
                .unwrap(),
            Ok(())
        );
    }

    #[tokio::test]
    async fn only_matching_pong_completes_the_data_delivery_fence() {
        let mut heartbeat = super::Heartbeat::default();
        assert!(!heartbeat.receive(b"unsolicited"));
        heartbeat.start();
        let nonce = heartbeat.take_ping().unwrap();
        heartbeat.sent();
        assert!(!heartbeat.receive(b"old-nonce"));
        assert!(!heartbeat.idle());
        assert!(heartbeat.receive(&nonce));
        assert!(heartbeat.idle());
        assert!(
            !heartbeat.receive(&nonce),
            "a duplicate cannot reset a later data batch"
        );
    }

    #[tokio::test(start_paused = true)]
    async fn early_pong_and_handoff_survive_pending_flush() {
        use super::*;
        use tokio_tungstenite::tungstenite::protocol::Role;
        let (io, peer) = tokio::io::duplex(128 * 1024);
        let gate = Arc::new(WriteGate::default());
        let socket = WebSocketStream::from_raw_socket(
            GatedIo {
                stream: io,
                gate: gate.clone(),
                flush_only: true,
            },
            Role::Server,
            None,
        )
        .await;
        let (sink, mut reader) = socket.split();
        let mut peer = WebSocketStream::from_raw_socket(peer, Role::Client, None).await;
        let mut writer = ConnectionWriter::new(sink, 64 * 1024, 64 * 1024);
        let mut heartbeat = Heartbeat::default();
        heartbeat.start();
        let nonce = heartbeat.take_ping().unwrap();
        gate.block();
        writer.start(Outbound::Ping(nonce.clone()));
        assert!(futures_util::poll!(std::pin::pin!(writer.finish())).is_pending());
        assert_eq!(
            peer.next().await.unwrap().unwrap(),
            Message::Ping(nonce.clone().into())
        );
        peer.flush().await.unwrap();
        let pong = reader.next().await.unwrap().unwrap();
        heartbeat.receive(&pong.into_data());
        gate.release();
        assert_eq!(writer.finish().await, Ok(Sent::Ping));
        heartbeat.sent();
        assert!(heartbeat.idle());
        assert!(heartbeat.deadline.is_none());

        // A response envelope owns its history handoff until flush, not merely write acceptance.
        let history = Arc::new(tokio::sync::RwLock::new(()));
        let handoff = ResponseHandoff::Local {
            operation: None,
            _history: history.clone().read_owned().await,
        };
        gate.block();
        writer.start(Outbound::Control(ControlResponse {
            payload: b"{}".to_vec(),
            handoff: Some(handoff),
        }));
        assert!(futures_util::poll!(std::pin::pin!(writer.finish())).is_pending());
        assert_eq!(peer.next().await.unwrap().unwrap(), Message::text("{}"));
        assert!(history.try_write().is_err());
        gate.release();
        assert_eq!(writer.finish().await, Ok(Sent::Control));
        assert!(history.try_write().is_ok());
    }

    #[tokio::test(start_paused = true)]
    async fn pong_before_deadline_remains_valid_when_data_flush_crosses_deadline() {
        use super::*;
        use tokio_tungstenite::tungstenite::protocol::Role;
        let (io, peer) = tokio::io::duplex(128 * 1024);
        let gate = Arc::new(WriteGate::default());
        let socket = WebSocketStream::from_raw_socket(
            GatedIo {
                stream: io,
                gate: gate.clone(),
                flush_only: true,
            },
            Role::Server,
            None,
        )
        .await;
        let (sink, mut reader) = socket.split();
        let mut peer = WebSocketStream::from_raw_socket(peer, Role::Client, None).await;
        let mut writer = ConnectionWriter::new(sink, 64 * 1024, 64 * 1024);
        let mut heartbeat = Heartbeat::default();
        heartbeat.start();
        let nonce = heartbeat.take_ping().unwrap();
        writer.start(Outbound::Ping(nonce.clone()));
        writer.finish().await.unwrap();
        heartbeat.sent();
        assert_eq!(
            peer.next().await.unwrap().unwrap(),
            Message::Ping(nonce.into())
        );
        tokio::time::advance(Duration::from_secs(8)).await;
        gate.block();
        writer.start(Outbound::Data(DataFrame {
            session_id: "s".into(),
            handle: "r".into(),
            kind: DataFrameKind::Chunk,
            offset: 0,
            payload: vec![3; 1024],
            reset_status: None,
        }));
        assert!(futures_util::poll!(std::pin::pin!(writer.finish())).is_pending());
        peer.flush().await.unwrap();
        heartbeat.receive(&reader.next().await.unwrap().unwrap().into_data());
        tokio::time::advance(Duration::from_secs(3)).await;
        assert!(heartbeat.idle());
        gate.release();
        assert!(matches!(writer.finish().await, Ok(Sent::Data(_))));
        assert!(matches!(
            peer.next().await.unwrap().unwrap(),
            Message::Binary(_)
        ));
    }

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
    fn rustls_certificate_errors_wrapped_in_io_are_fatal() {
        for certificate_error in [
            rustls::CertificateError::UnknownIssuer,
            rustls::CertificateError::NotValidForName,
        ] {
            // tokio-rustls wraps TLS handshake errors in io::Error; tungstenite preserves it.
            let error = Error::Io(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                rustls::Error::InvalidCertificate(certificate_error),
            ));
            assert_eq!(
                classify_connect_error(&error),
                super::ConnectionFailure::Fatal("reverse WebSocket TLS validation failed")
            );
        }
    }

    #[test]
    fn ordinary_connect_io_errors_remain_transient() {
        for kind in [
            std::io::ErrorKind::ConnectionRefused,
            std::io::ErrorKind::ConnectionReset,
            std::io::ErrorKind::UnexpectedEof,
            std::io::ErrorKind::TimedOut,
            std::io::ErrorKind::InvalidData,
        ] {
            let error = Error::Io(std::io::Error::new(kind, "ordinary I/O error"));
            assert_eq!(
                classify_connect_error(&error),
                super::ConnectionFailure::Transient
            );
        }
    }

    #[test]
    fn reconnect_jitter_never_exceeds_its_cap() {
        for _ in 0..100 {
            assert!(full_jitter(RECONNECT_CAP) <= RECONNECT_CAP);
            assert!(full_jitter(Duration::ZERO).is_zero());
        }
    }
}
