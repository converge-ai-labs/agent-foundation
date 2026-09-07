use std::{
    collections::HashMap,
    convert::Infallible,
    io::{self, BufReader},
    pin::Pin,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    task::{Context, Poll},
    time::{Duration, Instant},
};

use base64::Engine as _;
use bytes::Bytes;
use futures_util::stream;
use http_body_util::{BodyExt, Full, StreamBody, combinators::BoxBody};
use hyper::{
    Method, Request, Response, StatusCode,
    body::{Body, Frame, Incoming, SizeHint},
    header::{self, HeaderName, HeaderValue},
    server::conn::http1,
    service::service_fn,
};
use hyper_util::rt::TokioIo;
use tokio::{
    io::{AsyncRead, AsyncWrite},
    net::TcpListener,
    sync::{Mutex, OwnedSemaphorePermit, RwLock, Semaphore, mpsc},
    task::JoinHandle,
    time::{MissedTickBehavior, timeout},
};
use tokio_rustls::{TlsAcceptor, rustls};

use crate::{
    config::{Config, HttpConfig},
    daemon::Daemon,
    eip::{DataFrame, DataFrameKind, DataResetStatus},
    operation::ActiveResponseHandoff,
};

const CONTROL_PATH: &str = "/eip/control";
const TRANSFER_PATH: &str = "/eip/transfer";
const SESSION_HEADER: HeaderName = HeaderName::from_static("eip-session");
const TRANSFER_HANDLE_HEADER: HeaderName = HeaderName::from_static("eip-transfer-handle");
const TRANSFER_DIRECTION_HEADER: HeaderName = HeaderName::from_static("eip-transfer-direction");
const JSON_CONTENT_TYPE: &str = "application/json";
const BINARY_CONTENT_TYPE: &str = "application/octet-stream";
const SESSION_DRAIN_TIMEOUT: Duration = Duration::from_secs(1);
const TRANSFER_WAIT_TIMEOUT: Duration = Duration::from_secs(30);

type HttpBody = BoxBody<Bytes, io::Error>;
type HttpResponse = Response<HttpBody>;

struct ControlResponseBody {
    payload: Option<Bytes>,
    handoff: Option<ActiveResponseHandoff>,
}

impl Body for ControlResponseBody {
    type Data = Bytes;
    type Error = io::Error;

