//! Device resource observations, independent of EIP and Session workers.
use std::{
    collections::BTreeMap,
    convert::Infallible,
    io,
    net::SocketAddr,
    path::PathBuf,
    sync::Arc,
    time::{Duration, Instant},
};

use bytes::Bytes;
use http_body_util::Full;
use hyper::{
    Method, Request, Response, StatusCode, body::Incoming, header, server::conn::http1,
    service::service_fn,
};
use hyper_util::rt::TokioIo;
use serde::{Deserialize, Serialize};
use tokio::{
    io::{AsyncRead, AsyncWrite},
    net::TcpListener,
    sync::watch,
    task::{JoinHandle, JoinSet},
    time::{MissedTickBehavior, timeout},
};

use crate::{
    config::{Config, ConfigError, HttpConfig, TransportConfig, validate_bootstrap_file},
    http,
};

mod sample;

const REPORT_PATH: &str = "/monitoring/v1/report";
const MAX_CONNECTIONS: usize = 32;
const CONNECTION_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Options {
    #[serde(default = "unset_bind")]
    pub(crate) bind: SocketAddr,
    #[serde(default)]
    pub(crate) credential_file: PathBuf,
    pub(crate) tls_certificate_file: Option<PathBuf>,
    pub(crate) tls_private_key_file: Option<PathBuf>,
    plaintext_scope: Option<String>,
    #[serde(default = "sample_interval_ms")]
    sample_interval_ms: u64,
    #[serde(default)]
    filesystems: BTreeMap<String, PathBuf>,
    cgroup_v2_directory: Option<PathBuf>,
}

fn unset_bind() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], 0))
}

fn sample_interval_ms() -> u64 {
    10_000
}

impl Options {
    pub(crate) fn validate(&mut self) -> Result<(), ConfigError> {
        if !cfg!(target_os = "linux") {
            return Err(ConfigError::new("monitoring requires Linux"));
        }
        if self.bind.port() == 0 {
            return Err(ConfigError::new(
                "monitoring.bind requires an explicit nonzero port",
            ));
        }
        if !(1_000..=60_000).contains(&self.sample_interval_ms) {
            return Err(ConfigError::new(
                "monitoring.sample_interval_ms must be between 1000 and 60000",
            ));
        }
        if self.tls_certificate_file.is_some() != self.tls_private_key_file.is_some() {
            return Err(ConfigError::new(
                "monitoring TLS certificate and key must be configured together",
            ));
        }
        if self.tls_certificate_file.is_some() {
            if self.plaintext_scope.is_some() {
                return Err(ConfigError::new(
                    "monitoring plaintext_scope is not valid with TLS",
                ));
            }
        } else {
            match self.plaintext_scope.as_deref() {
                Some("loopback") if self.bind.ip().is_loopback() => {}
                Some("provider_private_link") => {}
                _ => {
                    return Err(ConfigError::new(
                        "monitoring requires TLS or explicit loopback/provider_private_link plaintext_scope",
                    ));
                }
            }
        }
        for path in std::iter::once(&mut self.credential_file)
            .chain(self.tls_certificate_file.iter_mut())
            .chain(self.tls_private_key_file.iter_mut())
        {
            validate_bootstrap_file(path, "monitoring credential/TLS file")?;
            *path = std::fs::canonicalize(&*path)
                .map_err(|_| ConfigError::new("cannot resolve monitoring credential/TLS file"))?;
        }
        if self.filesystems.len() > 16
            || self.filesystems.iter().any(|(name, path)| {
                name.is_empty()
                    || name.len() > 64
                    || !name
                        .bytes()
                        .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
                    || !path.is_absolute()
            })
        {
            return Err(ConfigError::new(
                "monitoring.filesystems requires at most 16 named absolute paths (names: 1-64 ASCII letters, digits, '_' or '-')",
            ));
        }
        if self
            .cgroup_v2_directory
            .as_ref()
            .is_some_and(|p| !p.is_absolute())
        {
            return Err(ConfigError::new(
                "monitoring.cgroup_v2_directory must be absolute",
            ));
        }
        for path in self
            .filesystems
            .values_mut()
            .chain(self.cgroup_v2_directory.iter_mut())
        {
            if path
                .components()
                .any(|part| part == std::path::Component::ParentDir)
            {
                return Err(ConfigError::new(
                    "monitoring paths must not contain parent traversal",
                ));
            }
            if let Ok(canonical) = std::fs::canonicalize(&*path) {
                *path = canonical;
            }
        }
        Ok(())
    }

