use crate::web::*;
use reqwest::{Method, Url, header};
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use std::{fmt, sync::Mutex, time::Duration};
use tokio_util::sync::CancellationToken;

#[derive(Debug)]
pub struct ApiError {
    pub status: u16,
    pub code: String,
    pub message: String,
    pub details: Value,
    pub request_id: Option<String>,
    pub retry_after: Option<String>,
}
#[derive(Debug)]
pub enum Error {
    Api(Box<ApiError>),
    Transport,
    Protocol,
    InvalidInput,
    Closed,
}
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Api(error) => write!(f, "{}: {} ({})", error.code, error.message, error.status),
            Self::Transport => {
                f.write_str("Service transport failed; mutation outcome may be unknown")
            }
            Self::Protocol => f.write_str("Invalid or oversized Service response"),
            Self::InvalidInput => {
                f.write_str("Invalid Service URL, resource identifier, or precondition")
            }
            Self::Closed => f.write_str("Client is closed"),
        }
    }
}
impl std::error::Error for Error {}

/// A generated call failed, or its owning client was closed.
#[derive(Debug)]
pub enum CallError<E> {
    Closed,
    Operation(E),
}
impl<E: fmt::Display> fmt::Display for CallError<E> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Closed => f.write_str("Client is closed"),
            Self::Operation(error) => error.fmt(f),
        }
    }
}
impl<E: std::error::Error + 'static> std::error::Error for CallError<E> {}

/// Owns a bounded bearer transport. Drop a request future to cancel that request;
/// close() cancels all local requests and releases the pool without changing Runs.
pub struct Client {
    base_url: Url,
    http: Mutex<Option<reqwest::Client>>,
    shutdown: CancellationToken,
}
impl Client {
    pub fn new(base_url: &str, token: Secret) -> Result<Self, Error> {
        let mut base_url = Url::parse(base_url).map_err(|_| Error::InvalidInput)?;
        if !matches!(base_url.scheme(), "http" | "https")
            || base_url.host_str().is_none()
            || !base_url.username().is_empty()
            || base_url.password().is_some()
            || base_url.query().is_some()
            || base_url.fragment().is_some()
        {
            return Err(Error::InvalidInput);
        }
        let path = format!("{}/api/v1/", base_url.path().trim_end_matches('/'));
        base_url.set_path(&path);
        let mut authorization = header::HeaderValue::from_str(&format!("Bearer {}", token.0))
            .map_err(|_| Error::InvalidInput)?;
        authorization.set_sensitive(true);
        let mut headers = header::HeaderMap::new();
        headers.insert(header::AUTHORIZATION, authorization);
        let http = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .no_proxy()
            .build()
            .map_err(|_| Error::Transport)?;
        Ok(Self {
            base_url,
            http: Mutex::new(Some(http)),
            shutdown: CancellationToken::new(),
        })
    }
    /// Execute generated operations with the same pool, authentication and
    /// cancellation as the Search facade. For binary responses, consume the
    /// body inside the async closure so close() also cancels stream delivery.
    pub async fn execute<T, E>(
        &self,
        operation: impl AsyncFnOnce(
            &crate::generated::apis::configuration::Configuration,
        ) -> Result<T, E>,
    ) -> Result<T, CallError<E>> {
        let http = self
            .http
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .clone()
            .ok_or(CallError::Closed)?;
        let configuration = crate::generated::apis::configuration::Configuration {
            base_path: self
                .base_url
                .as_str()
                .trim_end_matches("/api/v1/")
                .to_owned(),
            client: http,
            user_agent: None,
            basic_auth: None,
            oauth_access_token: None,
            bearer_access_token: None,
            api_key: None,
        };
        tokio::select! {
            biased;
            _ = self.shutdown.cancelled() => Err(CallError::Closed),
            result = operation(&configuration) => result.map_err(CallError::Operation),
        }
    }

