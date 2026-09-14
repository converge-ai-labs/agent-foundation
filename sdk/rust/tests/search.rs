use a13n::*;
use serde_json::{Value, json};
use std::sync::Arc;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::mpsc,
    task::JoinHandle,
};

#[derive(Clone, Debug)]
struct Request {
    method: String,
    target: String,
    headers: String,
    body: Value,
}
struct Server {
    url: String,
    requests: mpsc::UnboundedReceiver<Request>,
    task: JoinHandle<()>,
}
impl Drop for Server {
    fn drop(&mut self) {
        self.task.abort();
    }
}
async fn server(handler: impl Fn(&Request) -> Option<(u16, Value)> + Send + 'static) -> Server {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/prefix", listener.local_addr().unwrap());
    let (sender, requests) = mpsc::unbounded_channel();
    let task = tokio::spawn(async move {
        while let Ok((mut socket, _)) = listener.accept().await {
            let mut raw = Vec::new();
            let header_end = loop {
                let mut chunk = [0; 4096];
                let count = socket.read(&mut chunk).await.unwrap();
                if count == 0 {
                    return;
                }
                raw.extend_from_slice(&chunk[..count]);
                if let Some(index) = raw.windows(4).position(|window| window == b"\r\n\r\n") {
                    break index + 4;
                }
            };
            let headers = String::from_utf8(raw[..header_end].to_vec())
                .unwrap()
                .to_lowercase();
            let length = headers
                .lines()
                .find_map(|line| line.strip_prefix("content-length: "))
                .map(|value| value.parse::<usize>().unwrap())
                .unwrap_or(0);
            while raw.len() < header_end + length {
                let mut chunk = [0; 4096];
                let count = socket.read(&mut chunk).await.unwrap();
                if count == 0 {
                    break;
                }
                raw.extend_from_slice(&chunk[..count]);
            }
            let line = String::from_utf8(raw[..header_end].to_vec()).unwrap();
            let mut parts = line.split_whitespace();
            let request = Request {
                method: parts.next().unwrap().into(),
                target: parts.next().unwrap().into(),
                headers,
                body: if length == 0 {
                    Value::Null
                } else {
                    serde_json::from_slice(&raw[header_end..]).unwrap()
                },
            };
            sender.send(request.clone()).unwrap();
            if let Some((status, body)) = handler(&request) {
                let body = body.to_string();
                let head = format!(
                    "HTTP/1.1 {status} response\r\nContent-Type: application/json\r\nContent-Length: {}\r\nETag: \"v1\"\r\nX-Request-ID: req_test\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = socket.write_all(head.as_bytes()).await;
                let _ = socket.write_all(body.as_bytes()).await;
            }
        }
    });
    Server {
        url,
        requests,
        task,
    }
}
fn provider() -> Value {
    json!({"id":"sp_test","organization_id":"org_test","workspace_id":"ws_test","type":"brave","name":"Research","configuration":{},"enabled":true,"credential_configured":true,"created_at":"2026-09-09T00:00:00Z","updated_at":"2026-09-09T00:00:00Z","created_by":{"principal_type":"user","principal_id":"user_test"},"updated_by":{"principal_type":"user","principal_id":"user_test"},"credential":"unexpected-secret"})
}
fn scope() -> SearchScope {
    SearchScope::Workspace("ws_test".into())
}

#[test]
fn search_configuration_is_lossless_and_tri_state() {
    for value in [
        json!({}),
        json!({"search":null}),
        json!({"model":{"model_key":"research"},"search":{"provider_id":"sp_test"}}),
    ] {
        let config: AgentRunOverride = serde_json::from_value(value.clone()).unwrap();
        assert_eq!(serde_json::to_value(config).unwrap(), value);
    }
    let config = AgentRunOverride {
        search: Optional::Null,
        ..Default::default()
    };
    assert_eq!(
        serde_json::to_value(config).unwrap(),
        json!({"search":null})
    );
    let request = CreateSearchProviderRequest::new("brave", "Research", Secret::new("test-secret"));
    assert!(
        !format!("{request:?} {}", serde_json::to_string(&request).unwrap())
            .contains("test-secret")
    );
}

#[tokio::test]
async fn account_crud_preserves_scope_etags_and_write_only_credentials() {
    let mut server = server(|_| Some((200, provider()))).await;
    let client = Client::new(&server.url, Secret::new("service-token")).unwrap();
    let request = CreateSearchProviderRequest::new("brave", "Research", Secret::new("test-secret"));
    let result = client
        .create_search_provider(&scope(), &request)
        .await
        .unwrap();
    assert_eq!(result.etag.as_deref(), Some("\"v1\""));
    assert_eq!(result.request_id.as_deref(), Some("req_test"));
    assert!(!format!("{result:?}").contains("unexpected-secret"));
    let sent = server.requests.recv().await.unwrap();
    assert_eq!(sent.method, "POST");
    assert_eq!(sent.body["credential"], "test-secret");
    assert!(sent.headers.contains("authorization: bearer service-token"));
    client
        .update_search_provider(
            &scope(),
            "sp_test",
            "\"v1\"",
            &UpdateSearchProviderRequest {
                enabled: Some(false),
                ..Default::default()
            },
        )
        .await
        .unwrap();
    let sent = server.requests.recv().await.unwrap();
    assert_eq!(sent.method, "PATCH");
    assert_eq!(sent.body, json!({"enabled":false}));
    assert!(sent.headers.contains("if-match: \"v1\""));
    client
        .search_provider(&SearchScope::Organization("org_test".into()), "sp_test")
        .await
        .unwrap();
    assert_eq!(
        server.requests.recv().await.unwrap().target,
        "/prefix/api/v1/organizations/org_test/search-providers/sp_test"
    );
    client.close();
    assert!(matches!(
        client.search_provider(&scope(), "sp_test").await,
        Err(Error::Closed)
    ));
}

#[tokio::test]
async fn catalog_pages_references_and_probe_match_native_contract() {
    let mut server = server(|request| {
  let value = if request.target.ends_with("/test") { assert_eq!(request.body, json!({})); json!({"success":true,"code":null,"checked_at":"2026-09-09T00:00:00Z"}) }
  else if request.target.contains("/references") { json!({"items":[{"agent_id":"agent_test","agent_revision_id":"rev_test","version":1,"is_current":true}]}) }
  else if request.target.contains("search-provider-types") {
   let kind = json!({"type":"brave","display_name":"Brave","configuration_schema":{},"credential_schema":{"writeOnly":true},"credential_required":true,"setup_url":"https://example.com"});
   if request.target.ends_with("/brave") { kind } else { json!({"items":[kind]}) }
  } else { assert!(request.target.contains("cursor=next")); json!({"items":[provider()],"next_cursor":"later"}) };
  Some((200, value))
 }).await;
    let client = Client::new(&server.url, Secret::new("token")).unwrap();
    assert_eq!(
        client.search_provider_types().await.unwrap().value.items[0].provider_type,
        "brave"
    );
    assert_eq!(
        client
            .search_provider_type("brave")
            .await
            .unwrap()
            .value
            .provider_type,
        "brave"
    );
    assert_eq!(
        client
            .search_providers(
                &scope(),
                &SearchListOptions {
                    cursor: Some("next".into()),
                    ..Default::default()
                }
            )
            .await
            .unwrap()
            .value
            .next_cursor
            .as_deref(),
        Some("later")
    );
    assert!(
        client
            .search_provider_references(&scope(), "sp_test", &SearchListOptions::default())
            .await
            .unwrap()
            .value
            .items[0]
            .is_current
    );
    assert!(
        client
            .test_search_provider(&scope(), "sp_test")
            .await
            .unwrap()
            .value
            .success
    );
    for _ in 0..5 {
        server.requests.try_recv().unwrap();
    }
    assert!(server.requests.try_recv().is_err());
}

#[tokio::test]
async fn uncertain_mutations_are_not_replayed() {
    let mut server = server(|_| None).await;
    let client = Client::new(&server.url, Secret::new("token")).unwrap();
    assert!(matches!(
        client
            .create_search_provider(
                &scope(),
                &CreateSearchProviderRequest::new("exa", "Research", Secret::new("test-secret"))
            )
            .await,
        Err(Error::Transport)
    ));
    assert!(matches!(
        client
            .update_search_provider(
                &scope(),
                "sp_test",
                "\"v1\"",
                &UpdateSearchProviderRequest {
                    credential: Some(Secret::new("test-secret")),
                    ..Default::default()
                }
            )
            .await,
        Err(Error::Transport)
    ));
    assert!(matches!(
        client.test_search_provider(&scope(), "sp_test").await,
        Err(Error::Transport)
    ));
    for _ in 0..3 {
        server.requests.try_recv().unwrap();
    }
    assert!(server.requests.try_recv().is_err());
}

#[tokio::test]
async fn errors_are_safe_and_responses_bounded() {
    for (status, body) in [
        (
            412,
            json!({"error":{"code":"precondition_failed","message":"Changed","request_id":"req_test"}}),
        ),
        (200, json!("x".repeat(1_048_577))),
    ] {
        let body = body.clone();
        let server = server(move |_| Some((status, body.clone()))).await;
        let client = Client::new(&server.url, Secret::new("token")).unwrap();
        let error = client
            .search_provider(&scope(), "sp_test")
            .await
            .unwrap_err();
        if status == 412 {
            let Error::Api(error) = error else {
                panic!("Expected API error")
            };
            assert_eq!(error.status, 412);
            assert_eq!(error.request_id.as_deref(), Some("req_test"));
        } else {
            assert!(matches!(error, Error::Protocol));
        }
    }
}

#[tokio::test]
async fn close_cancels_an_inflight_request() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let client = Arc::new(
        Client::new(
            &format!("http://{}", listener.local_addr().unwrap()),
            Secret::new("token"),
        )
        .unwrap(),
    );
    let caller = client.clone();
    let task = tokio::spawn(async move { caller.test_search_provider(&scope(), "sp_test").await });
    let (_socket, _) = listener.accept().await.unwrap();
    client.close();
    assert!(matches!(task.await.unwrap(), Err(Error::Closed)));
}