    fn poll_frame(
        self: Pin<&mut Self>,
        _context: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Self::Data>, Self::Error>>> {
        let this = self.get_mut();
        let Some(payload) = this.payload.take() else {
            return Poll::Ready(None);
        };
        if let Some(handoff) = this.handoff.take() {
            handoff.complete();
        }
        Poll::Ready(Some(Ok(Frame::data(payload))))
    }

    fn size_hint(&self) -> SizeHint {
        let mut hint = SizeHint::new();
        let size = self.payload.as_ref().map_or(0, Bytes::len) as u64;
        hint.set_exact(size);
        hint
    }
}

struct HttpSession {
    selector: String,
    routes: Arc<Mutex<HashMap<String, mpsc::Sender<DataFrame>>>>,
    dispatcher: JoinHandle<()>,
    admission: RwLock<()>,
    last_activity: Mutex<Instant>,
    closed: AtomicBool,
}

struct ReaderBodyState {
    receiver: mpsc::Receiver<DataFrame>,
    routes: Arc<Mutex<HashMap<String, mpsc::Sender<DataFrame>>>>,
    daemon: Arc<Daemon>,
    handle: String,
    offset: u64,
    timeout: Duration,
    complete: bool,
    _permit: OwnedSemaphorePermit,
}

impl Drop for ReaderBodyState {
    fn drop(&mut self) {
        if self.complete {
            return;
        }
        let routes = Arc::clone(&self.routes);
        let daemon = Arc::clone(&self.daemon);
        let handle = self.handle.clone();
        let offset = self.offset;
        tokio::spawn(async move {
            reset_http_transfer(&daemon, &handle, offset).await;
            routes.lock().await.remove(&handle);
        });
    }
}

struct HttpState {
    daemon: Arc<Daemon>,
    credential: Vec<u8>,
    max_request_bytes: usize,
    max_response_bytes: usize,
    max_transfer_bytes: u64,
    max_transfer_chunk_bytes: usize,
    session_idle_timeout: Duration,
    transfer_timeout: Duration,
    control_admission: Arc<Semaphore>,
    transfer_admission: Arc<Semaphore>,
    session: Mutex<Option<Arc<HttpSession>>>,
}

pub(crate) async fn serve(
    daemon: Arc<Daemon>,
    config: &Config,
    http: &HttpConfig,
) -> io::Result<()> {
    let credential = read_credential(&http.credential_file).await?;
    let state = Arc::new(HttpState {
        daemon: Arc::clone(&daemon),
        credential,
        max_request_bytes: usize::try_from(config.limits.max_request_bytes)
            .map_err(|_| invalid_data("max_request_bytes does not fit this platform"))?,
        max_response_bytes: usize::try_from(config.limits.max_response_bytes)
            .map_err(|_| invalid_data("max_response_bytes does not fit this platform"))?,
        max_transfer_bytes: config.limits.max_staged_file_bytes,
        max_transfer_chunk_bytes: usize::try_from(config.limits.max_transfer_frame_bytes)
            .map_err(|_| invalid_data("max_transfer_frame_bytes does not fit this platform"))?
            .saturating_sub(1024)
            .max(1),
        session_idle_timeout: config.session_idle_timeout,
        transfer_timeout: Duration::from_millis(config.limits.max_file_transfer_duration_ms),
        control_admission: Arc::new(Semaphore::new(
            usize::try_from(config.limits.max_concurrent_operations).map_err(|_| {
                invalid_data("max_concurrent_operations does not fit this platform")
            })?,
        )),
        transfer_admission: Arc::new(Semaphore::new(
            usize::try_from(config.limits.max_concurrent_file_transfers).map_err(|_| {
                invalid_data("max_concurrent_file_transfers does not fit this platform")
            })?,
        )),
        session: Mutex::new(None),
    });
    let listener = TcpListener::bind(http.bind).await?;
    let tls = build_tls_acceptor(http)?;
    let mut shutdown = std::pin::pin!(shutdown_signal());
    let mut maintenance = tokio::time::interval(Duration::from_secs(1));
    maintenance.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, _) = accepted?;
                let state = Arc::clone(&state);
                let tls = tls.clone();
                tokio::spawn(async move {
                    let result = match tls {
                        Some(acceptor) => match acceptor.accept(stream).await {
                            Ok(stream) => serve_connection(stream, state).await,
                            Err(_) => Ok(()),
                        },
                        None => serve_connection(stream, state).await,
                    };
                    if let Err(error) = result {
                        let record = serde_json::json!({
                            "level": "warning",
                            "event": "a13n-envd.http.connection_failed",
                            "message": error.to_string(),
                        });
                        eprintln!("{record}");
                    }
                });
            }
            _ = maintenance.tick() => {
                state.daemon.maintenance().await;
                expire_idle_session(&state).await;
            },
            signal = &mut shutdown => {
                signal?;
                break;
            }
        }
    }
    close_current_session(&state).await;
    let processes_drained = daemon.drain_processes(SESSION_DRAIN_TIMEOUT).await;
    let operations_drained = daemon.drain_owned_operations(SESSION_DRAIN_TIMEOUT).await;
    if !processes_drained || !operations_drained {
        return Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "HTTP generation drain exceeded its deadline",
        ));
    }
    Ok(())
}

async fn serve_connection<I>(stream: I, state: Arc<HttpState>) -> io::Result<()>
where
    I: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    http1::Builder::new()
        .serve_connection(
            TokioIo::new(stream),
            service_fn(move |request| {
                let state = Arc::clone(&state);
                async move { Ok::<_, Infallible>(route(request, state).await) }
            }),
        )
        .await
        .map_err(|error| io::Error::other(format!("HTTP connection failed: {error}")))
}

