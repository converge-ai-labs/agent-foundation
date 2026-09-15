use a13n::generated::{apis::identity_api::get_auth_context, models::*};
use a13n::{Client, Secret};
use serde::{Serialize, de::DeserializeOwned};
use serde_json::{Value, json};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};

fn fixtures() -> Value {
    serde_json::from_str(include_str!("../../fixtures/wire.json")).unwrap()
}
fn roundtrip<T: DeserializeOwned + Serialize>(values: &Value) {
    for value in values.as_array().unwrap() {
        let parsed: T = serde_json::from_value(value.clone()).unwrap();
        assert_eq!(serde_json::to_value(parsed).unwrap(), *value);
    }
}

#[test]
fn generated_wire_fixtures() {
    let fixture = fixtures();
    roundtrip::<UpdateAgentRequest>(&fixture["patch"]);
    roundtrip::<ActorRef>(&fixture["actor"]);
    roundtrip::<EnvironmentSelection>(&fixture["environment"]);
    roundtrip::<UserMessage>(&fixture["user_message"]);
    roundtrip::<CostLimit>(&fixture["cost"]);
    roundtrip::<RunStatus>(&fixture["run_status"]);
    roundtrip::<ConnectorCollection>(&fixture["null_cursor"]);
    let system: ActorRef = serde_json::from_value(fixture["actor"][2].clone()).unwrap();
    assert!(matches!(system, ActorRef::AnyOf1(_)));
    let new_env: EnvironmentSelection =
        serde_json::from_value(fixture["environment"][1].clone()).unwrap();
    assert!(matches!(new_env, EnvironmentSelection::AnyOf1(_)));
    let omitted: UpdateAgentRequest = serde_json::from_value(json!({})).unwrap();
    let null: UpdateAgentRequest = serde_json::from_value(json!({"name":null})).unwrap();
    assert_eq!(omitted.name, None);
    assert_eq!(null.name, Some(None));
    let secret: CreateWebProviderRequest = serde_json::from_value(
        json!({"type":"brave","name":"test","credential":{"api_key":"do-not-print"}}),
    )
    .unwrap();
    assert!(!format!("{secret:?}").contains("do-not-print"));
    assert_eq!(
        serde_json::to_value(secret).unwrap()["credential"],
        json!({"api_key":"do-not-print"})
    );
}

#[tokio::test]
async fn generated_http_uses_owner_pool_prefix_and_headers() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}/prefix", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let request = request_headers(&mut socket).await;
        assert!(request.starts_with("GET /prefix/api/v1/auth/context HTTP/1.1"));
        assert!(
            request
                .to_lowercase()
                .contains("authorization: bearer test-token")
        );
        let body = fixtures()["credential_context"].to_string();
        socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Request-ID: req_test\r\nETag: \"v1\"\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).as_bytes()).await.unwrap();
    });
    let client = Client::new(&base, Secret::new("test-token")).unwrap();
    let response = client
        .execute(async |api| get_auth_context(api).await)
        .await
        .unwrap();
    assert_eq!(response.data.workspace_id.as_deref(), Some("ws_example"));
    assert_eq!(response.headers["x-request-id"], "req_test");
    assert_eq!(response.headers["etag"], "\"v1\"");
    server.await.unwrap();
    client.close();
    assert!(matches!(
        client
            .execute(async |api| get_auth_context(api).await)
            .await,
        Err(a13n::CallError::Closed)
    ));
}

#[test]
fn boolean_constants_are_booleans_not_strings() {
    let value = json!({"enabled": true, "permission": "inherit"});
    let parsed: ToolSelection = serde_json::from_value(value.clone()).unwrap();
    assert_eq!(serde_json::to_value(parsed).unwrap(), value);
    assert!(serde_json::from_value::<ToolSelection>(json!({"enabled": "true"})).is_err());
    let quota: model_connection_test_result::MayConsumeQuotaOrIncurCost =
        serde_json::from_value(json!(true)).unwrap();
    assert_eq!(serde_json::to_value(quota).unwrap(), json!(true));
}

async fn request_headers(socket: &mut tokio::net::TcpStream) -> String {
    let mut request = Vec::new();
    loop {
        let mut chunk = [0; 4096];
        let count = socket.read(&mut chunk).await.unwrap();
        assert_ne!(count, 0);
        request.extend_from_slice(&chunk[..count]);
        if request.windows(4).any(|v| v == b"\r\n\r\n") {
            break;
        }
    }
    String::from_utf8(request).unwrap()
}

#[tokio::test]
async fn generated_binary_upload_sets_the_declared_content_type() {
    use a13n::generated::apis::{
        Error,
        asset_management_api::{
            PostWorkspacesWorkspaceAssetsError, post_workspaces_workspace_assets,
        },
    };
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("target/test-upload.bin");
    std::fs::write(&path, b"binary body").unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut request = request_headers(&mut socket).await;
        let headers = request.split("\r\n\r\n").next().unwrap().to_lowercase();
        assert!(headers.contains("content-type: application/octet-stream"));
        assert!(headers.contains("idempotency-key: upload-test"));
        assert!(headers.contains("transfer-encoding: chunked"));
        // Drain the streamed upload before closing, otherwise unread bytes can
        // reset the TCP connection and discard the error response on Linux.
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            while !request.ends_with("\r\n0\r\n\r\n") {
                let mut chunk = [0; 4096];
                let count = socket.read(&mut chunk).await.unwrap();
                assert_ne!(count, 0, "upload ended before its final chunk");
                request.push_str(std::str::from_utf8(&chunk[..count]).unwrap());
            }
        })
        .await
        .expect("upload did not finish");
        assert!(request.contains("binary body"));
        let body = json!({"error":{"code":"test_error","message":"test", "details":{},"request_id":"req_upload"}}).to_string();
        socket.write_all(format!("HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nX-Request-ID: req_upload\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).as_bytes()).await.unwrap();
    });
    let client = Client::new(&base, Secret::new("test-token")).unwrap();
    let result = client
        .execute(async |api| {
            post_workspaces_workspace_assets(
                api,
                "ws_test",
                "test.bin",
                "upload-test",
                path.clone(),
                None,
            )
            .await
        })
        .await;
    server.await.unwrap();
    let Err(a13n::CallError::Operation(Error::ResponseError(response))) = result else {
        panic!("expected a typed HTTP error");
    };
    assert_eq!(response.status, reqwest::StatusCode::BAD_REQUEST);
    assert_eq!(response.headers["x-request-id"], "req_upload");
    assert!(response.content.contains("test_error"));
    assert!(matches!(
        response.entity,
        Some(PostWorkspacesWorkspaceAssetsError::Status400(_))
    ));
    std::fs::remove_file(path).unwrap();
}