    pub fn close(&self) {
        self.shutdown.cancel();
        self.http
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .take();
    }
    fn url(&self, segments: &[&str]) -> Result<Url, Error> {
        if segments
            .iter()
            .any(|value| value.is_empty() || *value == "." || *value == "..")
        {
            return Err(Error::InvalidInput);
        }
        let mut url = self.base_url.clone();
        url.path_segments_mut()
            .map_err(|_| Error::InvalidInput)?
            .pop_if_empty()
            .extend(segments);
        Ok(url)
    }
    async fn request<T: DeserializeOwned>(
        &self,
        method: Method,
        segments: &[&str],
        body: Option<Value>,
        etag: Option<&str>,
        query: Option<&WebProviderListOptions>,
    ) -> Result<Representation<T>, Error> {
        let http = self
            .http
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .clone()
            .ok_or(Error::Closed)?;
        let mut builder = http.request(method, self.url(segments)?);
        if let Some(body) = body {
            builder = builder.json(&body);
        }
        if let Some(etag) = etag {
            if etag.is_empty() || etag.starts_with("W/") {
                return Err(Error::InvalidInput);
            }
            builder = builder.header(header::IF_MATCH, etag);
        }
        if let Some(query) = query {
            builder = builder.query(query);
        }
        let operation = async {
            let mut response = builder.send().await.map_err(|_| Error::Transport)?;
            let status = response.status();
            let text_header = |name: &str| {
                response
                    .headers()
                    .get(name)
                    .and_then(|value| value.to_str().ok())
                    .map(str::to_owned)
            };
            let etag = text_header("ETag");
            let request_id = text_header("X-Request-ID");
            let retry_after = text_header("Retry-After");
            let mut raw = Vec::new();
            while let Some(chunk) = response.chunk().await.map_err(|_| Error::Transport)? {
                if raw.len() + chunk.len() > 1_048_576 {
                    return Err(Error::Protocol);
                }
                raw.extend_from_slice(&chunk);
            }
            let value: Value = serde_json::from_slice(&raw).map_err(|_| Error::Protocol)?;
            if !status.is_success() {
                let error = &value["error"];
                return Err(Error::Api(Box::new(ApiError {
                    status: status.as_u16(),
                    code: error["code"].as_str().unwrap_or("http_error").to_owned(),
                    message: error["message"]
                        .as_str()
                        .unwrap_or("Service request failed")
                        .to_owned(),
                    details: error["details"]
                        .as_object()
                        .cloned()
                        .map(Value::Object)
                        .unwrap_or(json!({})),
                    request_id: error["request_id"]
                        .as_str()
                        .map(str::to_owned)
                        .or(request_id),
                    retry_after,
                })));
            }
            Ok(Representation {
                value: serde_json::from_value(value).map_err(|_| Error::Protocol)?,
                etag,
                request_id,
            })
        };
        tokio::select! { biased; _ = self.shutdown.cancelled() => Err(Error::Closed), result = operation => result }
    }
    /// Resolve the API key's Workspace once and share this client's transport.
    pub async fn workspace(&self) -> Result<crate::WorkspaceClient<'_>, Error> {
        #[derive(serde::Deserialize)]
        struct CredentialContext {
            workspace_id: Option<String>,
        }
        let result: Representation<CredentialContext> = self
            .request(Method::GET, &["auth", "context"], None, None, None)
            .await?;
        let id = result
            .value
            .workspace_id
            .filter(|id| !id.is_empty())
            .ok_or(Error::InvalidInput)?;
        Ok(crate::WorkspaceClient::new(self, id))
    }

    pub async fn web_provider_types(
        &self,
    ) -> Result<Representation<Page<WebProviderDefinition>>, Error> {
        self.request(Method::GET, &["web-provider-types"], None, None, None)
            .await
    }
    pub async fn web_provider_type(
        &self,
        provider_type: &str,
    ) -> Result<Representation<WebProviderDefinition>, Error> {
        self.request(
            Method::GET,
            &["web-provider-types", provider_type],
            None,
            None,
            None,
        )
        .await
    }
    pub async fn web_providers(
        &self,
        scope: &WebProviderScope,
        options: &WebProviderListOptions,
    ) -> Result<Representation<Page<WebProvider>>, Error> {
        self.request(Method::GET, &scope.segments(), None, None, Some(options))
            .await
    }
    pub async fn web_provider(
        &self,
        scope: &WebProviderScope,
        provider_id: &str,
    ) -> Result<Representation<WebProvider>, Error> {
        let mut path = scope.segments().to_vec();
        path.push(provider_id);
        self.request(Method::GET, &path, None, None, None).await
    }
    pub async fn create_web_provider(
        &self,
        scope: &WebProviderScope,
        request: &CreateWebProviderRequest,
    ) -> Result<Representation<WebProvider>, Error> {
        let mut body = serde_json::to_value(request).map_err(|_| Error::InvalidInput)?;
        body["credential"] = Value::String(request.credential.0.clone());
        self.request(Method::POST, &scope.segments(), Some(body), None, None)
            .await
    }
    pub async fn update_web_provider(
        &self,
        scope: &WebProviderScope,
        provider_id: &str,
        etag: &str,
        request: &UpdateWebProviderRequest,
    ) -> Result<Representation<WebProvider>, Error> {
        let mut body = serde_json::to_value(request).map_err(|_| Error::InvalidInput)?;
        if let Some(credential) = &request.credential {
            body["credential"] = Value::String(credential.0.clone());
        }
        let mut path = scope.segments().to_vec();
        path.push(provider_id);
        self.request(Method::PATCH, &path, Some(body), Some(etag), None)
            .await
    }
    pub async fn test_web_provider(
        &self,
        scope: &WebProviderScope,
        provider_id: &str,
    ) -> Result<Representation<WebProviderTestResult>, Error> {
        let mut path = scope.segments().to_vec();
        path.extend([provider_id, "test"]);
        self.request(Method::POST, &path, Some(json!({})), None, None)
            .await
    }
    pub async fn web_provider_references(
        &self,
        scope: &WebProviderScope,
        provider_id: &str,
        options: &WebProviderListOptions,
    ) -> Result<Representation<Page<WebProviderReference>>, Error> {
        let mut path = scope.segments().to_vec();
        path.extend([provider_id, "references"]);
        let options = WebProviderListOptions {
            cursor: options.cursor.clone(),
            limit: options.limit,
            ..Default::default()
        };
        self.request(Method::GET, &path, None, None, Some(&options))
            .await
    }
}
impl Drop for Client {
    fn drop(&mut self) {
        self.close();
    }
}