async fn route(request: Request<Incoming>, state: Arc<HttpState>) -> HttpResponse {
    if request.method() != Method::POST {
        return empty_response(StatusCode::METHOD_NOT_ALLOWED);
    }
    if request.uri().query().is_some() {
        return empty_response(StatusCode::BAD_REQUEST);
    }
    if !authorized(request.headers(), &state.credential) {
        return empty_response(StatusCode::UNAUTHORIZED);
    }
    if request.headers().contains_key(header::COOKIE)
        || request.headers().contains_key(header::CONTENT_ENCODING)
    {
        return empty_response(StatusCode::BAD_REQUEST);
    }
    match request.uri().path() {
        CONTROL_PATH => control(request, state).await,
        TRANSFER_PATH => transfer(request, state).await,
        _ => empty_response(StatusCode::NOT_FOUND),
    }
}

async fn control(request: Request<Incoming>, state: Arc<HttpState>) -> HttpResponse {
    let Ok(_permit) = Arc::clone(&state.control_admission).try_acquire_owned() else {
        return empty_response(StatusCode::TOO_MANY_REQUESTS);
    };
    if !content_type_is(request.headers(), JSON_CONTENT_TYPE)
        || request.headers().contains_key(&TRANSFER_HANDLE_HEADER)
        || request.headers().contains_key(&TRANSFER_DIRECTION_HEADER)
    {
        return empty_response(StatusCode::UNSUPPORTED_MEDIA_TYPE);
    }
    let has_session_header = request.headers().contains_key(&SESSION_HEADER);
    let supplied_selector = header_text(request.headers(), &SESSION_HEADER).map(str::to_owned);
    if has_session_header && supplied_selector.is_none() {
        return empty_response(StatusCode::BAD_REQUEST);
    }
    let body = match read_body_bounded(request.into_body(), state.max_request_bytes).await {
        Ok(body) => body,
        Err(status) => return empty_response(status),
    };
    let payload = match String::from_utf8(body) {
        Ok(payload) => payload,
        Err(_) => return empty_response(StatusCode::BAD_REQUEST),
    };

    let session = if let Some(selector) = supplied_selector.as_deref() {
        match current_session(&state, selector).await {
            Some(session) => session,
            None => return empty_response(StatusCode::CONFLICT),
        }
    } else {
        match begin_http_session(&state).await {
            Ok(session) => session,
            Err(status) => return empty_response(status),
        }
    };
    let admission = session.admission.read().await;
    if session.closed.load(Ordering::Acquire) {
        return empty_response(StatusCode::CONFLICT);
    }

    let response = state.daemon.handle_payload_for_carrier(&payload).await;
    let (payload, handoff, closes_session) = response.into_parts();
    if payload.len() > state.max_response_bytes {
        close_session(&state, &session).await;
        return empty_response(StatusCode::INTERNAL_SERVER_ERROR);
    }
    let initialized = state.daemon.session_initialized();
    if supplied_selector.is_none() && !initialized {
        close_session(&state, &session).await;
    }
    let mut response = control_response(payload, handoff);
    if supplied_selector.is_none()
        && initialized
        && let Ok(value) = HeaderValue::from_str(&session.selector)
    {
        response.headers_mut().insert(SESSION_HEADER, value);
    }
    drop(admission);
    if closes_session {
        close_session(&state, &session).await;
    }
    response
}

