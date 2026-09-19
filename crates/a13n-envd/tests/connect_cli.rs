use std::{collections::BTreeMap, fs, path::PathBuf, process::Stdio, time::Duration};

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    process::Child,
    time::timeout,
};
use tokio_tungstenite::{WebSocketStream, accept_hdr_async, tungstenite::Message};

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let mut random = [0_u8; 8];
        getrandom::fill(&mut random).unwrap();
        let root =
            std::env::temp_dir().join(format!("envd-connect-{:016x}", u64::from_le_bytes(random)));
        fs::create_dir_all(&root).unwrap();
        #[cfg(unix)]
        {
            fs::create_dir(root.join("bin")).unwrap();
            std::os::unix::fs::symlink("/bin/sh", root.join("bin/envd-path-test")).unwrap();
        }
        Self(root)
    }

    fn start(&self, target: &str) -> Child {
        self.start_named(target, Some("Test device"))
    }

    fn start_named(&self, target: &str, name: Option<&str>) -> Child {
        let mut command = tokio::process::Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        for (name, _) in std::env::vars_os() {
            if name.to_string_lossy().starts_with("A13N_ENVD_") {
                command.env_remove(name);
            }
        }
        #[cfg(unix)]
        command.env(
            "PATH",
            std::env::join_paths(
                std::iter::once(self.0.join("bin")).chain(std::env::split_paths(
                    &std::env::var_os("PATH").unwrap_or_default(),
                )),
            )
            .unwrap(),
        );
        command.args(["connect", target, "--host", "test"]);
        if let Some(name) = name {
            command.args(["--name", name]);
        }
        command
            .arg("--state-dir")
            .arg(self.0.join("state"))
            .arg("--default-working-directory")
            .arg(&self.0)
            .env("A13N_ENVD_FULL_CONTROL", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .unwrap()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

async fn http_request(listener: &TcpListener) -> (TcpStream, BTreeMap<String, String>, Vec<u8>) {
    timeout(Duration::from_secs(10), async {
        let (stream, _) = listener.accept().await.unwrap();
        let mut reader = BufReader::new(stream);
        let mut line = String::new();
        reader.read_line(&mut line).await.unwrap();
        assert!(
            line.starts_with("POST /api/envd/pair ")
                || line.starts_with("GET /api/devices/device-test/connect "),
            "{line}"
        );
        let mut headers = BTreeMap::new();
        loop {
            line.clear();
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
        (reader.into_inner(), headers, body)
    })
    .await
    .unwrap()
}

async fn respond(mut stream: TcpStream, value: Value) {
    let body = serde_json::to_vec(&value).unwrap();
    stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).as_bytes()).await.unwrap();
    stream.write_all(&body).await.unwrap();
}

#[allow(
    clippy::result_large_err,
    reason = "tungstenite defines the handshake callback result"
)]
async fn accept(listener: &TcpListener, authorization: &str) -> WebSocketStream<TcpStream> {
    let (stream, _) = timeout(Duration::from_secs(10), listener.accept())
        .await
        .unwrap()
        .unwrap();
    accept_hdr_async(
        stream,
        |request: &tokio_tungstenite::tungstenite::handshake::server::Request,
         mut response: tokio_tungstenite::tungstenite::handshake::server::Response| {
            assert_eq!(request.uri().path(), "/api/devices/device-test/connect");
            assert_eq!(request.headers()["authorization"], authorization);
            response
                .headers_mut()
                .insert("sec-websocket-protocol", "eip.v1".parse().unwrap());
            Ok(response)
        },
    )
    .await
    .unwrap()
}

async fn call(
    socket: &mut WebSocketStream<TcpStream>,
    session: Option<&str>,
    method: &str,
    params: Value,
) -> Value {
    let mut request = json!({"jsonrpc":"2.0", "id":1, "method":method, "params":params});
    if let Some(session) = session {
        request["eip_session"] = json!(session);
    }
    socket
        .send(Message::Text(request.to_string().into()))
        .await
        .unwrap();
    timeout(Duration::from_secs(15), async {
        loop {
            match socket.next().await.unwrap().unwrap() {
                Message::Text(text) => {
                    let response: Value = serde_json::from_str(&text).unwrap();
                    assert!(response.get("error").is_none(), "{response}");
                    return response["result"].clone();
                }
                Message::Ping(bytes) => socket.send(Message::Pong(bytes)).await.unwrap(),
                _ => {}
            }
        }
    })
    .await
    .unwrap()
}

async fn stop(mut child: Child, credential: &str) {
    #[cfg(unix)]
    unsafe {
        libc::kill(child.id().unwrap() as i32, libc::SIGTERM);
    }
    #[cfg(windows)]
    child.kill().await.unwrap();
    timeout(Duration::from_secs(10), child.wait())
        .await
        .unwrap()
        .unwrap();
    let output = child.wait_with_output().await.unwrap();
    assert!(!String::from_utf8_lossy(&output.stderr).contains(credential));
    assert!(output.stdout.is_empty());
}

