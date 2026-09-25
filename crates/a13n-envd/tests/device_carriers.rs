use std::{net::SocketAddr, path::PathBuf, process::Stdio, time::Duration};

use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    process::Child,
    time::timeout,
};
use tokio_tungstenite::{WebSocketStream, accept_hdr_async, tungstenite::Message};

struct DeviceProcess {
    root: PathBuf,
    child: Child,
}

impl DeviceProcess {
    fn start(transport: &str, address: SocketAddr, extra: Value) -> Self {
        let mut random = [0_u8; 8];
        getrandom::fill(&mut random).unwrap();
        let root =
            std::env::temp_dir().join(format!("a13n-carrier-{:016x}", u64::from_le_bytes(random)));
        std::fs::create_dir_all(root.join("alpha")).unwrap();
        std::fs::create_dir(root.join("beta")).unwrap();
        let mut config =
            json!({"device_id":"device-test", "default_working_directory":root.join("alpha")});
        config
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        std::fs::write(
            root.join("config.json"),
            serde_json::to_vec(&config).unwrap(),
        )
        .unwrap();
        let credential = root.join("credential");
        std::fs::write(&credential, "carrier-test-credential").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&credential, std::fs::Permissions::from_mode(0o600)).unwrap();
        }
        let mut command = tokio::process::Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        command
            .env_clear()
            .env("A13N_ENVD_RUNTIME_DIR", root.join("runtime"))
            .env("A13N_ENVD_TRANSPORT", transport)
            .args(["--config", root.join("config.json").to_str().unwrap()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);
        if transport == "http" {
            command
                .env("A13N_ENVD_HTTP_BIND", address.to_string())
                .env("A13N_ENVD_HTTP_CREDENTIAL_FILE", &credential)
                .env("A13N_ENVD_HTTP_PLAINTEXT_SCOPE", "loopback");
        } else {
            command
                .env("A13N_ENVD_REVERSE_WS_URL", format!("ws://{address}/eip"))
                .env("A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE", &credential);
        }
        #[cfg(windows)]
        if let Some(system_root) = std::env::var_os("SystemRoot") {
            command.env("SystemRoot", system_root);
        }
        let child = command.spawn().unwrap();
        Self { root, child }
    }

    fn path(&self, folder: &str) -> String {
        let native = std::fs::canonicalize(self.root.join(folder)).unwrap();
        let path = native.to_str().unwrap().replace('\\', "/");
        if cfg!(windows) {
            format!("/{}", path.strip_prefix("//?/").unwrap_or(&path))
        } else {
            path
        }
    }

    async fn stop(mut self) {
        #[cfg(unix)]
        unsafe {
            libc::kill(self.child.id().unwrap() as i32, libc::SIGTERM);
        }
        #[cfg(windows)]
        self.child.kill().await.unwrap();
        let status = timeout(Duration::from_secs(15), self.child.wait())
            .await
            .unwrap()
            .unwrap();
        #[cfg(unix)]
        assert!(status.success(), "{status}");
        #[cfg(windows)]
        let _ = status;
        std::fs::remove_dir_all(self.root).unwrap();
    }
}

fn request(session: Option<&str>, method: &str, params: Value) -> Value {
    let mut request = json!({"jsonrpc":"2.0", "id":1, "method":method, "params":params});
    if let Some(session) = session {
        request["eip_session"] = json!(session);
    }
    request
}

fn initialize() -> Value {
    json!({"supported_protocol_versions":["0.1"], "expected_device_id":"device-test", "client":{"name":"carrier-test", "version":"0"}})
}

fn open_params(descriptor: &Value, path: &str) -> Value {
    json!({"expected_device_id":"device-test", "expected_generation":descriptor["generation"], "protocol_version":"0.1", "working_directory":path, "required_methods":[]})
}

fn context(operation: &str) -> Value {
    json!({"context":{"operation_id":operation, "timeout_ms":5000}})
}