async fn transfer(request: Request<Incoming>, state: Arc<HttpState>) -> HttpResponse {
    let Ok(permit) = Arc::clone(&state.transfer_admission).try_acquire_owned() else {
        return empty_response(StatusCode::TOO_MANY_REQUESTS);
    };
    if !content_type_is(request.headers(), BINARY_CONTENT_TYPE) {
        return empty_response(StatusCode::UNSUPPORTED_MEDIA_TYPE);
    }
    let Some(selector) = header_text(request.headers(), &SESSION_HEADER) else {
        return empty_response(StatusCode::CONFLICT);
    };
    let Some(handle) = header_text(request.headers(), &TRANSFER_HANDLE_HEADER).map(str::to_owned)
    else {
        return empty_response(StatusCode::BAD_REQUEST);
    };
    let Some(direction) = header_text(request.headers(), &TRANSFER_DIRECTION_HEADER) else {
        return empty_response(StatusCode::BAD_REQUEST);
    };
    if !matches!(direction, "read" | "write") {
        return empty_response(StatusCode::BAD_REQUEST);
    }
    let Some(session) = current_session(&state, selector).await else {
        return empty_response(StatusCode::CONFLICT);
    };
    let _admission = session.admission.read().await;
    if session.closed.load(Ordering::Acquire) {
        return empty_response(StatusCode::CONFLICT);
    }
    if handle.is_empty() || handle.len() > 512 {
        return empty_response(StatusCode::BAD_REQUEST);
    }

    let (tx, rx) = mpsc::channel(16);
    {
        let mut routes = session.routes.lock().await;
        if routes.insert(handle.clone(), tx).is_some() {
            return empty_response(StatusCode::CONFLICT);
        }
    }
    match direction {
        "read" => {
            reader_transfer(
                request.into_body(),
                state,
                Arc::clone(&session),
                handle,
                rx,
                permit,
            )
            .await
        }
        "write" => {
            writer_transfer(
                request.into_body(),
                state,
                Arc::clone(&session),
                handle,
                rx,
                permit,
            )
            .await
        }
        _ => unreachable!("transfer direction validated before route registration"),
    }
}

async fn reader_transfer(
    mut body: Incoming,
    state: Arc<HttpState>,
    session: Arc<HttpSession>,
    handle: String,
    mut rx: mpsc::Receiver<DataFrame>,
    permit: OwnedSemaphorePermit,
) -> HttpResponse {
    if body.frame().await.is_some() {
        session.routes.lock().await.remove(&handle);
        return empty_response(StatusCode::BAD_REQUEST);
    }
    let attached = state
        .daemon
        .handle_data_frame(DataFrame {
            kind: DataFrameKind::Attach,
            handle: handle.clone(),
            offset: 0,
            payload: Vec::new(),
            reset_status: None,
        })
        .await;
    if attached.is_err() {
        session.routes.lock().await.remove(&handle);
        return empty_response(StatusCode::CONFLICT);
    }
    match timeout(TRANSFER_WAIT_TIMEOUT, rx.recv()).await {
        Ok(Some(frame)) if frame.kind == DataFrameKind::Attached => {}
        _ => {
            session.routes.lock().await.remove(&handle);
            return empty_response(StatusCode::CONFLICT);
        }
    }
    let chunks = stream::unfold(
        ReaderBodyState {
            receiver: rx,
            routes: Arc::clone(&session.routes),
            daemon: Arc::clone(&state.daemon),
            handle,
            offset: 0,
            timeout: state.transfer_timeout,
            complete: false,
            _permit: permit,
        },
        |mut transfer| async move {
            match timeout(transfer.timeout, transfer.receiver.recv()).await {
                Ok(Some(frame)) if frame.kind == DataFrameKind::Chunk => {
                    transfer.offset = frame.offset.saturating_add(frame.payload.len() as u64);
                    Some((
                        Ok::<Frame<Bytes>, io::Error>(Frame::data(Bytes::from(frame.payload))),
                        transfer,
                    ))
                }
                Ok(Some(frame)) if frame.kind == DataFrameKind::End => {
                    transfer.complete = true;
                    transfer.routes.lock().await.remove(&transfer.handle);
                    None
                }
                Ok(Some(_)) | Ok(None) | Err(_) => {
                    transfer.complete = true;
                    transfer.routes.lock().await.remove(&transfer.handle);
                    Some((
                        Err(io::Error::new(
                            io::ErrorKind::InvalidData,
                            "EIP reader transfer reset",
                        )),
                        transfer,
                    ))
                }
            }
        },
    );
    let body = BodyExt::boxed(StreamBody::new(chunks));
    response_with_body(StatusCode::OK, BINARY_CONTENT_TYPE, body)
}