#[tokio::test]
async fn connect_pairs_once_runs_native_shell_and_reuses_credential_after_restart() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let child = fixture.start(&format!("http://{address}"));
    let (stream, headers, body) = http_request(&listener).await;
    let authorization = &headers["authorization"];
    let credential = authorization.strip_prefix("Bearer ").unwrap();
    assert_eq!(credential.len(), 64);
    let request: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(request["name"], "Test device");
    let device_id = request["device_id"].as_str().unwrap();
    respond(stream, json!({"status":"pending", "challenge":{"verification_code":"ABCD-1234"}, "approval_url":null, "poll_after_seconds":1})).await;
    let (stream, repeated, body) = http_request(&listener).await;
    assert_eq!(&repeated["authorization"], authorization);
    assert_eq!(serde_json::from_slice::<Value>(&body).unwrap(), request);
    respond(stream, json!({"status":"approved", "resource_id":"device-test", "websocket_url":format!("ws://{address}/api/devices/device-test/connect")})).await;
    let mut socket = accept(&listener, authorization).await;
    let initialize = json!({"supported_protocol_versions":["0.1"], "expected_device_id":device_id, "client":{"name":"pairing-test", "version":"1"}});
    let first = call(&mut socket, None, "initialize", initialize.clone()).await;
    let descriptor = &first["descriptor"];
    let opened = call(&mut socket, None, "session.open", json!({"expected_device_id":device_id, "expected_generation":descriptor["generation"], "protocol_version":"0.1", "working_directory":descriptor["default_working_directory"], "required_methods":["shell.exec"]})).await;
    let session = opened["descriptor"]["session_id"].as_str().unwrap();
    assert_eq!(
        opened["descriptor"]["shell_profiles"][0]["profile_id"],
        "default"
    );
    call(
        &mut socket,
        Some(session),
        "environment.readiness",
        json!({"context":{"operation_id":"ready", "timeout_ms":5000}}),
    )
    .await;
    let script = if cfg!(windows) {
        "[IO.File]::WriteAllText('pairing-smoke.txt', 'ready')"
    } else {
        "printf ready > pairing-smoke.txt"
    };
    let result = call(&mut socket, Some(session), "shell.exec", json!({"context":{"operation_id":"shell", "timeout_ms":10000}, "request":{"command":{"kind":"shell", "profile_id":"default", "script":script}}})).await;
    assert_eq!(result["status"]["exit_code"], 0);
    assert_eq!(
        fs::read_to_string(fixture.0.join("pairing-smoke.txt")).unwrap(),
        "ready"
    );
    #[cfg(unix)]
    {
        let result = call(&mut socket, Some(session), "shell.exec", json!({
            "context":{"operation_id":"argv", "timeout_ms":10000},
            "request":{"command":{"kind":"argv", "executable_spec":{"kind":"name", "name":"envd-path-test"},
                "arguments":["-c", "test -z \"$A13N_ENVD_FULL_CONTROL\" && test \"${PATH%%:*}\" = \"$PWD/bin\""]}}
        })).await;
        assert_eq!(result["status"]["exit_code"], 0);
    }
    call(&mut socket, Some(session), "session.close", json!({})).await;
    stop(child, credential).await;
    drop(socket);

    let child = fixture.start("test");
    // No enrollment POST on a saved registration: the next request is WebSocket.
    let mut socket = accept(&listener, authorization).await;
    let restarted = call(&mut socket, None, "initialize", initialize).await;
    assert_eq!(
        restarted["descriptor"]["device_id"],
        descriptor["device_id"]
    );
    assert_ne!(
        restarted["descriptor"]["generation"],
        descriptor["generation"]
    );
    stop(child, credential).await;
    drop(socket);

    let child = fixture.start("test");
    let (mut stream, rejected, _) = http_request(&listener).await;
    assert_eq!(&rejected["authorization"], authorization);
    stream
        .write_all(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        .await
        .unwrap();
    drop(stream);
    let output = timeout(Duration::from_secs(10), child.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(!output.status.success());
    assert!(!String::from_utf8_lossy(&output.stderr).contains(credential));
    let saved = fixture
        .0
        .join("state/instances/default/hosts/test/credential");
    assert_eq!(fs::read_to_string(saved).unwrap(), credential);
}

#[tokio::test]
async fn ipv6_pending_pairing_resumes_with_saved_name_and_credential() {
    let fixture = Fixture::new();
    let listener = TcpListener::bind("[::1]:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let child = fixture.start(&format!("http://{address}"));
    let (stream, headers, body) = http_request(&listener).await;
    let authorization = &headers["authorization"];
    let credential = authorization.strip_prefix("Bearer ").unwrap();
    let request: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(request["name"], "Test device");
    respond(stream, json!({"status":"pending", "challenge":{"verification_code":"ABCD-1234"}, "approval_url":null, "poll_after_seconds":1})).await;
    stop(child, credential).await;

    let child = fixture.start_named("test", None);
    let (stream, resumed_headers, body) = http_request(&listener).await;
    assert_eq!(&resumed_headers["authorization"], authorization);
    assert_eq!(serde_json::from_slice::<Value>(&body).unwrap(), request);
    respond(stream, json!({"status":"approved", "resource_id":"device-test", "websocket_url":format!("ws://{address}/api/devices/device-test/connect")})).await;
    let mut socket = accept(&listener, authorization).await;
    let result = call(&mut socket, None, "initialize", json!({"supported_protocol_versions":["0.1"], "expected_device_id":request["device_id"], "client":{"name":"ipv6-test", "version":"1"}})).await;
    assert_eq!(result["descriptor"]["device_id"], request["device_id"]);
    stop(child, credential).await;
}