    #[cfg(target_os = "linux")]
    pub(crate) fn relocate_sources(
        &mut self,
        root: &std::path::Path,
        runtime_parent: &std::path::Path,
        protected: &[PathBuf],
    ) -> Result<(), ConfigError> {
        for path in self
            .filesystems
            .values_mut()
            .chain(self.cgroup_v2_directory.iter_mut())
        {
            // Runtime preparation may have created a previously missing target.
            if let Ok(canonical) = std::fs::canonicalize(&*path) {
                *path = canonical;
            }
            if let Ok(relative) = path.strip_prefix(runtime_parent) {
                *path = std::path::Path::new("/state").join(relative);
            } else if protected.iter().any(|hidden| path.starts_with(hidden)) {
                return Err(ConfigError::new(
                    "monitoring targets must not select masked bootstrap paths",
                ));
            } else {
                *path = root.join(
                    path.strip_prefix("/")
                        .expect("validated absolute monitoring path"),
                );
            }
        }
        Ok(())
    }

    fn http(&self) -> HttpConfig {
        HttpConfig {
            bind: self.bind,
            credential_file: self.credential_file.clone(),
            tls_certificate_file: self.tls_certificate_file.clone(),
            tls_private_key_file: self.tls_private_key_file.clone(),
        }
    }
}

/// Owned only by the outer serve invocation. Dropping it stops both loops and
/// all admitted connections; Session workers never start monitoring.
pub(crate) struct Runtime(JoinHandle<()>);

impl Drop for Runtime {
    fn drop(&mut self) {
        self.0.abort();
    }
}

#[derive(Serialize)]
struct Report<'a> {
    schema_version: u8,
    device_id: &'a str,
    generation: &'a str,
    sample_interval_ms: u64,
    sample_age_ms: u128,
    sample: &'a sample::Snapshot,
}

struct State {
    credential: Vec<u8>,
    device_id: String,
    generation: String,
    interval_ms: u64,
    latest: watch::Receiver<Option<Arc<(Instant, sample::Snapshot)>>>,
}

pub(crate) async fn start(config: &Config, generation: u64) -> io::Result<Option<Runtime>> {
    let Some(options) = &config.monitoring else {
        return Ok(None);
    };
    let credential = http::read_credential(&options.credential_file).await?;
    let execution_credential = match &config.transport {
        TransportConfig::Http(http) => Some(&http.credential_file),
        TransportConfig::ReverseWebSocket(ws) => Some(&ws.credential_file),
        TransportConfig::Stdio => None,
    };
    if let Some(path) = execution_credential
        && credential == http::read_credential(path).await?
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "monitoring and execution credentials must differ",
        ));
    }
    let tls = http::build_tls_acceptor(&options.http())?;
    let listener = TcpListener::bind(options.bind).await?;
    let (sender, latest) = watch::channel(None);
    let state = Arc::new(State {
        credential,
        device_id: config.device_id.clone(),
        generation: generation.to_string(),
        interval_ms: options.sample_interval_ms,
        latest,
    });
    let options = options.clone();
    Ok(Some(Runtime(tokio::spawn(async move {
        let sampler = async move {
            let mut collector =
                sample::Collector::new(options.filesystems, options.cgroup_v2_directory);
            let mut interval =
                tokio::time::interval(Duration::from_millis(options.sample_interval_ms));
            interval.set_missed_tick_behavior(MissedTickBehavior::Skip);
            loop {
                interval.tick().await;
                // A single blocking observation may be in flight. HTTP reads the
                // prior snapshot while it runs; polling never schedules more I/O.
                let result = tokio::task::spawn_blocking(move || {
                    let observed_at = Instant::now();
                    let sample = collector.collect();
                    (collector, observed_at, sample)
                })
                .await;
                match result {
                    Ok((next, observed_at, snapshot)) => {
                        collector = next;
                        sender.send_replace(Some(Arc::new((observed_at, snapshot))));
                    }
                    Err(_) => break,
                }
            }
        };
        let server = async move {
            let mut connections = JoinSet::new();
            loop {
                tokio::select! {
                    accepted = listener.accept() => {
                        let Ok((stream, _)) = accepted else { break; };
                        if connections.len() >= MAX_CONNECTIONS { continue; }
                        let state = state.clone();
                        let tls = tls.clone();
                        connections.spawn(async move {
                            let _ = timeout(CONNECTION_TIMEOUT, async move {
                                match tls {
                                    Some(acceptor) => if let Ok(stream) = acceptor.accept(stream).await { serve_connection(stream, state).await; },
                                    None => serve_connection(stream, state).await,
                                }
                            }).await;
                        });
                    }
                    _ = connections.join_next(), if !connections.is_empty() => {}
                }
            }
        };
        tokio::select! { _ = sampler => {}, _ = server => {} }
        eprintln!(
            "{}",
            serde_json::json!({"level":"warning", "event":"a13n-envd.monitoring.stopped"})
        );
    }))))
}