async fn writer_transfer(
    mut body: Incoming,
    state: Arc<HttpState>,
    session: Arc<HttpSession>,
    handle: String,
    mut rx: mpsc::Receiver<DataFrame>,
    _permit: OwnedSemaphorePermit,
) -> HttpResponse {
    if state
        .daemon
        .handle_data_frame(DataFrame {
            kind: DataFrameKind::Attach,
            handle: handle.clone(),
            offset: 0,
            payload: Vec::new(),
            reset_status: None,
        })
        .await
        .is_err()
    {
        session.routes.lock().await.remove(&handle);
        return empty_response(StatusCode::CONFLICT);
    }
    match timeout(TRANSFER_WAIT_TIMEOUT, rx.recv()).await {
        Ok(Some(frame)) if frame.kind == DataFrameKind::Attached => {}
        _ => {
            session.routes.lock().await.remove(&handle);
            return empty_response(StatusCode::CONFLICT);
        }
    }
    let mut offset = 0_u64;
    loop {
        let next_frame = match timeout(state.transfer_timeout, body.frame()).await {
            Ok(next_frame) => next_frame,
            Err(_) => {
                reset_http_transfer(&state.daemon, &handle, offset).await;
                session.routes.lock().await.remove(&handle);
                return empty_response(StatusCode::REQUEST_TIMEOUT);
            }
        };
        let Some(frame) = next_frame else {
            break;
        };
        let frame = match frame {
            Ok(frame) => frame,
            Err(_) => {
                reset_http_transfer(&state.daemon, &handle, offset).await;
                session.routes.lock().await.remove(&handle);
                return empty_response(StatusCode::BAD_REQUEST);
            }
        };
        let Ok(data) = frame.into_data() else {
            continue;
        };
        let next = offset.saturating_add(data.len() as u64);
        if next > state.max_transfer_bytes {
            reset_http_transfer(&state.daemon, &handle, offset).await;
            session.routes.lock().await.remove(&handle);
            return empty_response(StatusCode::PAYLOAD_TOO_LARGE);
        }
        for chunk in data.chunks(state.max_transfer_chunk_bytes) {
            if state
                .daemon
                .handle_data_frame(DataFrame {
                    kind: DataFrameKind::Chunk,
                    handle: handle.clone(),
                    offset,
                    payload: chunk.to_vec(),
                    reset_status: None,
                })
                .await
                .is_err()
            {
                session.routes.lock().await.remove(&handle);
                return empty_response(StatusCode::CONFLICT);
            }
            offset += chunk.len() as u64;
        }
    }
    if state
        .daemon
        .handle_data_frame(DataFrame {
            kind: DataFrameKind::End,
            handle: handle.clone(),
            offset,
            payload: Vec::new(),
            reset_status: None,
        })
        .await
        .is_err()
    {
        session.routes.lock().await.remove(&handle);
        return empty_response(StatusCode::CONFLICT);
    }
    let status = match timeout(TRANSFER_WAIT_TIMEOUT, rx.recv()).await {
        Ok(Some(frame)) if frame.kind == DataFrameKind::EndAck && frame.offset == offset => {
            StatusCode::NO_CONTENT
        }
        _ => StatusCode::CONFLICT,
    };
    session.routes.lock().await.remove(&handle);
    empty_response(status)
}

async fn reset_http_transfer(daemon: &Daemon, handle: &str, offset: u64) {
    let _ = daemon
        .handle_data_frame(DataFrame {
            kind: DataFrameKind::Reset,
            handle: handle.to_owned(),
            offset,
            payload: Vec::new(),
            reset_status: Some(DataResetStatus::Cancelled),
        })
        .await;
}

