//! Real daemon subprocesses and OTLP wire decoding; no process-global env mutation.
use std::{convert::Infallible, fs, path::PathBuf, process::Stdio, sync::Arc, time::Duration};

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::{Request, Response, body::Incoming, service::service_fn};
use hyper_util::rt::TokioIo;
use opentelemetry_proto::tonic::{
    collector::metrics::v1::ExportMetricsServiceRequest,
    common::v1::{KeyValue, any_value::Value},
    metrics::v1::{Metric, metric::Data},
};
use prost::Message;
use serde_json::json;
use tokio::{net::TcpListener, process::Command, sync::mpsc, task::JoinHandle, time::timeout};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut random = [0; 16];
        getrandom::fill(&mut random).unwrap();
        let root =
            std::env::temp_dir().join(format!("a13n-otlp-{:032x}", u128::from_ne_bytes(random)));
        fs::create_dir(&root).unwrap();
        Self(root)
    }
    fn command(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        command.env_clear();
        // Windows needs its trusted system directory for ordinary native APIs.
        if let Some(value) = std::env::var_os("SystemRoot") {
            command.env("SystemRoot", value);
        }
        command
            .env("A13N_ENVD_RUNTIME_DIR", self.0.join("runtime"))
            .env(
                "A13N_ENVD_CONFIG_JSON",
                json!({
                    "device_id":"otlp-device", "default_working_directory":self.0,
                    "monitoring":{"sample_interval_ms":1000}
                })
                .to_string(),
            )
            .env("OTEL_METRICS_EXPORTER", "otlp")
            .env("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "http/protobuf")
            .env("OTEL_METRIC_EXPORT_INTERVAL", "100")
            .env("OTEL_EXPORTER_OTLP_METRICS_TIMEOUT", "500")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        command
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

trait AsyncStream: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send {}
impl<T: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send> AsyncStream for T {}

struct Received {
    path: String,
    headers: hyper::HeaderMap,
    metrics: ExportMetricsServiceRequest,
}
struct Receiver {
    endpoint: String,
    received: mpsc::Receiver<Received>,
    task: JoinHandle<()>,
}
impl Receiver {
    async fn start() -> Self {
        Self::http(None).await
    }
    async fn http(tls: Option<rustls::ServerConfig>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!(
            "{}://{}",
            if tls.is_some() { "https" } else { "http" },
            listener.local_addr().unwrap()
        );
        let acceptor = tls.map(|config| tokio_rustls::TlsAcceptor::from(Arc::new(config)));
        let (send, received) = mpsc::channel(64);
        let task = tokio::spawn(async move {
            loop {
                let (stream, _) = listener.accept().await.unwrap();
                let send = send.clone();
                let acceptor = acceptor.clone();
                tokio::spawn(async move {
                    let stream: Box<dyn AsyncStream> = if let Some(acceptor) = acceptor {
                        let stream = match acceptor.accept(stream).await {
                            Ok(stream) => stream,
                            Err(error) => {
                                eprintln!("OTLP test receiver TLS handshake failed: {error}");
                                return;
                            }
                        };
                        Box::new(stream)
                    } else {
                        Box::new(stream)
                    };
                    let service = service_fn(move |request: Request<Incoming>| {
                        let send = send.clone();
                        async move {
                            let (parts, body) = request.into_parts();
                            let body = body.collect().await.unwrap().to_bytes();
                            let metrics = ExportMetricsServiceRequest::decode(body).unwrap();
                            let _ = send
                                .send(Received {
                                    path: parts.uri.path().to_owned(),
                                    headers: parts.headers,
                                    metrics,
                                })
                                .await;
                            Ok::<_, Infallible>(Response::new(Full::new(Bytes::new())))
                        }
                    });
                    let _ = hyper::server::conn::http1::Builder::new()
                        .serve_connection(TokioIo::new(stream), service)
                        .await;
                });
            }
        });
        Self {
            endpoint,
            received,
            task,
        }
    }
    async fn sample(&mut self) -> Received {
        timeout(Duration::from_secs(10), async {
            loop {
                let received = self.received.recv().await.unwrap();
                if find_metric(&received.metrics, "envd_system_memory_total_bytes").is_some() {
                    break received;
                }
            }
        })
        .await
        .expect("daemon did not deliver native OTLP metrics")
    }
}
impl Drop for Receiver {
    fn drop(&mut self) {
        self.task.abort();
    }
}
fn find_metric<'a>(request: &'a ExportMetricsServiceRequest, name: &str) -> Option<&'a Metric> {
    request
        .resource_metrics
        .iter()
        .flat_map(|r| &r.scope_metrics)
        .flat_map(|s| &s.metrics)
        .find(|m| m.name == name)
}
fn attribute<'a>(attributes: &'a [KeyValue], name: &str) -> Option<&'a str> {
    attributes.iter().find(|kv| kv.key == name).and_then(|kv| {
        match kv.value.as_ref()?.value.as_ref()? {
            Value::StringValue(value) => Some(value.as_str()),
            _ => None,
        }
    })
}