async fn serve_connection<I: AsyncRead + AsyncWrite + Unpin + Send + 'static>(
    stream: I,
    state: Arc<State>,
) {
    let _ = http1::Builder::new()
        .keep_alive(false)
        .max_buf_size(16 * 1024)
        .serve_connection(
            TokioIo::new(stream),
            service_fn(move |request| {
                let response = route(request, &state);
                async move { Ok::<_, Infallible>(response) }
            }),
        )
        .await;
}

fn route(request: Request<Incoming>, state: &State) -> Response<Full<Bytes>> {
    let mut status = StatusCode::OK;
    let mut body = Vec::new();
    if request
        .headers()
        .get_all(header::AUTHORIZATION)
        .iter()
        .count()
        != 1
        || !http::authorized(request.headers(), &state.credential)
    {
        status = StatusCode::UNAUTHORIZED;
    } else if request.uri().path() != REPORT_PATH {
        status = StatusCode::NOT_FOUND;
    } else if request.method() != Method::GET {
        status = StatusCode::METHOD_NOT_ALLOWED;
    } else if request.uri().query().is_some()
        || request.headers().contains_key(header::COOKIE)
        || request.headers().contains_key(header::ORIGIN)
        || request.headers().contains_key(header::TRANSFER_ENCODING)
        || request.headers().contains_key(header::CONTENT_ENCODING)
        || request
            .headers()
            .get(header::CONTENT_LENGTH)
            .is_some_and(|value| value != "0")
    {
        status = StatusCode::BAD_REQUEST;
    } else if let Some(latest) = state.latest.borrow().as_ref() {
        body = serde_json::to_vec(&Report {
            schema_version: 1,
            device_id: &state.device_id,
            generation: &state.generation,
            sample_interval_ms: state.interval_ms,
            sample_age_ms: latest.0.elapsed().as_millis(),
            sample: &latest.1,
        })
        .expect("monitoring report is serializable");
    } else {
        status = StatusCode::SERVICE_UNAVAILABLE;
    }
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::CACHE_CONTROL, "no-store")
        .header(header::CONNECTION, "close")
        .body(Full::new(Bytes::from(body)))
        .expect("static monitoring response is valid")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn disabled_monitoring_starts_no_runtime() {
        assert!(
            start(&Config::for_test("test-device"), 1)
                .await
                .unwrap()
                .is_none()
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn managed_sources_use_original_tree_without_relocating_credentials() {
        let mut options: Options = serde_json::from_value(serde_json::json!({
            "bind":"127.0.0.1:9090", "credential_file":"/credentials/monitoring",
            "filesystems":{"workspace":"/workspace", "root":"/", "runtime":"/envd-runtime", "spool":"/envd-runtime/generation-test/spool"}, "cgroup_v2_directory":"/sys/fs/cgroup"
        })).unwrap();
        options
            .relocate_sources(
                std::path::Path::new("/original"),
                std::path::Path::new("/envd-runtime"),
                &[PathBuf::from("/envd-runtime")],
            )
            .unwrap();
        assert_eq!(options.filesystems["runtime"], PathBuf::from("/state"));
        assert_eq!(
            options.filesystems["spool"],
            PathBuf::from("/state/generation-test/spool")
        );
        assert_eq!(
            options.filesystems["workspace"],
            PathBuf::from("/original/workspace")
        );
        assert_eq!(options.filesystems["root"], PathBuf::from("/original"));
        assert_eq!(
            options.cgroup_v2_directory,
            Some(PathBuf::from("/original/sys/fs/cgroup"))
        );
        assert_eq!(
            options.credential_file,
            PathBuf::from("/credentials/monitoring")
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn managed_sources_reject_other_masked_files_and_directories() {
        for target in [
            "/private-config",
            "/home/example/.a13n",
            "/home/example/.a13n/state",
        ] {
            let mut options: Options = serde_json::from_value(serde_json::json!({
                "filesystems":{"target":target}
            }))
            .unwrap();
            assert!(
                options
                    .relocate_sources(
                        std::path::Path::new("/original"),
                        std::path::Path::new("/envd-runtime"),
                        &[
                            PathBuf::from("/private-config"),
                            PathBuf::from("/home/example/.a13n")
                        ],
                    )
                    .unwrap_err()
                    .to_string()
                    .contains("masked bootstrap paths")
            );
        }
    }

    #[cfg(not(target_os = "linux"))]
    #[test]
    fn unsupported_platform_rejects_monitoring() {
        let mut options: Options = serde_json::from_value(serde_json::json!({})).unwrap();
        assert!(
            options
                .validate()
                .unwrap_err()
                .to_string()
                .contains("requires Linux")
        );
    }
}