#[tokio::test]
async fn workspace_binding_resolves_once_and_shares_shutdown() {
    let mut server = server(|request| {
        Some((
            200,
            if request.target.ends_with("/auth/context") {
                json!({"workspace_id":"ws_test", "workspace_key":"renamed"})
            } else {
                json!({"items":[provider()],"next_cursor":null})
            },
        ))
    })
    .await;
    let client = Client::new(&server.url, Secret::new("token")).unwrap();
    let workspace = client.workspace().await.unwrap();
    assert_eq!(
        server.requests.recv().await.unwrap().target,
        "/prefix/api/v1/auth/context"
    );
    for _ in 0..2 {
        assert_eq!(
            workspace
                .search_providers(&SearchListOptions::default())
                .await
                .unwrap()
                .value
                .items[0]
                .id,
            "sp_test"
        );
        assert_eq!(
            server.requests.recv().await.unwrap().target,
            "/prefix/api/v1/workspaces/ws_test/search-providers"
        );
    }
    client.close();
    assert!(matches!(
        workspace
            .search_providers(&SearchListOptions::default())
            .await,
        Err(Error::Closed)
    ));
}

#[tokio::test]
async fn workspace_binding_requires_workspace_credential() {
    for context in [
        json!({}),
        json!({"workspace_id":null}),
        json!({"workspace_id":""}),
    ] {
        let server = server(move |_| Some((200, context.clone()))).await;
        let client = Client::new(&server.url, Secret::new("token")).unwrap();
        assert!(matches!(client.workspace().await, Err(Error::InvalidInput)));
    }
}
