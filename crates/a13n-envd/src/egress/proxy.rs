use std::{convert::Infallible, io, sync::Arc, time::Duration};

use bytes::Bytes;
use futures_util::stream;
use http_body_util::{BodyExt, Full, StreamBody, combinators::BoxBody};
use hyper::{
    Request, Response, StatusCode,
    body::{Frame, Incoming},
    header::{self, HeaderMap, HeaderValue},
    service::service_fn,
};
use hyper_util::rt::TokioIo;
use tokio::{
    io::{AsyncRead, AsyncWrite},
    net::TcpStream,
    sync::mpsc,
    time::timeout,
};
use tokio_rustls::{TlsAcceptor, TlsConnector};

use super::{
    address,
    policy::{Policy, Snapshot},
    tls::Authority,
};

type Body = BoxBody<Bytes, io::Error>;
const IO_TIMEOUT: Duration = Duration::from_secs(30);

pub(crate) struct Proxy {
    pub policy: Arc<Policy>,
    pub authority: Authority,
}

impl Proxy {
    pub fn new(policy: Arc<Policy>, authority: Authority) -> Arc<Self> {
        Arc::new(Self { policy, authority })
    }

    /// `host` comes from the trusted synthetic DNS map, never solely from SNI/Host.
    pub async fn serve(
        self: Arc<Self>,
        socket: TcpStream,
        host: String,
        port: u16,
    ) -> io::Result<()> {
        let mut changes = self.policy.subscribe();
        let mut snapshot = self
            .policy
            .snapshot()
            .map_err(|_| io::Error::other("closed policy"))?;
        if !snapshot.permits(&host) {
            return Err(denied());
        }
        let processing = async {
            let domain = host.parse::<std::net::IpAddr>().is_err();
            if port == 443 && domain {
                let acceptor = TlsAcceptor::from(self.authority.server(&host)?);
                let tls = timeout(IO_TIMEOUT, acceptor.accept(socket))
                    .await
                    .map_err(io::Error::other)??;
                if tls.get_ref().1.server_name() != Some(host.as_str()) {
                    return Err(denied());
                }
                self.clone().http(tls, host.clone(), true).await
            } else if port == 80 && domain {
                self.clone().http(socket, host.clone(), false).await
            } else {
                let addresses = timeout(IO_TIMEOUT, address::resolve_public(&host, port))
                    .await
                    .map_err(io::Error::other)??;
                let mut upstream = timeout(IO_TIMEOUT, TcpStream::connect(addresses.as_slice()))
                    .await
                    .map_err(io::Error::other)??;
                let mut socket = socket;
                tokio::io::copy_bidirectional(&mut socket, &mut upstream).await?;
                Ok(())
            }
        };
        tokio::pin!(processing);
        loop {
            tokio::select! {
                result = &mut processing => return result,
                _ = changes.changed() => {
                    let current = self.policy.snapshot().map_err(|_| denied())?;
                    if !current.preserves(&snapshot, &host) { return Err(denied()); }
                    snapshot = current;
                }
            }
        }
    }

    async fn http<S>(self: Arc<Self>, socket: S, host: String, secure: bool) -> io::Result<()>
    where
        S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
    {
        let service = service_fn(move |request| {
            let proxy = self.clone();
            let host = host.clone();
            async move {
                let response = match proxy.request(request, &host, secure).await {
                    Ok(response) => response,
                    Err(_) => Response::builder()
                        .status(StatusCode::BAD_GATEWAY)
                        .body(full(b"egress request denied or unavailable"))
                        .expect("constant response"),
                };
                Ok::<_, Infallible>(response)
            }
        });
        hyper::server::conn::http1::Builder::new()
            .max_headers(128)
            .max_buf_size(64 * 1024)
            .serve_connection(TokioIo::new(socket), service)
            .await
            .map_err(io::Error::other)
    }