fn result(response: Value) -> Value {
    assert!(response.get("error").is_none(), "{response}");
    response["result"].clone()
}

async fn http_control(address: SocketAddr, request: Value) -> Value {
    let bytes = serde_json::to_vec(&request).unwrap();
    let response = http_request(address, "/eip/control", "application/json", "", &bytes).await;
    assert_eq!(response.0, 200, "{}", String::from_utf8_lossy(&response.1));
    let response: Value = serde_json::from_slice(&response.1).unwrap();
    assert_eq!(response.get("eip_session"), request.get("eip_session"));
    response
}

async fn http_request(
    address: SocketAddr,
    path: &str,
    media: &str,
    headers: &str,
    body: &[u8],
) -> (u16, Vec<u8>) {
    timeout(Duration::from_secs(10), async {
        let mut stream = TcpStream::connect(address).await.unwrap();
        stream.write_all(format!("POST {path} HTTP/1.1\r\nHost: {address}\r\nAuthorization: Bearer carrier-test-credential\r\nContent-Type: {media}\r\nContent-Length: {}\r\nConnection: close\r\n{headers}\r\n", body.len()).as_bytes()).await.unwrap();
        stream.write_all(body).await.unwrap();
        let mut response = Vec::new();
        stream.read_to_end(&mut response).await.unwrap();
        let boundary = response.windows(4).position(|bytes| bytes == b"\r\n\r\n").unwrap();
        let headers = std::str::from_utf8(&response[..boundary]).unwrap();
        let status = headers.split_whitespace().nth(1).unwrap().parse().unwrap();
        let body = &response[boundary+4..];
        if headers.to_ascii_lowercase().contains("transfer-encoding: chunked") {
            let mut offset = 0;
            let mut decoded = Vec::new();
            loop {
                let end = body[offset..].windows(2).position(|bytes| bytes == b"\r\n").unwrap()+offset;
                let length = usize::from_str_radix(std::str::from_utf8(&body[offset..end]).unwrap(), 16).unwrap();
                if length == 0 { break; }
                offset = end + 2;
                decoded.extend_from_slice(&body[offset..offset+length]);
                offset += length + 2;
            }
            (status, decoded)
        } else { (status, body.to_vec()) }
    }).await.unwrap()
}

#[allow(
    clippy::result_large_err,
    reason = "the handshake callback uses tungstenite's response error type"
)]
async fn accept(listener: &TcpListener) -> WebSocketStream<TcpStream> {
    let (stream, _) = timeout(Duration::from_secs(10), listener.accept())
        .await
        .unwrap()
        .unwrap();
    accept_hdr_async(
        stream,
        |request: &tokio_tungstenite::tungstenite::handshake::server::Request,
         mut response: tokio_tungstenite::tungstenite::handshake::server::Response| {
            assert_eq!(
                request.headers()["authorization"],
                "Bearer carrier-test-credential"
            );
            assert_eq!(request.headers()["sec-websocket-protocol"], "eip.v1");
            response
                .headers_mut()
                .insert("sec-websocket-protocol", "eip.v1".parse().unwrap());
            Ok(response)
        },
    )
    .await
    .unwrap()
}

async fn ws_control(socket: &mut WebSocketStream<TcpStream>, request: Value) -> Value {
    socket
        .send(Message::Text(request.to_string().into()))
        .await
        .unwrap();
    timeout(Duration::from_secs(10), async {
        loop {
            match socket.next().await.unwrap().unwrap() {
                Message::Text(payload) => {
                    let response: Value = serde_json::from_str(payload.as_str()).unwrap();
                    assert_eq!(response.get("eip_session"), request.get("eip_session"));
                    return response;
                }
                Message::Ping(bytes) => socket.send(Message::Pong(bytes)).await.unwrap(),
                message => panic!("unexpected control message: {message:?}"),
            }
        }
    })
    .await
    .unwrap()
}

