use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    process::{Child, Command},
    time::timeout,
};

struct Fixture(tempfile::TempDir);
impl Fixture {
    fn new() -> Self {
        let fixture = Self(tempfile::tempdir().unwrap());
        fs::write(fixture.0.path().join("api-token"), "api-secret").unwrap();
        fixture
    }
    fn command(&self, target: &str) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        for (name, _) in std::env::vars_os() {
            if name.to_string_lossy().starts_with("A13N_ENVD_") {
                command.env_remove(name);
            }
        }
        command
            .args([
                "connect-service",
                target,
                "--host",
                "test",
                "--device-id",
                "dev-test",
            ])
            .arg("--state-dir")
            .arg(self.0.path().join("state"))
            .arg("--default-working-directory")
            .arg(fs::canonicalize(self.0.path()).unwrap())
            .env("A13N_ENVD_FULL_CONTROL", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        command
    }
    fn enroll(&self, url: &str) -> Child {
        self.command(url)
            .args(["--workspace-id", "ws_test", "--api-token-file"])
            .arg(self.0.path().join("api-token"))
            .spawn()
            .unwrap()
    }
    fn profile(&self) -> std::path::PathBuf {
        self.0.path().join("state/instances/default/services/test")
    }
}

async fn request(listener: &TcpListener) -> (TcpStream, String, BTreeMap<String, String>, Value) {
    timeout(Duration::from_secs(15), async {
        let (stream, _) = listener.accept().await.unwrap();
        let mut reader = BufReader::new(stream);
        let mut first = String::new();
        reader.read_line(&mut first).await.unwrap();
        let mut headers = BTreeMap::new();
        loop {
            let mut line = String::new();
            reader.read_line(&mut line).await.unwrap();
            if line == "\r\n" {
                break;
            }
            let (key, value) = line.split_once(':').unwrap();
            headers.insert(key.to_ascii_lowercase(), value.trim().to_owned());
        }
        let length = headers
            .get("content-length")
            .map(|value| value.parse().unwrap())
            .unwrap_or(0);
        let mut body = vec![0; length];
        reader.read_exact(&mut body).await.unwrap();
        let value = if body.is_empty() {
            Value::Null
        } else {
            serde_json::from_slice(&body).unwrap()
        };
        (reader.into_inner(), first, headers, value)
    })
    .await
    .unwrap()
}
async fn respond(mut stream: TcpStream, status: u16, body: Value) {
    let body = body.to_string();
    stream.write_all(format!("HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).await.unwrap();
}
fn registered(token: Value) -> Value {
    json!({"client":{"environment":{"id":"env_test", "device_id":"dev-test"}}, "token":token, "connection_path":"/api/v1/client-environments/env_test/connect"})
}
async fn finish(child: Child) -> String {
    let output = timeout(Duration::from_secs(10), child.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(!output.status.success());
    let error = String::from_utf8(output.stderr).unwrap();
    assert!(
        !error.contains("api-secret") && !error.contains("device-secret"),
        "{error}"
    );
    error
}
async fn websocket(listener: &TcpListener, token: &str) {
    let (stream, line, headers, _) = request(listener).await;
    assert!(
        line.starts_with("GET /api/v1/client-environments/env_test/connect "),
        "{line}"
    );
    assert_eq!(headers["authorization"], format!("Bearer {token}"));
    // A terminal refusal lets the process exit without requiring a full EIP peer.
    respond(stream, 403, json!({})).await;
}

#[tokio::test]
async fn service_enrollment_persists_device_token_and_reconnects_without_api_token() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let child = fixture.enroll(&url);
    let (stream, line, headers, body) = request(&listener).await;
    assert!(
        line.starts_with("POST /api/v1/client-environments/connect?workspace_id=ws_test "),
        "{line}"
    );
    assert_eq!(headers["authorization"], "Bearer api-secret");
    assert_eq!(body["device_id"], "dev-test");
    assert!(body["environment_id"].is_null());
    respond(stream, 201, registered(json!("device-secret"))).await;
    websocket(&listener, "device-secret").await;
    finish(child).await;
    assert_eq!(
        fs::read_to_string(fixture.profile().join("credential")).unwrap(),
        "device-secret"
    );
    let saved = fs::read_to_string(fixture.profile().join("service.json")).unwrap();
    assert!(!saved.contains("secret") && !saved.contains("api-token"));
    fs::remove_file(fixture.0.path().join("api-token")).unwrap();
    // Explicit device_id bypasses a damaged installation file on both starts.
    fs::write(
        fixture.0.path().join("state/instances/default/device-id"),
        "damaged\nidentity",
    )
    .unwrap();
    let child = fixture.command("test").spawn().unwrap();
    websocket(&listener, "device-secret").await;
    finish(child).await;
}

#[tokio::test]
async fn lost_delivery_requires_explicit_rotation_and_accepts_imported_credential() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let child = fixture.enroll(&format!("http://{}", listener.local_addr().unwrap()));
    let (stream, _, _, _) = request(&listener).await;
    respond(stream, 200, registered(Value::Null)).await;
    let error = finish(child).await;
    assert!(
        error.contains("client_credential_required")
            && error.contains("env_test/rotate-credential"),
        "{error}"
    );
    assert!(
        fs::read_to_string(fixture.profile().join("service.json"))
            .unwrap()
            .contains("env_test")
    );
    let rotated = fixture.0.path().join("rotated");
    fs::write(&rotated, "device-secret").unwrap();
    let child = fixture
        .command("test")
        .arg("--credential-file")
        .arg(rotated)
        .spawn()
        .unwrap();
    websocket(&listener, "device-secret").await;
    finish(child).await;
}

#[tokio::test]
async fn supplied_environment_is_forwarded_and_rejection_does_not_retry_or_replace() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let child = fixture
        .command(&format!("http://{}", listener.local_addr().unwrap()))
        .args([
            "--workspace-id",
            "ws_test",
            "--environment-id",
            "env_test",
            "--api-token-file",
        ])
        .arg(fixture.0.path().join("api-token"))
        .spawn()
        .unwrap();
    let (stream, _, _, body) = request(&listener).await;
    assert_eq!(body["environment_id"], "env_test");
    respond(
        stream,
        409,
        json!({"error":{"code":"conflict", "message":"device id mismatch", "details":{"reason":"device_id_mismatch"}}}),
    )
    .await;
    let error = finish(child).await;
    assert!(
        error.contains("device_id_mismatch") && error.contains("--device-id"),
        "{error}"
    );
    assert_eq!(
        fs::metadata(fixture.profile().join("credential"))
            .unwrap()
            .len(),
        0
    );
    assert!(matches!(
        listener.into_std().unwrap().accept(),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
    ));
}