async fn begin_http_session(state: &Arc<HttpState>) -> Result<Arc<HttpSession>, StatusCode> {
    let mut current = state.session.lock().await;
    if current
        .as_ref()
        .is_some_and(|session| !session.closed.load(Ordering::Acquire))
    {
        return Err(StatusCode::CONFLICT);
    }
    let selector = fresh_selector().map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let routes = Arc::new(Mutex::new(HashMap::<String, mpsc::Sender<DataFrame>>::new()));
    let (data_tx, mut data_rx) = mpsc::channel(128);
    state
        .daemon
        .begin_session(data_tx)
        .map_err(|_| StatusCode::CONFLICT)?;
    let dispatcher_routes = Arc::clone(&routes);
    let dispatcher_daemon = Arc::clone(&state.daemon);
    let dispatcher = tokio::spawn(async move {
        while let Some(frame) = data_rx.recv().await {
            let route = dispatcher_routes.lock().await.get(&frame.handle).cloned();
            let Some(route) = route else {
                continue;
            };
            if route.try_send(frame.clone()).is_err() {
                dispatcher_routes.lock().await.remove(&frame.handle);
                reset_http_transfer(&dispatcher_daemon, &frame.handle, frame.offset).await;
            }
        }
    });
    let session = Arc::new(HttpSession {
        selector,
        routes,
        dispatcher,
        admission: RwLock::new(()),
        last_activity: Mutex::new(Instant::now()),
        closed: AtomicBool::new(false),
    });
    *current = Some(Arc::clone(&session));
    Ok(session)
}

async fn current_session(state: &Arc<HttpState>, selector: &str) -> Option<Arc<HttpSession>> {
    let session = {
        let current = state.session.lock().await;
        current
            .as_ref()
            .filter(|session| {
                !session.closed.load(Ordering::Acquire)
                    && secure_equal(session.selector.as_bytes(), selector.as_bytes())
            })
            .cloned()
    }?;
    *session.last_activity.lock().await = Instant::now();
    Some(session)
}

async fn expire_idle_session(state: &Arc<HttpState>) {
    if state.daemon.has_active_file_transfers() {
        return;
    }
    let session = state.session.lock().await.clone();
    let Some(session) = session else {
        return;
    };
    if session.last_activity.lock().await.elapsed() >= state.session_idle_timeout {
        close_session(state, &session).await;
    }
}

async fn close_current_session(state: &Arc<HttpState>) {
    let session = state.session.lock().await.clone();
    if let Some(session) = session {
        close_session(state, &session).await;
    }
}

async fn close_session(state: &Arc<HttpState>, session: &Arc<HttpSession>) {
    if session.closed.swap(true, Ordering::AcqRel) {
        return;
    }
    let _admission = session.admission.write().await;
    session.routes.lock().await.clear();
    session.dispatcher.abort();
    let _ = state.daemon.transport_closed(SESSION_DRAIN_TIMEOUT).await;
    let mut current = state.session.lock().await;
    if current
        .as_ref()
        .is_some_and(|candidate| Arc::ptr_eq(candidate, session))
    {
        *current = None;
    }
}

async fn read_body_bounded(mut body: Incoming, maximum: usize) -> Result<Vec<u8>, StatusCode> {
    let mut output = Vec::new();
    while let Some(frame) = body.frame().await {
        let frame = frame.map_err(|_| StatusCode::BAD_REQUEST)?;
        let Ok(data) = frame.into_data() else {
            continue;
        };
        if output.len().saturating_add(data.len()) > maximum {
            return Err(StatusCode::PAYLOAD_TOO_LARGE);
        }
        output.extend_from_slice(&data);
    }
    Ok(output)
}