#[tokio::test]
async fn http_sessions_are_not_tcp_affine_and_close_is_scoped() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    drop(listener);
    let device = DeviceProcess::start("http", address, json!({}));
    timeout(Duration::from_secs(10), async {
        while TcpStream::connect(address).await.is_err() {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let descriptor = result(http_control(address, request(None, "initialize", initialize())).await)
        ["descriptor"]
        .clone();
    let (alpha, beta) = tokio::join!(
        http_control(
            address,
            request(
                None,
                "session.open",
                open_params(&descriptor, &device.path("alpha"))
            )
        ),
        http_control(
            address,
            request(
                None,
                "session.open",
                open_params(&descriptor, &device.path("beta"))
            )
        )
    );
    let alpha = result(alpha)["descriptor"]["session_id"]
        .as_str()
        .unwrap()
        .to_owned();
    let beta = result(beta)["descriptor"]["session_id"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_ne!(alpha, beta);
    for session in [&alpha, &beta] {
        result(
            http_control(
                address,
                request(Some(session), "environment.readiness", context("ready")),
            )
            .await,
        );
    }
    let path = format!("{}/http-upload.bin", device.path("beta"));
    let mut params = context("open-writer");
    params["path"] = json!({"path":path});
    params["mode"] = json!("create");
    let writer = result(
        http_control(address, request(Some(&beta), "file.open_writer", params)).await,
    )["writer"]
        .as_str()
        .unwrap()
        .to_owned();
    let headers = format!(
        "EIP-Session: {beta}\r\nEIP-Transfer-Handle: {writer}\r\nEIP-Transfer-Direction: write\r\n"
    );
    let content = b"http\0binary\xff";
    let uploaded = http_request(
        address,
        "/eip/transfer",
        "application/octet-stream",
        &headers,
        content,
    )
    .await;
    assert_eq!(uploaded.0, 204);
    result(http_control(address, request(Some(&alpha), "session.close", json!({}))).await);
    use sha2::{Digest, Sha256};
    let digest = format!("{:x}", Sha256::digest(content));
    let mut params = context("commit");
    params["writer"] = json!(writer);
    params["transferred_bytes"] = json!(content.len());
    params["transfer_digest"] = json!({"algorithm":"sha256", "value":digest});
    result(http_control(address, request(Some(&beta), "file.commit_writer", params)).await);
    assert_eq!(
        std::fs::read(device.root.join("beta/http-upload.bin")).unwrap(),
        content
    );
    let mut params = context("open-reader");
    params["path"] = json!({"path":path});
    let reader = result(
        http_control(address, request(Some(&beta), "file.open_reader", params)).await,
    )["reader"]
        .as_str()
        .unwrap()
        .to_owned();
    let headers = format!(
        "EIP-Session: {beta}\r\nEIP-Transfer-Handle: {reader}\r\nEIP-Transfer-Direction: read\r\n"
    );
    let downloaded = http_request(
        address,
        "/eip/transfer",
        "application/octet-stream",
        &headers,
        &[],
    )
    .await;
    assert_eq!(downloaded.0, 200);
    assert_eq!(downloaded.1, content);
    let mut params = context("close-reader");
    params["reader"] = json!(reader);
    let closed =
        result(http_control(address, request(Some(&beta), "file.close_reader", params)).await);
    assert_eq!(closed["completion"]["digest"]["value"], digest);
    let expired = http_control(
        address,
        request(Some(&alpha), "session.keepalive", json!({})),
    )
    .await;
    assert!(expired.get("error").is_some());
    result(
        http_control(
            address,
            request(Some(&beta), "session.keepalive", json!({})),
        )
        .await,
    );
    result(http_control(address, request(None, "initialize", initialize())).await);
    let described = result(
        http_control(
            address,
            request(Some(&beta), "environment.describe", context("describe")),
        )
        .await,
    );
    assert_eq!(
        described["descriptor"]["working_directory"],
        device.path("beta")
    );
    result(http_control(address, request(Some(&beta), "session.close", json!({}))).await);
    device.stop().await;
}

#[tokio::test]
async fn reverse_websocket_reattaches_sessions_within_disconnect_grace() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let device = DeviceProcess::start(
        "reverse_websocket",
        listener.local_addr().unwrap(),
        json!({"idle_timeout_ms":10000, "disconnect_grace_ms":1500}),
    );
    let mut socket = accept(&listener).await;
    let descriptor = result(
        ws_control(&mut socket, request(None, "initialize", initialize())).await,
    )["descriptor"]
        .clone();
    let opened = result(
        ws_control(
            &mut socket,
            request(
                None,
                "session.open",
                open_params(&descriptor, &device.path("alpha")),
            ),
        )
        .await,
    );
    let session = opened["descriptor"]["session_id"]
        .as_str()
        .unwrap()
        .to_owned();
    result(
        ws_control(
            &mut socket,
            request(Some(&session), "environment.readiness", context("ready")),
        )
        .await,
    );
    socket.close(None).await.unwrap();
    drop(socket);
    let mut socket = accept(&listener).await;
    let reconnected =
        result(ws_control(&mut socket, request(None, "initialize", initialize())).await);
    assert_eq!(
        reconnected["descriptor"]["generation"],
        descriptor["generation"]
    );
    let attached = result(
        ws_control(
            &mut socket,
            request(Some(&session), "session.attach", json!({})),
        )
        .await,
    );
    assert_eq!(attached["descriptor"], opened["descriptor"]);
    result(
        ws_control(
            &mut socket,
            request(Some(&session), "session.keepalive", json!({})),
        )
        .await,
    );
    device.stop().await;
}

async fn ws_send_frame(
    socket: &mut WebSocketStream<TcpStream>,
    session: &str,
    handle: &str,
    kind: a13n_envd::eip::DataFrameKind,
    offset: u64,
    payload: &[u8],
) {
    let frame = a13n_envd::eip::DataFrame {
        session_id: session.to_owned(),
        handle: handle.to_owned(),
        kind,
        offset,
        payload: payload.to_vec(),
        reset_status: None,
    };
    socket
        .send(Message::Binary(
            a13n_envd::eip::encode_data_frame(&frame, 1024 * 1024)
                .unwrap()
                .into(),
        ))
        .await
        .unwrap();
}

async fn ws_receive_frame(socket: &mut WebSocketStream<TcpStream>) -> a13n_envd::eip::DataFrame {
    timeout(Duration::from_secs(10), async {
        loop {
            match socket.next().await.unwrap().unwrap() {
                Message::Binary(bytes) => {
                    return a13n_envd::eip::decode_data_frame(&bytes, 1024 * 1024).unwrap();
                }
                Message::Ping(bytes) => socket.send(Message::Pong(bytes)).await.unwrap(),
                message => panic!("unexpected data message: {message:?}"),
            }
        }
    })
    .await
    .unwrap()
}

#[tokio::test]
async fn reverse_websocket_binary_transfer_survives_sibling_close_and_rejects_cross_session_handle()
{
    use a13n_envd::eip::DataFrameKind;
    use sha2::{Digest, Sha256};
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let device = DeviceProcess::start(
        "reverse_websocket",
        listener.local_addr().unwrap(),
        json!({}),
    );
    let mut socket = accept(&listener).await;
    let descriptor = result(
        ws_control(&mut socket, request(None, "initialize", initialize())).await,
    )["descriptor"]
        .clone();
    let mut sessions = Vec::new();
    for folder in ["alpha", "beta"] {
        let opened = result(
            ws_control(
                &mut socket,
                request(
                    None,
                    "session.open",
                    open_params(&descriptor, &device.path(folder)),
                ),
            )
            .await,
        );
        let session = opened["descriptor"]["session_id"]
            .as_str()
            .unwrap()
            .to_owned();
        result(
            ws_control(
                &mut socket,
                request(Some(&session), "environment.readiness", context("ready")),
            )
            .await,
        );
        sessions.push(session);
    }
    let (alpha, beta) = (&sessions[0], &sessions[1]);
    let path = format!("{}/upload.bin", device.path("beta"));
    let mut params = context("writer");
    params["path"] = json!({"path":path});
    params["mode"] = json!("create");
    let opened =
        result(ws_control(&mut socket, request(Some(beta), "file.open_writer", params)).await);
    let writer = opened["writer"].as_str().unwrap();
    ws_send_frame(&mut socket, alpha, writer, DataFrameKind::Attach, 0, &[]).await;
    let denied = ws_receive_frame(&mut socket).await;
    assert_eq!(denied.kind, DataFrameKind::Reset);
    assert_eq!(denied.session_id, *alpha);
    ws_send_frame(&mut socket, beta, writer, DataFrameKind::Attach, 0, &[]).await;
    let attached = ws_receive_frame(&mut socket).await;
    assert_eq!(attached.kind, DataFrameKind::Attached);
    assert_eq!(attached.session_id, *beta);
    let content = b"binary\0payload\xff";
    ws_send_frame(&mut socket, beta, writer, DataFrameKind::Chunk, 0, content).await;
    let credit = ws_receive_frame(&mut socket).await;
    assert_eq!(credit.kind, DataFrameKind::Credit);
    assert_eq!(credit.offset, content.len() as u64);
    result(
        ws_control(
            &mut socket,
            request(Some(alpha), "session.close", json!({})),
        )
        .await,
    );
    ws_send_frame(
        &mut socket,
        beta,
        writer,
        DataFrameKind::End,
        content.len() as u64,
        &[],
    )
    .await;
    assert_eq!(
        ws_receive_frame(&mut socket).await.kind,
        DataFrameKind::EndAck
    );
    let digest = format!("{:x}", Sha256::digest(content));
    let mut commit = context("commit");
    commit["writer"] = json!(writer);
    commit["transferred_bytes"] = json!(content.len());
    commit["transfer_digest"] = json!({"algorithm":"sha256", "value":digest});
    result(
        ws_control(
            &mut socket,
            request(Some(beta), "file.commit_writer", commit),
        )
        .await,
    );
    assert_eq!(
        std::fs::read(device.root.join("beta/upload.bin")).unwrap(),
        content
    );
    let mut params = context("reader");
    params["path"] = json!({"path":path});
    let opened =
        result(ws_control(&mut socket, request(Some(beta), "file.open_reader", params)).await);
    let reader = opened["reader"].as_str().unwrap();
    ws_send_frame(&mut socket, beta, reader, DataFrameKind::Attach, 0, &[]).await;
    assert_eq!(
        ws_receive_frame(&mut socket).await.kind,
        DataFrameKind::Attached
    );
    let mut received = Vec::new();
    loop {
        let frame = ws_receive_frame(&mut socket).await;
        assert_eq!(frame.session_id, *beta);
        assert_eq!(frame.offset, received.len() as u64);
        if frame.kind == DataFrameKind::End {
            break;
        }
        assert_eq!(frame.kind, DataFrameKind::Chunk);
        received.extend(frame.payload);
        ws_send_frame(
            &mut socket,
            beta,
            reader,
            DataFrameKind::Credit,
            received.len() as u64,
            &[],
        )
        .await;
    }
    assert_eq!(received, content);
    let mut close = context("close-reader");
    close["reader"] = json!(reader);
    let closed =
        result(ws_control(&mut socket, request(Some(beta), "file.close_reader", close)).await);
    assert_eq!(closed["completion"]["digest"]["value"], digest);
    result(ws_control(&mut socket, request(Some(beta), "session.close", json!({}))).await);
    device.stop().await;
}
