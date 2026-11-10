use std::{
    fs,
    net::{SocketAddr, TcpListener, TcpStream},
    process::{Child, Command, Stdio},
    thread::sleep,
    time::{Duration, Instant},
};

struct Fixture {
    root: tempfile::TempDir,
    address: SocketAddr,
    client: reqwest::blocking::Client,
}

impl Fixture {
    fn new() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        Self {
            root: tempfile::tempdir().unwrap(),
            address: listener.local_addr().unwrap(),
            client: reqwest::blocking::Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(1))
                .build()
                .unwrap(),
        }
    }

    fn command(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        for (name, _) in std::env::vars_os() {
            if name.to_string_lossy().starts_with("A13N_ENVD_") {
                command.env_remove(name);
            }
        }
        command
            .arg("daemon")
            .current_dir(self.root.path())
            .env("TOKIO_WORKER_THREADS", "2")
            .env("A13N_ENVD_RUNTIME_DIR", self.root.path().join("runtime"))
            .env("A13N_ENVD_STATE_DIR", self.root.path().join("installation"))
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(fs::File::create(self.root.path().join("stderr")).unwrap());
        command
    }

    fn wait(&self, child: &mut Child) {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            assert!(child.try_wait().unwrap().is_none(), "{}", self.log());
            if self
                .client
                .get(self.url("/healthz"))
                .send()
                .is_ok_and(|response| response.status().is_success())
            {
                return;
            }
            assert!(Instant::now() < deadline, "{}", self.log());
            sleep(Duration::from_millis(20));
        }
    }

    fn url(&self, path: &str) -> String {
        format!("http://{}{path}", self.address)
    }

    fn log(&self) -> String {
        fs::read_to_string(self.root.path().join("stderr")).unwrap()
    }
}

struct Running(Child);
impl Drop for Running {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn health_is_opt_in_and_does_not_bind_when_disabled() {
    let fixture = Fixture::new();
    let held = TcpListener::bind(fixture.address).unwrap();
    for config in ["{}", r#"{"health":{"enabled":false}}"#] {
        let mut command = fixture.command();
        command
            .env("A13N_ENVD_CONFIG_JSON", config)
            .env(
                "A13N_ENVD_HEALTH_BIND",
                held.local_addr().unwrap().to_string(),
            )
            .stdin(Stdio::null());
        assert!(command.status().unwrap().success(), "{}", fixture.log());
    }
}

#[test]
fn probe_routes_are_public_and_expose_no_eip_or_device_details() {
    let fixture = Fixture::new();
    let mut command = fixture.command();
    command
        .env("A13N_ENVD_HEALTH_ENABLED", "true")
        .env("A13N_ENVD_HEALTH_BIND", fixture.address.to_string());
    let mut child = Running(command.spawn().unwrap());
    fixture.wait(&mut child.0);
    for path in ["/healthz", "/ping"] {
        let response = fixture.client.get(fixture.url(path)).send().unwrap();
        assert_eq!(response.status(), 200);
        assert_eq!(response.headers()["cache-control"], "no-store");
        assert_eq!(response.text().unwrap(), "ok\n");
        let response = fixture.client.head(fixture.url(path)).send().unwrap();
        assert_eq!(response.status(), 200);
        assert!(response.bytes().unwrap().is_empty());
        let response = fixture.client.post(fixture.url(path)).send().unwrap();
        assert_eq!(response.status(), 405);
        assert_eq!(response.headers()["allow"], "GET, HEAD");
    }
    for path in ["/", "/metrics", "/eip/control", "/eip/transfer"] {
        assert_eq!(
            fixture
                .client
                .get(fixture.url(path))
                .send()
                .unwrap()
                .status(),
            404
        );
    }
    // Stdio EOF shuts down the daemon and releases its probe socket.
    drop(child.0.stdin.take());
    let deadline = Instant::now() + Duration::from_secs(5);
    while child.0.try_wait().unwrap().is_none() {
        assert!(Instant::now() < deadline);
        sleep(Duration::from_millis(20));
    }
    assert!(TcpStream::connect(fixture.address).is_err());
}

#[test]
fn health_remains_live_while_outbound_host_is_unavailable() {
    let fixture = Fixture::new();
    let credential = fixture.root.path().join("credential");
    fs::write(&credential, "health-test-credential").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&credential, fs::Permissions::from_mode(0o600)).unwrap();
    }
    let unavailable = TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap();
    let mut command = fixture.command();
    command
        .env("A13N_ENVD_TRANSPORT", "reverse_websocket")
        .env(
            "A13N_ENVD_REVERSE_WS_URL",
            format!("ws://{unavailable}/eip"),
        )
        .env("A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE", credential)
        .env(
            "A13N_ENVD_CONFIG_JSON",
            serde_json::json!({"health":{"enabled":true,"bind":fixture.address}}).to_string(),
        );
    let mut child = Running(command.spawn().unwrap());
    fixture.wait(&mut child.0);
    drop(child.0.stdin.take());
    sleep(Duration::from_millis(100));
    assert_eq!(
        fixture
            .client
            .get(fixture.url("/ping"))
            .send()
            .unwrap()
            .status(),
        200
    );
    assert!(child.0.try_wait().unwrap().is_none());
}