fn authorized(headers: &hyper::HeaderMap, credential: &[u8]) -> bool {
    let Some(value) = headers.get(header::AUTHORIZATION) else {
        return false;
    };
    let Ok(value) = value.to_str() else {
        return false;
    };
    let Some(token) = value.strip_prefix("Bearer ") else {
        return false;
    };
    secure_equal(token.as_bytes(), credential)
}

fn secure_equal(left: &[u8], right: &[u8]) -> bool {
    let mut difference = left.len() ^ right.len();
    for index in 0..left.len().max(right.len()) {
        difference |= usize::from(
            left.get(index).copied().unwrap_or(0) ^ right.get(index).copied().unwrap_or(0),
        );
    }
    difference == 0
}

fn content_type_is(headers: &hyper::HeaderMap, expected: &str) -> bool {
    headers
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.eq_ignore_ascii_case(expected))
}

fn header_text<'a>(headers: &'a hyper::HeaderMap, name: &HeaderName) -> Option<&'a str> {
    headers.get(name)?.to_str().ok()
}

fn empty_response(status: StatusCode) -> HttpResponse {
    response_with_body(
        status,
        BINARY_CONTENT_TYPE,
        Full::new(Bytes::new()).map_err(infallible_to_io).boxed(),
    )
}

fn control_response(payload: Vec<u8>, handoff: Option<ActiveResponseHandoff>) -> HttpResponse {
    response_with_body(
        StatusCode::OK,
        JSON_CONTENT_TYPE,
        ControlResponseBody {
            payload: Some(Bytes::from(payload)),
            handoff,
        }
        .boxed(),
    )
}

fn response_with_body(status: StatusCode, content_type: &str, body: HttpBody) -> HttpResponse {
    let mut response = Response::new(body);
    *response.status_mut() = status;
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_str(content_type).expect("static content type is valid"),
    );
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

fn infallible_to_io(error: Infallible) -> io::Error {
    match error {}
}

fn fresh_selector() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; 24];
    getrandom::fill(&mut bytes)?;
    Ok(base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes))
}

async fn read_credential(path: &std::path::Path) -> io::Result<Vec<u8>> {
    let mut bytes = tokio::fs::read(path).await?;
    if bytes.ends_with(b"\r\n") {
        bytes.truncate(bytes.len() - 2);
    } else if bytes.ends_with(b"\n") {
        bytes.truncate(bytes.len() - 1);
    }
    if bytes.is_empty()
        || bytes.len() > 8 * 1024
        || bytes
            .iter()
            .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
    {
        return Err(invalid_data("invalid HTTP attachment credential"));
    }
    Ok(bytes)
}

fn build_tls_acceptor(http: &HttpConfig) -> io::Result<Option<TlsAcceptor>> {
    let (Some(certificate), Some(private_key)) =
        (&http.tls_certificate_file, &http.tls_private_key_file)
    else {
        return Ok(None);
    };
    let mut certificate_reader = BufReader::new(std::fs::File::open(certificate)?);
    let certificates = rustls_pemfile::certs(&mut certificate_reader)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|_| invalid_data("invalid HTTP TLS certificate file"))?;
    let mut key_reader = BufReader::new(std::fs::File::open(private_key)?);
    let key = rustls_pemfile::private_key(&mut key_reader)
        .map_err(|_| invalid_data("invalid HTTP TLS private key file"))?
        .ok_or_else(|| invalid_data("HTTP TLS private key file contains no key"))?;
    let server = rustls::ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(certificates, key)
        .map_err(|_| invalid_data("HTTP TLS certificate and private key do not match"))?;
    Ok(Some(TlsAcceptor::from(Arc::new(server))))
}

fn invalid_data(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

async fn shutdown_signal() -> io::Result<()> {
    #[cfg(unix)]
    {
        let mut terminate =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
        tokio::select! {
            result = tokio::signal::ctrl_c() => result,
            _ = terminate.recv() => Ok(()),
        }
    }
    #[cfg(not(unix))]
    {
        tokio::signal::ctrl_c().await
    }
}