#[tokio::test]
async fn console_pairing_saves_secret_before_claim_and_reconnects_without_api_token() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let mut child = fixture
        .command(&url)
        .args(["--workspace-id", "ws_test", "--pairing-id", "pair_test"])
        .spawn()
        .unwrap();
    let (stream, line, headers, body) = request(&listener).await;
    assert!(line.starts_with("POST /api/v1/client-environments/pairings/pair_test/claim "));
    let credential = fs::read_to_string(fixture.profile().join("credential")).unwrap();
    assert_eq!(credential.len(), 64);
    assert_eq!(
        headers["x-a13n-device-credential"],
        format!("Bearer {credential}")
    );
    assert_eq!(body["device_id"], "dev-test");
    assert_eq!(body["full_control"], true);
    respond(
        stream,
        200,
        json!({"state":"pending", "verification_code":"123456"}),
    )
    .await;
    let (stream, _, repeated_headers, repeated_body) = request(&listener).await;
    assert_eq!(
        headers["x-a13n-device-credential"],
        repeated_headers["x-a13n-device-credential"]
    );
    assert_eq!(body, repeated_body);
    respond(stream, 200, json!({"state":"approved", "verification_code":"123456", "environment_id":"env_test", "connection_path":"/api/v1/client-environments/env_test/connect"})).await;
    websocket(&listener, &credential).await;
    child.kill().await.unwrap();
    let output = child.wait_with_output().await.unwrap();
    let log = String::from_utf8(output.stderr).unwrap();
    assert!(log.contains("123456") && log.contains("env_test"));
    assert!(!log.contains(&credential));
    let mut reconnect = fixture.command("test").spawn().unwrap();
    websocket(&listener, &credential).await;
    reconnect.kill().await.unwrap();
    reconnect.wait().await.unwrap();
}

#[tokio::test]
async fn rejected_pairing_preserves_local_secret_and_does_not_open_a_websocket() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let child = fixture
        .command(&url)
        .args(["--workspace-id", "ws_test", "--pairing-id", "pair_expired"])
        .spawn()
        .unwrap();
    let (stream, _, _, _) = request(&listener).await;
    let credential = fs::read_to_string(fixture.profile().join("credential")).unwrap();
    respond(
        stream,
        404,
        json!({"error":{"code":"not_found", "message":"Pairing expired"}}),
    )
    .await;
    let error = finish(child).await;
    assert!(!error.contains(&credential));
    assert_eq!(
        credential,
        fs::read_to_string(fixture.profile().join("credential")).unwrap()
    );
    assert!(matches!(
        listener.into_std().unwrap().accept(),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
    ));
}