    async fn request(
        &self,
        mut request: Request<Incoming>,
        host: &str,
        secure: bool,
    ) -> io::Result<Response<Body>> {
        let snapshot = self.policy.snapshot().map_err(|_| denied())?;
        if !snapshot.permits(host)
            || request.method() == hyper::Method::CONNECT
            || request.headers().contains_key(header::UPGRADE)
            || request.uri().authority().is_some()
        {
            return Err(denied());
        }
        let authority = request
            .headers()
            .get(header::HOST)
            .ok_or_else(denied)?
            .to_str()
            .map_err(|_| denied())?;
        let expected = if secure {
            format!("{host}:443")
        } else {
            format!("{host}:80")
        };
        if !authority.eq_ignore_ascii_case(host) && !authority.eq_ignore_ascii_case(&expected) {
            return Err(denied());
        }
        if secure {
            *request.headers_mut() = snapshot
                .inject(host, request.headers())
                .map_err(|_| denied())?;
        } else if snapshot.contains_marker(request.headers()) {
            return Err(denied());
        }
        strip_hop_headers(request.headers_mut());
        request.headers_mut().insert(
            header::ACCEPT_ENCODING,
            HeaderValue::from_static("identity"),
        );
        let port = if secure { 443 } else { 80 };
        let addresses = timeout(IO_TIMEOUT, address::resolve_public(host, port))
            .await
            .map_err(io::Error::other)??;
        let upstream = timeout(IO_TIMEOUT, TcpStream::connect(addresses.as_slice()))
            .await
            .map_err(io::Error::other)??;
        let (response, connection) = if secure {
            let name =
                rustls::pki_types::ServerName::try_from(host.to_owned()).map_err(|_| denied())?;
            let tls = timeout(
                IO_TIMEOUT,
                TlsConnector::from(self.authority.upstream()).connect(name, upstream),
            )
            .await
            .map_err(io::Error::other)??;
            send(tls, request).await?
        } else {
            send(upstream, request).await?
        };
        scrub_response(response, snapshot, connection)
    }
}

async fn send<S>(
    socket: S,
    request: Request<Incoming>,
) -> io::Result<(Response<Incoming>, Connection)>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(socket))
        .await
        .map_err(io::Error::other)?;
    let connection = Connection(tokio::spawn(async move {
        let _ = connection.await;
    }));
    let response = timeout(IO_TIMEOUT, sender.send_request(request))
        .await
        .map_err(io::Error::other)?
        .map_err(io::Error::other)?;
    Ok((response, connection))
}

struct Connection(tokio::task::JoinHandle<()>);
impl Drop for Connection {
    fn drop(&mut self) {
        self.0.abort();
    }
}

fn scrub_response(
    response: Response<Incoming>,
    snapshot: Arc<Snapshot>,
    connection: Connection,
) -> io::Result<Response<Body>> {
    let (mut parts, mut body) = response.into_parts();
    if parts.status == StatusCode::SWITCHING_PROTOCOLS {
        return Err(denied());
    }
    // Never search compressed bytes and claim their decoded content is scrubbed.
    if parts
        .headers
        .get(header::CONTENT_ENCODING)
        .is_some_and(|v| v != "identity")
    {
        return Err(denied());
    }
    parts.headers = scrub_headers(&parts.headers, &snapshot)?;
    strip_hop_headers(&mut parts.headers);
    parts.headers.remove(header::CONTENT_LENGTH);
    parts.headers.remove("content-md5");
    parts.headers.remove("digest");
    parts.headers.remove(header::ETAG);
    let (tx, rx) = mpsc::channel(2);
    tokio::spawn(async move {
        let _connection = connection;
        let mut scrubber = snapshot.scrubber();
        loop {
            let next = tokio::select! {
                _ = tx.closed() => return,
                frame = timeout(IO_TIMEOUT, body.frame()) => frame,
            };
            let frame = match next {
                Ok(Some(Ok(frame))) => frame,
                Ok(None) => {
                    let tail = scrubber.push(&[], true);
                    if !tail.is_empty() {
                        let _ = tx.send(Ok(Frame::data(Bytes::from(tail)))).await;
                    }
                    return;
                }
                _ => {
                    let _ = tx
                        .send(Err(io::Error::other("upstream body interrupted")))
                        .await;
                    return;
                }
            };
            let output = match frame.into_data() {
                Ok(data) => Some(Ok(Frame::data(Bytes::from(scrubber.push(&data, false))))),
                Err(frame) => match frame.into_trailers() {
                    Ok(trailers) => {
                        let tail = scrubber.push(&[], true);
                        if !tail.is_empty()
                            && tx.send(Ok(Frame::data(Bytes::from(tail)))).await.is_err()
                        {
                            return;
                        }
                        Some(scrub_headers(&trailers, &snapshot).map(Frame::trailers))
                    }
                    Err(_) => None,
                },
            };
            if let Some(output) = output
                && tx.send(output).await.is_err()
            {
                return;
            }
        }
    });
    let stream = stream::unfold(rx, |mut rx| async {
        rx.recv().await.map(|item| (item, rx))
    });
    Ok(Response::from_parts(
        parts,
        BodyExt::boxed(StreamBody::new(stream)),
    ))
}