#[tokio::test]
async fn native_http_exports_cached_metrics_with_standard_identity_and_endpoint_precedence() {
    for signal_specific in [false, true] {
        let fixture = Fixture::new();
        let mut receiver = Receiver::start().await;
        let mut command = fixture.command();
        command
            .env(
                "OTEL_EXPORTER_OTLP_ENDPOINT",
                format!("{}/collector", receiver.endpoint),
            )
            .env(
                "OTEL_EXPORTER_OTLP_HEADERS",
                "authorization=Bearer%20generic",
            )
            .env("OTEL_SERVICE_NAME", "configured-envd")
            .env(
                "OTEL_RESOURCE_ATTRIBUTES",
                "workspace.id=test-workspace,service.instance.id=untrusted",
            );
        if signal_specific {
            command
                .env(
                    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
                    format!("{}/custom-metrics", receiver.endpoint),
                )
                .env(
                    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
                    "authorization=Bearer%20metrics",
                );
        }
        let mut child = command.spawn().unwrap();
        let received = receiver.sample().await;
        assert_eq!(
            received.path,
            if signal_specific {
                "/custom-metrics"
            } else {
                "/collector/v1/metrics"
            }
        );
        assert_eq!(received.headers["content-type"], "application/x-protobuf");
        assert_eq!(
            received.headers["authorization"],
            if signal_specific {
                "Bearer metrics"
            } else {
                "Bearer generic"
            }
        );
        let resource = received.metrics.resource_metrics[0]
            .resource
            .as_ref()
            .unwrap();
        assert_eq!(
            attribute(&resource.attributes, "service.name"),
            Some("configured-envd")
        );
        assert_eq!(
            attribute(&resource.attributes, "service.instance.id"),
            Some("otlp-device")
        );
        assert_eq!(
            attribute(&resource.attributes, "workspace.id"),
            Some("test-workspace")
        );
        let metric = find_metric(&received.metrics, "envd_system_memory_total_bytes").unwrap();
        let Some(Data::Gauge(gauge)) = &metric.data else {
            panic!("memory must be a gauge")
        };
        assert_eq!(
            attribute(&gauge.data_points[0].attributes, "device_id"),
            Some("otlp-device")
        );
        assert!(
            attribute(&gauge.data_points[0].attributes, "generation")
                .unwrap()
                .parse::<u64>()
                .is_ok()
        );
        drop(child.stdin.take());
        let output = timeout(Duration::from_secs(7), child.wait_with_output())
            .await
            .unwrap()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[tokio::test]
async fn invalid_exporter_selection_fails_without_printing_secret_values() {
    let fixture = Fixture::new();
    let output = fixture
        .command()
        .env("OTEL_METRICS_EXPORTER", "secret-selection")
        .output()
        .await
        .unwrap();
    assert!(!output.status.success());
    let error = String::from_utf8_lossy(&output.stderr);
    assert!(error.contains("OTEL_METRICS_EXPORTER"));
    assert!(!error.contains("secret-selection"));
}

struct Certificates {
    ca: rcgen::CertifiedIssuer<'static, rcgen::KeyPair>,
    server: rcgen::Certificate,
    server_key: rcgen::KeyPair,
    client: rcgen::Certificate,
    client_key: rcgen::KeyPair,
}
impl Certificates {
    fn new() -> Self {
        use rcgen::{
            BasicConstraints, CertificateParams, CertifiedIssuer, IsCa, KeyPair, KeyUsagePurpose,
        };
        let mut params = CertificateParams::default();
        // Give each certificate a distinct subject: platform chain builders must
        // distinguish the leaf from its issuer instead of treating it as self-issued.
        params
            .distinguished_name
            .push(rcgen::DnType::CommonName, "OTLP test CA");
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![
            KeyUsagePurpose::KeyCertSign,
            KeyUsagePurpose::DigitalSignature,
        ];
        let ca = CertifiedIssuer::self_signed(params, KeyPair::generate().unwrap()).unwrap();
        let server_key = KeyPair::generate().unwrap();
        let mut server =
            CertificateParams::new(vec!["localhost".into(), "127.0.0.1".into()]).unwrap();
        server
            .distinguished_name
            .push(rcgen::DnType::CommonName, "localhost");
        let server = server.signed_by(&server_key, &ca).unwrap();
        let client_key = KeyPair::generate().unwrap();
        let mut client = CertificateParams::new(vec!["envd-client".into()]).unwrap();
        client
            .distinguished_name
            .push(rcgen::DnType::CommonName, "envd-client");
        let client = client.signed_by(&client_key, &ca).unwrap();
        Self {
            ca,
            server,
            server_key,
            client,
            client_key,
        }
    }
    fn server_config(&self, mutual: bool) -> rustls::ServerConfig {
        let builder = rustls::ServerConfig::builder_with_provider(Arc::new(
            rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()
        .unwrap();
        let builder = if mutual {
            let mut roots = rustls::RootCertStore::empty();
            roots.add(self.ca.der().clone()).unwrap();
            let verifier = rustls::server::WebPkiClientVerifier::builder_with_provider(
                Arc::new(roots),
                Arc::new(rustls::crypto::ring::default_provider()),
            )
            .build()
            .unwrap();
            builder.with_client_cert_verifier(verifier)
        } else {
            builder.with_no_client_auth()
        };
        builder
            .with_single_cert(
                vec![self.server.der().clone()],
                rustls::pki_types::PrivatePkcs8KeyDer::from(self.server_key.serialize_der()).into(),
            )
            .unwrap()
    }
    fn configure(&self, fixture: &Fixture, command: &mut Command, mutual: bool, specific: bool) {
        let prefix = if specific {
            "OTEL_EXPORTER_OTLP_METRICS"
        } else {
            "OTEL_EXPORTER_OTLP"
        };
        for (suffix, value) in [
            ("CERTIFICATE", self.ca.pem()),
            ("CLIENT_CERTIFICATE", self.client.pem()),
            ("CLIENT_KEY", self.client_key.serialize_pem()),
        ] {
            if suffix != "CERTIFICATE" && !mutual {
                continue;
            }
            let path = fixture.0.join(format!("{suffix}.pem"));
            fs::write(&path, value).unwrap();
            command.env(format!("{prefix}_{suffix}"), path);
            if specific {
                command.env(
                    format!("OTEL_EXPORTER_OTLP_{suffix}"),
                    fixture.0.join("unused-secret-path"),
                );
            }
        }
    }
}

async fn stop(mut child: tokio::process::Child) {
    drop(child.stdin.take());
    let output = timeout(Duration::from_secs(7), child.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[tokio::test]
async fn native_https_verifies_ca_and_supports_mutual_tls_with_signal_precedence() {
    for (mutual, specific) in [(false, false), (true, true)] {
        let fixture = Fixture::new();
        let certificates = Certificates::new();
        let mut receiver = Receiver::http(Some(certificates.server_config(mutual))).await;
        let mut command = fixture.command();
        command.env("OTEL_EXPORTER_OTLP_ENDPOINT", &receiver.endpoint);
        certificates.configure(&fixture, &mut command, mutual, specific);
        let child = command.spawn().unwrap();
        let received = receiver.sample().await;
        assert_eq!(received.path, "/v1/metrics");
        stop(child).await;
    }
}

#[tokio::test]
async fn native_https_rejects_untrusted_server_without_stopping_daemon() {
    let fixture = Fixture::new();
    let certificates = Certificates::new();
    let mut receiver = Receiver::http(Some(certificates.server_config(false))).await;
    let mut command = fixture.command();
    command.env("OTEL_EXPORTER_OTLP_ENDPOINT", &receiver.endpoint);
    let mut child = command.spawn().unwrap();
    assert!(
        timeout(Duration::from_secs(2), receiver.received.recv())
            .await
            .is_err()
    );
    assert!(child.try_wait().unwrap().is_none());
    stop(child).await;
}

#[tokio::test]
async fn invalid_tls_configuration_fails_without_disclosing_paths_or_key_material() {
    for partial_identity in [false, true] {
        let fixture = Fixture::new();
        let path = fixture.0.join("private-secret-name.pem");
        fs::write(&path, "private-secret-material").unwrap();
        let output = fixture
            .command()
            .env(
                if partial_identity {
                    "OTEL_EXPORTER_OTLP_CLIENT_KEY"
                } else {
                    "OTEL_EXPORTER_OTLP_CERTIFICATE"
                },
                &path,
            )
            .output()
            .await
            .unwrap();
        assert!(!output.status.success());
        let error = String::from_utf8_lossy(&output.stderr);
        assert!(error.contains("OTLP"));
        assert!(!error.contains("private-secret"));
    }
}

struct GrpcReceiver(mpsc::Sender<Received>);
#[tonic::async_trait]
impl opentelemetry_proto::tonic::collector::metrics::v1::metrics_service_server::MetricsService
    for GrpcReceiver
{
    async fn export(
        &self,
        request: tonic::Request<ExportMetricsServiceRequest>,
    ) -> Result<
        tonic::Response<
            opentelemetry_proto::tonic::collector::metrics::v1::ExportMetricsServiceResponse,
        >,
        tonic::Status,
    > {
        let (metadata, _, metrics) = request.into_parts();
        let _ = self
            .0
            .send(Received {
                path: String::new(),
                headers: metadata.into_headers(),
                metrics,
            })
            .await;
        Ok(tonic::Response::new(Default::default()))
    }
}
impl Receiver {
    async fn grpc(certificates: Option<&Certificates>, mutual: bool) -> Self {
        use opentelemetry_proto::tonic::collector::metrics::v1::metrics_service_server::MetricsServiceServer;
        use tonic::transport::{Certificate, Identity, Server, ServerTlsConfig};
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!(
            "{}://{}",
            if certificates.is_some() {
                "https"
            } else {
                "http"
            },
            listener.local_addr().unwrap()
        );
        let (send, received) = mpsc::channel(64);
        let mut server = Server::builder();
        if let Some(certificates) = certificates {
            let mut tls = ServerTlsConfig::new().identity(Identity::from_pem(
                certificates.server.pem(),
                certificates.server_key.serialize_pem(),
            ));
            if mutual {
                tls = tls.client_ca_root(Certificate::from_pem(certificates.ca.pem()));
            }
            server = server.tls_config(tls).unwrap();
        }
        let task = tokio::spawn(async move {
            server
                .add_service(MetricsServiceServer::new(GrpcReceiver(send)))
                .serve_with_incoming(tokio_stream::wrappers::TcpListenerStream::new(listener))
                .await
                .unwrap();
        });
        Self {
            endpoint,
            received,
            task,
        }
    }
}

#[tokio::test]
async fn native_grpc_exports_over_plaintext_and_mutual_tls() {
    for mutual in [false, true] {
        let fixture = Fixture::new();
        let certificates = Certificates::new();
        let mut receiver = Receiver::grpc(mutual.then_some(&certificates), mutual).await;
        let mut command = fixture.command();
        command
            .env("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "grpc")
            .env("OTEL_EXPORTER_OTLP_ENDPOINT", &receiver.endpoint)
            .env("OTEL_EXPORTER_OTLP_HEADERS", "x-collector-token=grpc-test");
        if mutual {
            certificates.configure(&fixture, &mut command, true, true);
        }
        let child = command.spawn().unwrap();
        let received = receiver.sample().await;
        assert_eq!(received.headers["x-collector-token"], "grpc-test");
        stop(child).await;
    }
}

async fn call(
    child: &mut tokio::process::Child,
    method: &str,
    params: serde_json::Value,
) -> serde_json::Value {
    session_call(child, None, method, params).await
}

async fn session_call(
    child: &mut tokio::process::Child,
    session: Option<&str>,
    method: &str,
    params: serde_json::Value,
) -> serde_json::Value {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    timeout(Duration::from_secs(10), async {
        let mut request = json!({"jsonrpc":"2.0", "id":1, "method":method, "params":params});
        if let Some(session) = session {
            request["eip_session"] = json!(session);
        }
        let body = request.to_string();
        let frame = format!(
            "Content-Length: {}\r\nContent-Type: application/json\r\n\r\n{body}",
            body.len()
        );
        child
            .stdin
            .as_mut()
            .unwrap()
            .write_all(frame.as_bytes())
            .await
            .unwrap();
        let stdout = child.stdout.as_mut().unwrap();
        let mut header = Vec::new();
        while !header.ends_with(b"\r\n\r\n") {
            header.push(stdout.read_u8().await.unwrap());
        }
        let header = String::from_utf8(header).unwrap();
        let length: usize = header
            .lines()
            .find_map(|line| line.strip_prefix("Content-Length: "))
            .unwrap()
            .parse()
            .unwrap();
        let mut body = vec![0; length];
        stdout.read_exact(&mut body).await.unwrap();
        let response: serde_json::Value = serde_json::from_slice(&body).unwrap();
        assert!(response.get("error").is_none(), "{response}");
        response["result"].clone()
    })
    .await
    .expect("EIP blocked by metrics export")
}

async fn initialize(child: &mut tokio::process::Child) -> serde_json::Value {
    call(child, "initialize", json!({"supported_protocol_versions":["0.3"], "expected_device_id":"otlp-device", "client":{"name":"otlp-test", "version":"1"}})).await["descriptor"].clone()
}

#[tokio::test]
async fn exporter_opt_in_and_explicit_monitoring_disable_are_preserved() {
    for mode in [
        "default", "endpoint", "exporter", "none", "disabled", "null",
    ] {
        let fixture = Fixture::new();
        let mut receiver = Receiver::start().await;
        let mut command = fixture.command();
        let mut config = json!({"device_id":"otlp-device", "default_working_directory":fixture.0});
        if mode == "null" {
            config["monitoring"] = serde_json::Value::Null;
        }
        command
            .env("A13N_ENVD_CONFIG_JSON", config.to_string())
            .stdout(Stdio::piped());
        if mode != "exporter" {
            command.env_remove("OTEL_METRICS_EXPORTER");
        }
        if mode != "default" {
            command.env("OTEL_EXPORTER_OTLP_ENDPOINT", &receiver.endpoint);
        }
        if mode == "none" {
            command.env("OTEL_METRICS_EXPORTER", "none");
        }
        if mode == "disabled" {
            command.env("OTEL_SDK_DISABLED", "true");
        }
        let mut child = command.spawn().unwrap();
        let descriptor = initialize(&mut child).await;
        let enabled = ["endpoint", "exporter"].contains(&mode);
        assert_eq!(
            descriptor["available_methods"]
                .as_array()
                .unwrap()
                .contains(&json!("device.metrics")),
            enabled,
            "{mode}: {descriptor}"
        );
        if enabled {
            receiver.sample().await;
        } else {
            assert!(
                timeout(Duration::from_millis(250), receiver.received.recv())
                    .await
                    .is_err()
            );
        }
        stop(child).await;
    }
}

#[tokio::test]
async fn stalled_export_does_not_block_eip_sampling_or_bounded_shutdown() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut command = fixture.command();
    command
        .env(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            format!("http://{}", listener.local_addr().unwrap()),
        )
        .env("OTEL_EXPORTER_OTLP_METRICS_TIMEOUT", "60000")
        .stdout(Stdio::piped());
    let mut child = command.spawn().unwrap();
    let descriptor = initialize(&mut child).await;
    // Keep the receiver connected without responding, beyond the shutdown budget.
    let (_connection, _) = timeout(Duration::from_secs(5), listener.accept())
        .await
        .unwrap()
        .unwrap();
    let params =
        json!({"expected_device_id":"otlp-device", "expected_generation":descriptor["generation"]});
    let first = call(&mut child, "device.metrics", params.clone()).await;
    let first_time = first["sample"]["sampled_at"].clone();
    timeout(Duration::from_secs(4), async {
        loop {
            let sample = call(&mut child, "device.metrics", params.clone()).await;
            if sample["sample"]["sampled_at"] != first_time {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .expect("sampler stalled with exporter");
    stop(child).await;
}

/// Run inside the disposable managed-monitoring CI container, never on the host.
#[cfg(target_os = "linux")]
#[tokio::test]
#[ignore = "requires root and mount/PID/network namespace permissions in a disposable container"]
async fn managed_mtls_export_keeps_credentials_out_of_sessions() {
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(unsafe { libc::geteuid() }, 0);
    for specific in [false, true] {
        let fixture = Fixture::new();
        fs::set_permissions(&fixture.0, fs::Permissions::from_mode(0o755)).unwrap();
        let workspace = fixture.0.join("workspace");
        fs::create_dir(&workspace).unwrap();
        // Only this generated disposable directory is writable by the payload.
        fs::set_permissions(&workspace, fs::Permissions::from_mode(0o777)).unwrap();
        let certificates = Certificates::new();
        let mut receiver = Receiver::http(Some(certificates.server_config(true))).await;
        let mut command = fixture.command();
        command
            .env(
                "A13N_ENVD_CONFIG_JSON",
                json!({
                    "device_id":"otlp-device", "default_working_directory":workspace,
                    "execution":{"uid":65534,"gid":65534}, "full_control":true,
                    "sandbox":{"mode":"disabled"}, "egress":{"mode":"deny"},
                    "monitoring":{"sample_interval_ms":1000}
                })
                .to_string(),
            )
            .env("PATH", "/usr/bin:/bin")
            .env("OTEL_EXPORTER_OTLP_ENDPOINT", &receiver.endpoint)
            .env(
                "OTEL_EXPORTER_OTLP_HEADERS",
                "x-collector-token=private-export-token",
            )
            .stdout(Stdio::piped());
        certificates.configure(&fixture, &mut command, true, specific);
        for name in ["CERTIFICATE", "CLIENT_CERTIFICATE", "CLIENT_KEY"] {
            fs::set_permissions(
                fixture.0.join(format!("{name}.pem")),
                fs::Permissions::from_mode(0o644),
            )
            .unwrap();
        }
        let mut child = command.spawn().unwrap();
        let descriptor = initialize(&mut child).await;
        let received = receiver.sample().await;
        assert_eq!(
            received.headers["x-collector-token"],
            "private-export-token"
        );
        let opened = call(
            &mut child,
            "session.open",
            json!({
                "expected_device_id":"otlp-device", "expected_generation":descriptor["generation"],
                "protocol_version":"0.3", "working_directory":workspace
            }),
        )
        .await;
        let session = opened["descriptor"]["session_id"].as_str().unwrap();
        let profile = &opened["descriptor"]["shell_profiles"][0]["profile_id"];
        let ready = session_call(
            &mut child,
            Some(session),
            "environment.readiness",
            json!({"context":{"operation_id":"op-ready"}}),
        )
        .await;
        assert_eq!(ready["ready"], true, "{ready}");
        let script = format!(
            "set -eu; test -z \"$(env | grep '^OTEL_' || true)\"; test ! -e /bootstrap.json; test ! -e /credentials; for f in '{}/CERTIFICATE.pem' '{}/CLIENT_CERTIFICATE.pem' '{}/CLIENT_KEY.pem'; do test -z \"$(cat \"$f\")\"; done",
            fixture.0.display(),
            fixture.0.display(),
            fixture.0.display()
        );
        let result = session_call(
            &mut child,
            Some(session),
            "shell.exec",
            json!({
                "context":{"operation_id":"op-otlp-isolation"},
                "request":{"command":{"kind":"shell","profile_id":profile,"script":script}}
            }),
        )
        .await;
        assert_eq!(result["status"]["exit_code"], 0, "{result}");
        session_call(&mut child, Some(session), "session.close", json!({})).await;
        // Drain prior exports, then require a subsequent export without a Session.
        while receiver.received.try_recv().is_ok() {}
        receiver.sample().await;
        stop(child).await;
    }
}

#[tokio::test]
async fn grpc_tls_uses_native_roots_without_otlp_certificate_override() {
    let fixture = Fixture::new();
    let certificates = Certificates::new();
    let mut receiver = Receiver::grpc(Some(&certificates), false).await;
    let roots = fixture.0.join("native-roots.pem");
    fs::write(&roots, certificates.ca.pem()).unwrap();
    let child = fixture
        .command()
        .env("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "grpc")
        .env("OTEL_EXPORTER_OTLP_ENDPOINT", &receiver.endpoint)
        // rustls-native-certs' supported root-store override, not an OTLP CA setting.
        .env("SSL_CERT_FILE", roots)
        .spawn()
        .unwrap();
    receiver.sample().await;
    stop(child).await;
}

#[cfg(unix)]
#[tokio::test]
async fn full_control_does_not_inherit_daemon_exporter_settings() {
    let fixture = Fixture::new();
    let mut receiver = Receiver::start().await;
    let mut child = fixture
        .command()
        .env("A13N_ENVD_FULL_CONTROL", "1")
        .env("PATH", "/usr/bin:/bin")
        .env("OTEL_EXPORTER_OTLP_ENDPOINT", &receiver.endpoint)
        .env(
            "OTEL_EXPORTER_OTLP_HEADERS",
            "x-collector-token=private-export-token",
        )
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let descriptor = initialize(&mut child).await;
    receiver.sample().await;
    let opened = call(
        &mut child,
        "session.open",
        json!({
            "expected_device_id":"otlp-device", "expected_generation":descriptor["generation"],
            "protocol_version":"0.3", "working_directory":fixture.0
        }),
    )
    .await;
    let session = opened["descriptor"]["session_id"].as_str().unwrap();
    let ready = session_call(
        &mut child,
        Some(session),
        "environment.readiness",
        json!({"context":{"operation_id":"op-ready"}}),
    )
    .await;
    assert_eq!(ready["ready"], true);
    let result = session_call(&mut child, Some(session), "shell.exec", json!({
        "context":{"operation_id":"op-env"}, "request":{"command":{
            "kind":"shell", "profile_id":opened["descriptor"]["shell_profiles"][0]["profile_id"],
            "script":"test -z \"$(env | grep '^OTEL_' || true)\""
        }}
    })).await;
    assert_eq!(result["status"]["exit_code"], 0, "{result}");
    stop(child).await;
}
