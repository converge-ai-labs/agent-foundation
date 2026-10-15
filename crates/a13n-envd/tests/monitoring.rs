#![cfg(target_os = "linux")]

use serde_json::{Value, json};
use std::{fs, net::SocketAddr, path::PathBuf, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    process::{Child, Command},
    time::{sleep, timeout},
};

const TOKEN: &str = "monitoring-test-only";
const EXECUTION_TOKEN: &str = "execution-test-only";
const REPORT: &str = "/monitoring/v1/report";

struct Fixture {
    root: PathBuf,
    address: SocketAddr,
}

impl Fixture {
    fn new() -> Self {
        let mut random = [0; 16];
        getrandom::fill(&mut random).unwrap();
        let root = std::env::temp_dir().join(format!(
            "a13n-monitoring-{:032x}",
            u128::from_ne_bytes(random)
        ));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("monitoring-credential"), TOKEN).unwrap();
        fs::write(root.join("execution-credential"), EXECUTION_TOKEN).unwrap();
        use std::os::unix::fs::PermissionsExt;
        for name in ["monitoring-credential", "execution-credential"] {
            fs::set_permissions(root.join(name), fs::Permissions::from_mode(0o600)).unwrap();
        }
        Self {
            root,
            address: free_address(),
        }
    }
    fn options(&self) -> Value {
        json!({"bind": self.address, "credential_file": self.root.join("monitoring-credential"), "plaintext_scope":"loopback", "sample_interval_ms":1000, "filesystems":{"workspace":self.root, "missing":self.root.join("missing")}})
    }
    fn command(&self, monitoring: Value) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        command.env_clear().env("A13N_ENVD_RUNTIME_DIR", self.root.join("runtime"))
            .env("A13N_ENVD_CONFIG_JSON", json!({"device_id":"metrics-device", "default_working_directory":self.root, "monitoring":monitoring}).to_string())
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
        command
    }
    async fn ready(&self, child: &mut Child) -> Value {
        timeout(Duration::from_secs(10), async {
            loop {
                assert!(
                    child.try_wait().unwrap().is_none(),
                    "daemon exited before monitoring became ready"
                );
                if let Ok(stream) = TcpStream::connect(self.address).await {
                    let (status, body) = request(stream, "GET", REPORT, Some(TOKEN), "").await;
                    if status == 200 {
                        return serde_json::from_slice(&body).unwrap();
                    }
                    assert_eq!(status, 503);
                }
                sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap()
    }
    async fn reject(&self, options: Value, expected: &str) {
        let output = timeout(Duration::from_secs(10), self.command(options).output())
            .await
            .unwrap()
            .unwrap();
        assert!(!output.status.success());
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains(expected), "{stderr}");
        assert!(!stderr.contains(TOKEN));
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}
fn free_address() -> SocketAddr {
    std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
}

async fn request<I: AsyncRead + AsyncWrite + Unpin>(
    mut stream: I,
    method: &str,
    path: &str,
    token: Option<&str>,
    extra: &str,
) -> (u16, Vec<u8>) {
    timeout(Duration::from_secs(8), async {
        let authorization = token.map(|s| format!("Authorization: Bearer {s}\r\n")).unwrap_or_default();
        stream.write_all(format!("{method} {path} HTTP/1.1\r\nHost: localhost\r\n{authorization}Connection: close\r\n{extra}\r\n").as_bytes()).await.unwrap();
        let mut bytes = Vec::new(); stream.read_to_end(&mut bytes).await.unwrap();
        let boundary = bytes.windows(4).position(|s| s == b"\r\n\r\n").unwrap();
        let header = std::str::from_utf8(&bytes[..boundary]).unwrap();
        assert!(header.to_ascii_lowercase().contains("cache-control: no-store"));
        (header.split_whitespace().nth(1).unwrap().parse().unwrap(), bytes[boundary+4..].to_vec())
    }).await.unwrap()
}
async fn get(
    address: SocketAddr,
    method: &str,
    path: &str,
    token: Option<&str>,
    extra: &str,
) -> (u16, Vec<u8>) {
    request(
        TcpStream::connect(address).await.unwrap(),
        method,
        path,
        token,
        extra,
    )
    .await
}
async fn eof(mut child: Child) {
    drop(child.stdin.take());
    let output = timeout(Duration::from_secs(5), child.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stdout.is_empty(), "monitoring wrote to EIP stdout");
}

#[tokio::test]
async fn stdio_samples_without_sessions_and_eof_stops_listener() {
    let fixture = Fixture::new();
    let mut child = fixture.command(fixture.options()).spawn().unwrap();
    let first = fixture.ready(&mut child).await;
    assert_eq!(first["schema_version"], 1);
    assert_eq!(first["device_id"], "metrics-device");
    assert!(
        first["generation"]
            .as_str()
            .unwrap()
            .parse::<u64>()
            .unwrap()
            > 0
    );
    assert!(
        first["sample"]["system"]["memory_total_bytes"]
            .as_u64()
            .unwrap()
            > 0
    );
    assert!(
        first["sample"]["filesystems"]["workspace"]["total_bytes"]
            .as_u64()
            .unwrap()
            > 0
    );
    for field in ["total_inodes", "free_inodes", "available_inodes"] {
        assert!(first["sample"]["filesystems"]["workspace"][field].is_u64());
    }
    let cpu = first["sample"]["system"]["cpu_time_seconds"]
        .as_object()
        .unwrap();
    assert_eq!(cpu.len(), 8);
    assert!(
        cpu.values()
            .all(|value| value.as_f64().is_some_and(|time| time >= 0.0))
    );
    assert!(first["sample"]["system"]["pressure"]["cpu"]["full"].is_null());
    assert!(first["sample"]["filesystems"]["missing"].is_null());
    assert!(first["sample"]["cgroup_v2"].is_null());
    assert!(!first.to_string().contains(fixture.root.to_str().unwrap()));
    let sequence = first["sample"]["sequence"].as_u64().unwrap();
    timeout(Duration::from_secs(4), async {
        loop {
            let (_, body) = get(fixture.address, "GET", REPORT, Some(TOKEN), "").await;
            let report: Value = serde_json::from_slice(&body).unwrap();
            if report["sample"]["sequence"].as_u64().unwrap() > sequence {
                assert!(
                    report["sample"]["system"]["cpu_busy_percent"]
                        .as_f64()
                        .is_some()
                );
                break;
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    // An idle client must not extend stdio's owned process lifetime.
    let _idle = TcpStream::connect(fixture.address).await.unwrap();
    eof(child).await;
    assert!(TcpStream::connect(fixture.address).await.is_err());
}

#[tokio::test]
async fn monitoring_requires_its_own_credential_and_only_serves_bodyless_get() {
    let fixture = Fixture::new();
    let mut child = fixture.command(fixture.options()).spawn().unwrap();
    fixture.ready(&mut child).await;
    for (method, path, token, extra, expected) in [
        ("GET", REPORT, None, "", 401),
        ("GET", REPORT, Some(EXECUTION_TOKEN), "", 401),
        ("POST", REPORT, Some(TOKEN), "", 405),
        ("GET", "/eip/control", Some(TOKEN), "", 404),
        ("GET", "/monitoring/v1/report?path=/", Some(TOKEN), "", 400),
        ("GET", REPORT, Some(TOKEN), "Cookie: x=y\r\n", 400),
        (
            "GET",
            REPORT,
            Some(TOKEN),
            "Origin: https://example.com\r\n",
            400,
        ),
        ("GET", REPORT, Some(TOKEN), "Content-Length: 1\r\n", 400),
        (
            "GET",
            REPORT,
            Some(TOKEN),
            "Authorization: Bearer duplicate\r\n",
            401,
        ),
    ] {
        assert_eq!(
            get(fixture.address, method, path, token, extra).await.0,
            expected
        );
    }
    eof(child).await;
}

#[tokio::test]
async fn http_and_monitoring_credentials_do_not_cross_authorize() {
    let fixture = Fixture::new();
    let execution = free_address();
    let mut command = fixture.command(fixture.options());
    command
        .env("A13N_ENVD_TRANSPORT", "http")
        .env("A13N_ENVD_HTTP_BIND", execution.to_string())
        .env(
            "A13N_ENVD_HTTP_CREDENTIAL_FILE",
            fixture.root.join("execution-credential"),
        )
        .env("A13N_ENVD_HTTP_PLAINTEXT_SCOPE", "loopback");
    let mut child = command.spawn().unwrap();
    fixture.ready(&mut child).await;
    assert_eq!(
        get(
            execution,
            "POST",
            "/eip/control",
            Some(TOKEN),
            "Content-Length: 0\r\n"
        )
        .await
        .0,
        401
    );
    assert_eq!(
        get(fixture.address, "GET", REPORT, Some(EXECUTION_TOKEN), "")
            .await
            .0,
        401
    );
    unsafe {
        libc::kill(child.id().unwrap() as i32, libc::SIGTERM);
    }
    assert!(
        timeout(Duration::from_secs(5), child.wait())
            .await
            .unwrap()
            .unwrap()
            .success()
    );
    // Equal contents in different files also fail, not just equal paths.
    fs::write(fixture.root.join("monitoring-credential"), EXECUTION_TOKEN).unwrap();
    let output = command.output().await.unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("credentials must differ"));
}

#[tokio::test]
async fn reverse_websocket_can_expose_metrics_before_eip_initialization() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut child = fixture
        .command(fixture.options())
        .env("A13N_ENVD_TRANSPORT", "reverse_websocket")
        .env(
            "A13N_ENVD_REVERSE_WS_URL",
            format!("ws://{}/eip", listener.local_addr().unwrap()),
        )
        .env(
            "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE",
            fixture.root.join("execution-credential"),
        )
        .spawn()
        .unwrap();
    // No accepted EIP handshake or Session is needed by monitoring.
    fixture.ready(&mut child).await;
    child.kill().await.unwrap();
    child.wait().await.unwrap();
}

#[tokio::test]
async fn startup_rejects_invalid_monitoring_configuration_and_occupied_bind() {
    let fixture = Fixture::new();
    for (key, value, expected) in [
        ("bind", json!("0.0.0.0:9099"), "loopback"),
        ("bind", json!("127.0.0.1:0"), "nonzero port"),
        ("sample_interval_ms", json!(0), "between 1000 and 60000"),
        (
            "tls_certificate_file",
            json!("/missing"),
            "configured together",
        ),
        (
            "filesystems",
            json!({"bad label":"/"}),
            "named absolute paths",
        ),
        ("cgroup_v2_directory", json!("relative"), "must be absolute"),
        ("unknown", json!(true), "unknown field"),
    ] {
        let mut options = fixture.options();
        options[key] = value;
        fixture.reject(options, expected).await;
    }
    let listener = TcpListener::bind(fixture.address).await.unwrap();
    fixture
        .reject(fixture.options(), "Address already in use")
        .await;
    drop(listener);
}

#[tokio::test]
async fn native_tls_serves_monitoring_with_verified_certificate() {
    use tokio_rustls::{
        TlsConnector,
        rustls::{ClientConfig, RootCertStore, pki_types::ServerName},
    };
    let fixture = Fixture::new();
    let certificate = rcgen::generate_simple_self_signed(vec!["localhost".to_string()]).unwrap();
    fs::write(fixture.root.join("cert.pem"), certificate.cert.pem()).unwrap();
    fs::write(
        fixture.root.join("key.pem"),
        certificate.signing_key.serialize_pem(),
    )
    .unwrap();
    let mut options = fixture.options();
    options.as_object_mut().unwrap().remove("plaintext_scope");
    options["tls_certificate_file"] = json!(fixture.root.join("cert.pem"));
    options["tls_private_key_file"] = json!(fixture.root.join("key.pem"));
    let mut child = fixture.command(options).spawn().unwrap();
    let mut roots = RootCertStore::empty();
    roots.add(certificate.cert.der().clone()).unwrap();
    let connector = TlsConnector::from(std::sync::Arc::new(
        ClientConfig::builder()
            .with_root_certificates(roots)
            .with_no_client_auth(),
    ));
    timeout(Duration::from_secs(10), async {
        loop {
            assert!(child.try_wait().unwrap().is_none());
            if let Ok(stream) = TcpStream::connect(fixture.address).await {
                let stream = connector
                    .connect(ServerName::try_from("localhost").unwrap(), stream)
                    .await
                    .unwrap();
                let (status, _) = request(stream, "GET", REPORT, Some(TOKEN), "").await;
                if status == 200 {
                    break;
                }
                assert_eq!(status, 503);
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    eof(child).await;
}

#[tokio::test]
async fn selected_cgroup_is_separate_from_system_and_missing_fields_recover() {
    let fixture = Fixture::new();
    let cgroup = fixture.root.join("cgroup");
    fs::create_dir(&cgroup).unwrap();
    fs::write(
        cgroup.join("cpu.stat"),
        "usage_usec 12345\nuser_usec 10000\nsystem_usec 2345\nnr_periods 100\nnr_throttled 12\nthrottled_usec 7654\n",
    )
    .unwrap();
    fs::write(cgroup.join("cpu.max"), "150000 100000\n").unwrap();
    fs::write(cgroup.join("memory.max"), "max\n").unwrap();
    fs::write(
        cgroup.join("memory.events"),
        "low 0\nhigh 7\nmax 4\noom 2\noom_kill 1\n",
    )
    .unwrap();
    fs::write(cgroup.join("cpu.pressure"), "some avg10=1.25 avg60=2.50 avg300=3.75 total=123456\nfull avg10=0.10 avg60=0.20 avg300=0.30 total=789\n").unwrap();
    fs::write(
        cgroup.join("memory.pressure"),
        "some avg10=0 avg60=0 avg300=0 total=0\nfull avg10=NaN avg60=0 avg300=0 total=0\n",
    )
    .unwrap();
    let mut options = fixture.options();
    options["cgroup_v2_directory"] = json!(cgroup);
    let mut child = fixture.command(options).spawn().unwrap();
    let first = fixture.ready(&mut child).await;
    let metrics = &first["sample"]["cgroup_v2"];
    assert_eq!(metrics["cpu_usage_usec"], 12345);
    assert_eq!(metrics["cpu_user_usec"], 10000);
    assert_eq!(metrics["cpu_system_usec"], 2345);
    assert_eq!(metrics["cpu_periods_total"], 100);
    assert_eq!(metrics["cpu_throttled_periods_total"], 12);
    assert_eq!(metrics["cpu_throttled_usec"], 7654);
    assert_eq!(metrics["memory_events"]["low"], 0);
    assert_eq!(metrics["memory_events"]["high"], 7);
    assert_eq!(metrics["memory_events"]["oom_kill"], 1);
    assert!(metrics["memory_events"]["oom_group_kill"].is_null());
    assert_eq!(metrics["pressure"]["cpu"]["some"]["avg10_percent"], 1.25);
    assert_eq!(metrics["pressure"]["cpu"]["full"]["total_usec"], 789);
    assert_eq!(metrics["pressure"]["memory"]["some"]["total_usec"], 0);
    assert!(metrics["pressure"]["memory"]["full"].is_null());
    assert!(metrics["pressure"]["io"].is_null());
    assert_eq!(
        metrics["cpu_max"],
        json!({"kind":"limited","quota_usec":150000,"period_usec":100000})
    );
    assert_eq!(metrics["memory_max"], json!({"kind":"unlimited"}));
    assert!(metrics["memory_current_bytes"].is_null());
    assert!(
        first["sample"]["system"]["memory_total_bytes"]
            .as_u64()
            .unwrap()
            > 0
    );
    // A reset remains a raw counter decrease, not a fabricated rate or zero.
    fs::write(
        cgroup.join("cpu.stat"),
        "usage_usec 5\nuser_usec 3\nsystem_usec 2\nnr_throttled broken\n",
    )
    .unwrap();
    fs::write(cgroup.join("memory.events"), "high 8\noom_kill broken\n").unwrap();
    fs::write(
        cgroup.join("io.pressure"),
        "some avg10=4 avg60=5 avg300=6 total=100\n",
    )
    .unwrap();
    fs::write(cgroup.join("memory.current"), "4096\n").unwrap();
    timeout(Duration::from_secs(4), async {
        loop {
            let (_, body) = get(fixture.address, "GET", REPORT, Some(TOKEN), "").await;
            let report: Value = serde_json::from_slice(&body).unwrap();
            if report["sample"]["sequence"] != first["sample"]["sequence"] {
                let metrics = &report["sample"]["cgroup_v2"];
                assert_eq!(metrics["memory_current_bytes"], 4096);
                assert_eq!(metrics["cpu_usage_usec"], 5);
                assert!(metrics["cpu_periods_total"].is_null());
                assert!(metrics["cpu_throttled_periods_total"].is_null());
                assert!(metrics["cpu_throttled_usec"].is_null());
                assert_eq!(metrics["memory_events"]["high"], 8);
                assert!(metrics["memory_events"]["oom_kill"].is_null());
                assert_eq!(metrics["pressure"]["io"]["some"]["total_usec"], 100);
                assert!(metrics["pressure"]["io"]["full"].is_null());
                break;
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    eof(child).await;
}

#[tokio::test]
async fn stalled_collection_keeps_one_aging_snapshot_and_recovers() {
    use std::{
        ffi::CString,
        io::Write,
        os::unix::{ffi::OsStrExt, fs::OpenOptionsExt},
    };

    let fixture = Fixture::new();
    let cgroup = fixture.root.join("cgroup");
    fs::create_dir(&cgroup).unwrap();
    let cpu = cgroup.join("cpu.stat");
    fs::write(&cpu, "usage_usec 10\n").unwrap();
    let mut options = fixture.options();
    options["cgroup_v2_directory"] = json!(cgroup);
    let mut child = fixture.command(options).spawn().unwrap();
    let first = fixture.ready(&mut child).await;

    // A FIFO deterministically holds the next native read in flight without
    // requiring a stalled network mount or blocking the test's async runtime.
    fs::remove_file(&cpu).unwrap();
    let path = CString::new(cpu.as_os_str().as_bytes()).unwrap();
    // SAFETY: path is NUL-terminated and remains alive for the call.
    assert_eq!(unsafe { libc::mkfifo(path.as_ptr(), 0o600) }, 0);
    let mut writer = timeout(Duration::from_secs(5), async {
        loop {
            match fs::OpenOptions::new()
                .write(true)
                .custom_flags(libc::O_NONBLOCK)
                .open(&cpu)
            {
                Ok(writer) => break writer,
                Err(error) if error.raw_os_error() == Some(libc::ENXIO) => {
                    sleep(Duration::from_millis(20)).await
                }
                Err(error) => panic!("opening observation FIFO: {error}"),
            }
        }
    })
    .await
    .unwrap();

    // The HTTP listener remains responsive, and repeated reads do not turn
    // the retained snapshot into new observations even after its interval.
    timeout(Duration::from_secs(4), async {
        loop {
            let (status, body) = get(fixture.address, "GET", REPORT, Some(TOKEN), "").await;
            assert_eq!(status, 200);
            let report: Value = serde_json::from_slice(&body).unwrap();
            assert_eq!(report["sample"], first["sample"]);
            if report["sample_age_ms"].as_u64().unwrap()
                > 2 * report["sample_interval_ms"].as_u64().unwrap()
            {
                break;
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();

    // Replace the path before releasing the in-flight FIFO read, so every
    // later collection sees the ordinary source without a missing-file gap.
    let replacement = cgroup.join("cpu.next");
    fs::write(&replacement, "usage_usec 20\n").unwrap();
    fs::rename(replacement, &cpu).unwrap();
    writer.write_all(b"usage_usec 20\n").unwrap();
    drop(writer);
    timeout(Duration::from_secs(5), async {
        loop {
            let (_, body) = get(fixture.address, "GET", REPORT, Some(TOKEN), "").await;
            let report: Value = serde_json::from_slice(&body).unwrap();
            if report["sample"]["sequence"].as_u64().unwrap()
                > first["sample"]["sequence"].as_u64().unwrap()
                && report["sample_age_ms"].as_u64().unwrap()
                    < report["sample_interval_ms"].as_u64().unwrap()
            {
                assert_eq!(report["sample"]["cgroup_v2"]["cpu_usage_usec"], 20);
                break;
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    eof(child).await;
}

#[tokio::test]
async fn monitoring_configuration_supports_partial_json_overlays() {
    let fixture = Fixture::new();
    let mut options = fixture.options();
    options["sample_interval_ms"] = json!(10000);
    fs::write(
        fixture.root.join("config.json"),
        json!({"device_id":"metrics-device", "monitoring":options}).to_string(),
    )
    .unwrap();
    let mut child = fixture
        .command(Value::Null)
        .arg("--config")
        .arg(fixture.root.join("config.json"))
        .env(
            "A13N_ENVD_CONFIG_JSON",
            r#"{"monitoring":{"sample_interval_ms":1000}}"#,
        )
        .spawn()
        .unwrap();
    let report = fixture.ready(&mut child).await;
    assert_eq!(report["sample_interval_ms"], 1000);
    eof(child).await;
}