#[tokio::test]
async fn link_check_reports_target_without_contacting_it_or_starting_a_daemon() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let link = format!(
        "a13n-envd://connect?service=http://{}&workspace=ws_test&host=console-test&pairing=pair_test",
        listener.local_addr().unwrap()
    );
    let output = Command::new(env!("CARGO_BIN_EXE_a13n-envd"))
        .args(["connect-link", &link, "--check"])
        .output()
        .await
        .unwrap();
    assert!(output.status.success());
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(text.contains("Service: http://127.0.0.1:"));
    assert!(text.contains("Workspace: ws_test"));
    assert!(!text.contains("pair_test"));
    let output = Command::new(env!("CARGO_BIN_EXE_a13n-envd"))
        .args(["connect-link", &link, "--check-json"])
        .output()
        .await
        .unwrap();
    assert!(output.status.success());
    let data: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(data["workspace"], "ws_test");
    assert_eq!(data["host"], "console-test");
    assert!(
        !String::from_utf8(output.stdout)
            .unwrap()
            .contains("pair_test")
    );
    assert!(matches!(
        listener.into_std().unwrap().accept(),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
    ));
}

#[cfg(unix)]
#[tokio::test]
async fn launcher_reports_verification_without_secrets_and_stops_when_app_pipe_closes() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let mut child = fixture
        .command(&format!("http://{}", listener.local_addr().unwrap()))
        .args([
            "--workspace-id",
            "ws_test",
            "--pairing-id",
            "pair_test",
            "--launcher",
        ])
        .stdin(Stdio::piped())
        .spawn()
        .unwrap();
    let (stream, _, _, _) = request(&listener).await;
    respond(
        stream,
        200,
        json!({"state":"pending", "verification_code":"123456"}),
    )
    .await;
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let mut line = String::new();
    timeout(Duration::from_secs(5), output.read_line(&mut line))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        serde_json::from_str::<Value>(&line).unwrap(),
        json!({"event":"verification","data":{"code":"123456"}})
    );
    let credential = fs::read_to_string(fixture.profile().join("credential")).unwrap();
    assert!(!line.contains(&credential));
    drop(child.stdin.take());
    let error = finish(child).await;
    assert!(error.contains("Console pairing cancelled"), "{error}");
}

#[tokio::test]
async fn recognition_reuses_registration_while_daemon_owns_runtime_and_preserves_files() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let child = fixture.enroll(&url);
    let (stream, _, _, _) = request(&listener).await;
    respond(stream, 200, registered(json!("device-secret"))).await;
    // Keep the original daemon alive and holding its runtime lock.
    let (carrier, line, _, _) = request(&listener).await;
    assert!(line.starts_with("GET /api/v1/client-environments/env_test/connect "));
    let profile = fs::read(fixture.profile().join("service.json")).unwrap();
    let credential = fs::read(fixture.profile().join("credential")).unwrap();
    for status in [200, 401] {
        let link = format!(
            "a13n-envd://connect?service={url}&workspace=ws_test&host=test&pairing=pair_test"
        );
        let probe = Command::new(env!("CARGO_BIN_EXE_a13n-envd"))
            .args(["connect-link", &link, "--recognize", "--state-dir"])
            .arg(fixture.0.path().join("state"))
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let (stream, line, headers, body) = request(&listener).await;
        assert!(line.starts_with("POST /api/v1/client-environments/pairings/pair_test/recognize "));
        assert_eq!(headers["x-a13n-device-credential"], "Bearer device-secret");
        assert_eq!(
            body,
            json!({"device_id":"dev-test", "environment_id":"env_test"})
        );
        respond(
            stream,
            status,
            json!({"state":"approved", "reused":true, "environment_id":"env_test"}),
        )
        .await;
        let output = timeout(Duration::from_secs(5), probe.wait_with_output())
            .await
            .unwrap()
            .unwrap();
        assert_eq!(output.status.success(), status == 200);
        if status == 200 {
            assert_eq!(
                serde_json::from_slice::<Value>(&output.stdout).unwrap(),
                json!({"registered":true})
            );
        }
        assert!(!String::from_utf8_lossy(&output.stdout).contains("device-secret"));
        assert!(!String::from_utf8_lossy(&output.stderr).contains("device-secret"));
        assert_eq!(
            profile,
            fs::read(fixture.profile().join("service.json")).unwrap()
        );
        assert_eq!(
            credential,
            fs::read(fixture.profile().join("credential")).unwrap()
        );
    }
    respond(carrier, 403, json!({})).await;
    finish(child).await;
    assert!(matches!(
        listener.into_std().unwrap().accept(),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock
    ));
}

#[tokio::test]
async fn recognition_without_registration_does_not_create_state_or_contact_service() {
    let fixture = Fixture::new();
    let output = Command::new(env!("CARGO_BIN_EXE_a13n-envd"))
        .args(["connect-link", "a13n-envd://connect?service=https://unreachable.invalid&workspace=ws_test&host=test&pairing=pair_test", "--recognize", "--state-dir"])
        .arg(fixture.0.path().join("state"))
        .output().await.unwrap();
    assert!(output.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stdout).unwrap(),
        json!({"registered":false})
    );
    assert!(!fixture.0.path().join("state").exists());
}
