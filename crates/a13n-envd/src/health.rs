//! Optional local daemon liveness, independent of EIP and Host connectivity.

use std::{convert::Infallible, future::pending, io, net::SocketAddr, time::Duration};

use bytes::Bytes;
use http_body_util::Full;
use hyper::{
    Method, Request, Response, StatusCode, body::Incoming, server::conn::http1, service::service_fn,
};
use hyper_util::rt::TokioIo;
use serde::{Deserialize, Serialize};
use tokio::{net::TcpListener, task::JoinSet, time::timeout};

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Options {
    pub(crate) enabled: bool,
    pub(crate) bind: SocketAddr,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            enabled: false,
            bind: SocketAddr::from(([127, 0, 0, 1], 8080)),
        }
    }
}

pub(crate) async fn bind(options: &Options) -> io::Result<Option<TcpListener>> {
    if !options.enabled {
        return Ok(None);
    }
    TcpListener::bind(options.bind)
        .await
        .map(Some)
        .map_err(|error| {
            io::Error::new(
                error.kind(),
                format!("cannot bind health listener at {}: {error}", options.bind),
            )
        })
}

pub(crate) async fn serve(listener: Option<TcpListener>) -> io::Result<()> {
    let Some(listener) = listener else {
        return pending().await;
    };
    // Probe connections cannot accumulate indefinitely or hold up shutdown.
    let mut connections = JoinSet::new();
    loop {
        tokio::select! {
            connection = listener.accept(), if connections.len() < 32 => {
                let (stream, _) = match connection {
                    Ok(connection) => connection,
                    Err(error) => {
                        eprintln!("health listener accept failed: {error}");
                        tokio::time::sleep(Duration::from_millis(100)).await;
                        continue;
                    }
                };
                connections.spawn(async move {
                    let _ = timeout(
                        Duration::from_secs(5),
                        http1::Builder::new()
                            .keep_alive(false)
                            .max_headers(32)
                            .max_buf_size(8192)
                            .serve_connection(TokioIo::new(stream), service_fn(respond)),
                    ).await;
                });
            }
            _ = connections.join_next(), if !connections.is_empty() => {}
        }
    }
}

async fn respond(request: Request<Incoming>) -> Result<Response<Full<Bytes>>, Infallible> {
    let mut response = Response::new(Full::new(Bytes::new()));
    response
        .headers_mut()
        .insert("Cache-Control", "no-store".parse().unwrap());
    if !matches!(request.uri().path(), "/healthz" | "/ping") {
        *response.status_mut() = StatusCode::NOT_FOUND;
    } else if request.method() != Method::GET && request.method() != Method::HEAD {
        *response.status_mut() = StatusCode::METHOD_NOT_ALLOWED;
        response
            .headers_mut()
            .insert("Allow", "GET, HEAD".parse().unwrap());
    } else {
        response
            .headers_mut()
            .insert("Content-Type", "text/plain; charset=utf-8".parse().unwrap());
        response
            .headers_mut()
            .insert("Content-Length", "3".parse().unwrap());
        if request.method() == Method::GET {
            *response.body_mut() = Full::new(Bytes::from_static(b"ok\n"));
        }
    }
    Ok(response)
}