#[test]
fn enabled_probe_bind_failure_and_invalid_configuration_fail_startup() {
    let fixture = Fixture::new();
    let held = TcpListener::bind(fixture.address).unwrap();
    let mut command = fixture.command();
    command
        .env("A13N_ENVD_HEALTH_ENABLED", "1")
        .env(
            "A13N_ENVD_HEALTH_BIND",
            held.local_addr().unwrap().to_string(),
        )
        .stdin(Stdio::null());
    assert!(!command.status().unwrap().success());
    assert!(fixture.log().contains("cannot bind health listener"));
    for (name, value) in [
        ("A13N_ENVD_HEALTH_BIND", "localhost:8080"),
        ("A13N_ENVD_HEALTH_ENABLED", "maybe"),
    ] {
        let mut command = fixture.command();
        command.env(name, value).stdin(Stdio::null());
        assert!(!command.status().unwrap().success(), "{name}");
    }
}

#[test]
fn health_listener_is_separate_from_authenticated_http_execution() {
    let fixture = Fixture::new();
    let credential = fixture.root.path().join("credential");
    fs::write(&credential, "health-test-credential").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&credential, fs::Permissions::from_mode(0o600)).unwrap();
    }
    let execution = TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap();
    let mut command = fixture.command();
    command
        .env("A13N_ENVD_TRANSPORT", "http")
        .env("A13N_ENVD_HTTP_BIND", execution.to_string())
        .env("A13N_ENVD_HTTP_PLAINTEXT_SCOPE", "loopback")
        .env("A13N_ENVD_HTTP_CREDENTIAL_FILE", credential)
        .env("A13N_ENVD_HEALTH_ENABLED", "true")
        .env("A13N_ENVD_HEALTH_BIND", fixture.address.to_string());
    let mut child = Running(command.spawn().unwrap());
    fixture.wait(&mut child.0);
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Ok(response) = fixture
            .client
            .get(format!("http://{execution}/healthz"))
            .send()
        {
            assert_eq!(response.status(), 405);
            break;
        }
        assert!(Instant::now() < deadline, "{}", fixture.log());
        sleep(Duration::from_millis(20));
    }
    assert_eq!(
        fixture
            .client
            .post(format!("http://{execution}/eip/control"))
            .send()
            .unwrap()
            .status(),
        401
    );
    let response = fixture.client.post(format!("http://{execution}/eip/control"))
        .bearer_auth("health-test-credential")
        .header("Content-Type", "application/json")
        .body(serde_json::json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"client":{"name":"health-test","version":"1"}}}).to_string())
        .send().unwrap();
    assert_eq!(response.status(), 200);
    assert_eq!(
        fixture
            .client
            .get(fixture.url("/healthz"))
            .send()
            .unwrap()
            .status(),
        200
    );
}

#[test]
fn health_environment_overrides_file_configuration() {
    let fixture = Fixture::new();
    let mut command = fixture.command();
    command
        .env(
            "A13N_ENVD_CONFIG_JSON",
            r#"{"health":{"enabled":false,"bind":"127.0.0.1:1"}}"#,
        )
        .env("A13N_ENVD_HEALTH_ENABLED", "true")
        .env("A13N_ENVD_HEALTH_BIND", fixture.address.to_string());
    let mut child = Running(command.spawn().unwrap());
    fixture.wait(&mut child.0);
}