fn scrub_headers(headers: &HeaderMap, snapshot: &Snapshot) -> io::Result<HeaderMap> {
    let mut result = HeaderMap::with_capacity(headers.len());
    for (name, value) in headers {
        // Header names cannot safely be rewritten into arbitrary sentinel strings.
        if snapshot.scrubber().push(name.as_str().as_bytes(), true) != name.as_str().as_bytes() {
            return Err(denied());
        }
        let value = snapshot.scrubber().push(value.as_bytes(), true);
        result.append(
            name.clone(),
            HeaderValue::from_bytes(&value).map_err(|_| denied())?,
        );
    }
    Ok(result)
}

fn strip_hop_headers(headers: &mut HeaderMap) {
    let nominated: Vec<_> = headers
        .get_all(header::CONNECTION)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(','))
        .map(|value| value.trim().to_owned())
        .collect();
    for name in nominated {
        headers.remove(name);
    }
    for name in [
        "connection",
        "proxy-connection",
        "proxy-authorization",
        "proxy-authenticate",
        "keep-alive",
        "upgrade",
        "transfer-encoding",
        "te",
        "trailer",
    ] {
        headers.remove(name);
    }
}
fn full(value: &'static [u8]) -> Body {
    Full::new(Bytes::from_static(value))
        .map_err(|never| match never {})
        .boxed()
}
fn denied() -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, "egress denied")
}

#[cfg(test)]
mod tests {
    use super::super::policy::{PolicyInput, SecretInput, Update};
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn response_redacts_headers_chunks_and_trailers_using_request_revision() {
        let policy = Policy::new(PolicyInput {
            destinations: crate::egress::policy::public_destinations(),
            secrets: vec![SecretInput {
                env: "TOKEN".into(),
                value: "old-secret".into(),
                inject_hosts: vec!["example.com".into()],
            }],
        })
        .unwrap();
        let snapshot = policy.snapshot().unwrap();
        let marker = snapshot.environment()["TOKEN"].clone();
        policy
            .update(Update {
                expected_revision: 1,
                destinations: None,
                remove_secrets: vec![],
                set_secrets: vec![SecretInput {
                    env: "TOKEN".into(),
                    value: "new-secret".into(),
                    inject_hosts: vec!["example.com".into()],
                }],
            })
            .unwrap();
        let (client, mut server) = tokio::io::duplex(4096);
        let upstream = tokio::spawn(async move {
            let mut request = [0; 1024];
            assert!(server.read(&mut request).await.unwrap() > 0);
            server.write_all(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nX-Echo: old-secret\r\nTrailer: X-Echo\r\n\r\n").await.unwrap();
            for chunk in [b"hello old-".as_slice(), b"secret world"] {
                server
                    .write_all(format!("{:x}\r\n", chunk.len()).as_bytes())
                    .await
                    .unwrap();
                server.write_all(chunk).await.unwrap();
                server.write_all(b"\r\n").await.unwrap();
                tokio::task::yield_now().await;
            }
            server
                .write_all(b"0\r\nX-Echo: old-secret\r\n\r\n")
                .await
                .unwrap();
        });
        let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(client))
            .await
            .unwrap();
        let connection = Connection(tokio::spawn(async move {
            let _ = connection.await;
        }));
        let response = sender
            .send_request(
                Request::builder()
                    .uri("/")
                    .body(Full::new(Bytes::new()))
                    .unwrap(),
            )
            .await
            .unwrap();
        let response = scrub_response(response, snapshot, connection).unwrap();
        assert_eq!(response.headers()["x-echo"], marker);
        assert!(!response.headers().contains_key(header::CONTENT_LENGTH));
        let collected = response.into_body().collect().await.unwrap();
        assert_eq!(collected.trailers().unwrap()["x-echo"], marker);
        assert_eq!(collected.to_bytes(), format!("hello {marker} world"));
        upstream.await.unwrap();
    }

    #[test]
    fn secret_in_response_header_name_is_rejected() {
        let policy = Policy::new(PolicyInput {
            destinations: crate::egress::policy::public_destinations(),
            secrets: vec![SecretInput {
                env: "TOKEN".into(),
                value: "secret".into(),
                inject_hosts: vec!["example.com".into()],
            }],
        })
        .unwrap();
        let mut headers = HeaderMap::new();
        headers.insert("x-secret", HeaderValue::from_static("value"));
        assert!(scrub_headers(&headers, &policy.snapshot().unwrap()).is_err());
    }
}
