//! One-command, one-Host enrollment with installation-local saved Host profiles.

use std::{
    collections::BTreeSet,
    ffi::OsString,
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    time::Duration,
};

use bytes::Bytes;
use http_body_util::{BodyExt, Full, Limited};
use hyper::{Request, header};
use hyper_util::rt::TokioIo;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::{
    io::{AsyncRead, AsyncWrite},
    net::TcpStream,
    task::JoinSet,
};
use url::Url;

use crate::config::{Config, ConnectionBootstrap, ReverseWebSocketConfig, TransportConfig};

const PAIRING_TIMEOUT: Duration = Duration::from_secs(10 * 60);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);
const RESPONSE_LIMIT: usize = 64 * 1024;

pub(crate) const HELP: &str = "a13n-envd connect <URL or saved Host> [options]

Connect one envd process to one Harness UI Host.
The first connection asks you to approve a code in the Host's device settings.
Later connections reuse the saved identity and credential automatically.

Options:
  --host NAME                  Save the URL under a friendly Host name
  --instance NAME              Independent installation identity (default: default)
  --name NAME                  Device display name
  --state-dir PATH             Persistent instance configuration parent
  --ca-file PATH               Additional trusted TLS certificates
  --config PATH                Daemon JSON configuration
  --default-working-directory PATH
  --execution-uid UID           Non-root native execution account
  --execution-gid GID           Native primary group (paired with UID)
  --allow-sudo BOOL             Permit native privilege gains (default: true)
  --egress-mode MODE            Network: inherit, deny, or controlled
  --computer-use BOOL           Enable shared macOS/X11/Windows desktop observation and input (default: false)

Computer use checks desktop readiness before connecting (macOS permissions, X11 access or Windows interactive session).
Screenshots can be sent to the configured model and retained in conversation history.
Pairing and working directories do not grant or confine desktop authority.
Set A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS to change the 120000 ms wait.
Daemon settings also accept A13N_ENVD_CONFIG_JSON and scalar environment overrides.
Set A13N_ENVD_ALLOW_SUDO=false to disable sudo/setuid privilege gains on Linux.
Enabling sudo does not grant sudoers permission or configure passwordless access.
Set A13N_ENVD_FULL_CONTROL=1 to enable native shell execution without profiles.
Run separate envd processes to connect to different Hosts. No service is installed.";

#[derive(Default)]
struct Arguments {
    target: String,
    host: Option<String>,
    instance: String,
    name: Option<String>,
    state_directory: Option<PathBuf>,
    ca_file: Option<PathBuf>,
    daemon: Vec<OsString>,
}

impl Arguments {
    fn parse(arguments: Vec<OsString>) -> io::Result<Self> {
        let mut result = Self {
            instance: "default".to_owned(),
            ..Self::default()
        };
        let mut seen = BTreeSet::new();
        let mut arguments = arguments.into_iter();
        while let Some(argument) = arguments.next() {
            let flag = argument
                .to_str()
                .ok_or_else(|| invalid("arguments must be UTF-8"))?;
            if !flag.starts_with('-') {
                if !result.target.is_empty() {
                    return Err(invalid("connect accepts exactly one Host"));
                }
                result.target = flag.to_owned();
                continue;
            }
            if !seen.insert(flag.to_owned()) {
                return Err(invalid("duplicate connect option"));
            }
            let value = arguments
                .next()
                .ok_or_else(|| invalid("connect option requires a value"))?;
            match flag {
                "--host" => result.host = Some(text(value)?),
                "--instance" => result.instance = text(value)?,
                "--name" => result.name = Some(text(value)?),
                "--state-dir" => result.state_directory = Some(absolute(PathBuf::from(value))?),
                "--ca-file" => result.ca_file = Some(absolute(PathBuf::from(value))?),
                "--config" | "--default-working-directory" => {
                    result.daemon.push(argument);
                    result
                        .daemon
                        .push(absolute(PathBuf::from(value))?.into_os_string());
                }
                "--allow-sudo" | "--execution-uid" | "--execution-gid" | "--egress-mode"
                | "--computer-use" => {
                    result.daemon.push(argument);
                    result.daemon.push(value);
                }
                _ => return Err(invalid("unknown connect option; use connect --help")),
            }
        }
        if result.target.is_empty() {
            return Err(invalid("connect requires a Host URL or saved Host name"));
        }
        validate_alias(&result.instance)?;
        if let Some(host) = &result.host {
            validate_alias(host)?;
        }
        Ok(result)
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct HostProfile {
    url: String,
    name: Option<String>,
    #[serde(default)]
    ca_file: Option<PathBuf>,
    #[serde(default)]
    registration: Option<Registration>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Registration {
    resource_id: String,
    websocket_url: String,
}

#[derive(Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
enum PairingResponse {
    Pending {
        challenge: Challenge,
        approval_url: Option<String>,
        poll_after_seconds: u64,
    },
    Approved {
        resource_id: String,
        websocket_url: String,
    },
}

#[derive(Deserialize)]
struct Challenge {
    verification_code: String,
}

pub(crate) async fn prepare(
    arguments: Vec<OsString>,
) -> Result<Config, Box<dyn std::error::Error + Send + Sync>> {
    let arguments = Arguments::parse(arguments)?;
    let state = arguments
        .state_directory
        .clone()
        .map(Ok)
        .unwrap_or_else(default_state_directory)?
        .join("instances")
        .join(&arguments.instance);
    let identity = crate::runtime::installation_identity(&state).map_err(io::Error::other)?;
    let target_url = if arguments.target.contains("://") {
        Some(host_url(&arguments.target)?)
    } else {
        validate_alias(&arguments.target)?;
        None
    };
    let alias = match (&arguments.host, &target_url) {
        (Some(alias), _) => alias.clone(),
        (None, Some(url)) => format!(
            "host-{}",
            &hex(&Sha256::digest(url.as_str().as_bytes()))[..16]
        ),
        (None, None) => arguments.target.clone(),
    };
    let directory = state.join("hosts").join(&alias);
    fs::create_dir_all(&directory)?;
    crate::runtime::protect_directory(&directory).map_err(io::Error::other)?;
    let profile_path = directory.join("host.json");
    let mut profile: HostProfile = match fs::read(&profile_path) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map_err(|_| invalid("saved Host configuration is invalid"))?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => HostProfile {
            url: target_url
                .as_ref()
                .ok_or_else(|| invalid("saved Host not found; connect using its URL first"))?
                .to_string(),
            ca_file: arguments.ca_file.clone(),
            name: None,
            registration: None,
        },
        Err(error) => return Err(error.into()),
    };
    if let Some(url) = &target_url
        && url.as_str() != profile.url
    {
        return Err(
            invalid("Host name already selects another URL; use a different Host name").into(),
        );
    }
    if arguments.ca_file.is_some() {
        profile.ca_file = arguments.ca_file;
    }
    let url = host_url(&profile.url)?;
    let credential_path = directory.join("credential");
    let credential = load_or_create_credential(&credential_path)?;
    // Save local identity and credential before the first request. Repeating an
    // interrupted enrollment therefore names the same pending/approved pairing.
    let placeholder = websocket_url(&url, "/api/envd/connect")?;
    let mut config = Config::for_connection(
        arguments.daemon,
        ConnectionBootstrap {
            device_id: identity,
            name: arguments.name.or_else(|| profile.name.clone()),
            runtime_directory: directory.join("runtime"),
            transport: ReverseWebSocketConfig {
                endpoint: placeholder.to_string(),
                credential_file: credential_path,
                tls_ca_file: profile.ca_file.clone(),
            },
        },
    )?;
    let name = config
        .display_name
        .as_deref()
        .unwrap_or(&arguments.instance);
    profile.name = Some(name.to_owned());
    save_profile(&profile_path, &profile)?;
    if profile.registration.is_none() {
        let pairing = pair(
            &url,
            &credential,
            &config.device_id,
            name,
            profile.ca_file.as_deref(),
        );
        let registration = tokio::select! {
            result = tokio::time::timeout(PAIRING_TIMEOUT, pairing) => {
                result.map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "pairing expired; run connect again"))??
            }
            _ = tokio::signal::ctrl_c() => return Err(io::Error::new(io::ErrorKind::Interrupted, "pairing cancelled; saved Host is retained").into()),
        };
        profile.registration = Some(registration);
        save_profile(&profile_path, &profile)?;
    }
    let registration = profile.registration.as_ref().expect("pairing completed");
    let endpoint = websocket_url(&url, &registration.websocket_url)?;
    if let TransportConfig::ReverseWebSocket(transport) = &mut config.transport {
        transport.endpoint = endpoint.to_string();
    }
    eprintln!(
        "Connecting to saved Host {alias}. Reuse it with: a13n-envd connect {alias} --instance {}",
        arguments.instance
    );
    Ok(config)
}

async fn pair(
    base: &Url,
    credential: &str,
    device_id: &str,
    name: &str,
    ca_file: Option<&Path>,
) -> io::Result<Registration> {
    let endpoint = base
        .join("api/envd/pair")
        .map_err(|_| invalid("invalid pairing endpoint"))?;
    let body = serde_json::to_vec(&serde_json::json!({"device_id": device_id, "name": name}))?;
    let tls = if base.scheme() == "https" {
        Some(crate::websocket::tls_client_config(ca_file)?)
    } else {
        None
    };
    let mut displayed_code = None;
    loop {
        let response = tokio::time::timeout(
            REQUEST_TIMEOUT,
            post(&endpoint, credential, &body, tls.clone()),
        )
        .await
        .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "Host pairing request timed out"))??;
        match serde_json::from_slice::<PairingResponse>(&response)
            .map_err(|_| invalid("Host returned an invalid pairing response"))?
        {
            PairingResponse::Approved {
                resource_id,
                websocket_url,
            } => {
                websocket_url_check(base, &websocket_url)?;
                return Ok(Registration {
                    resource_id,
                    websocket_url,
                });
            }
            PairingResponse::Pending {
                challenge,
                approval_url,
                poll_after_seconds,
            } => {
                if displayed_code.as_ref() != Some(&challenge.verification_code) {
                    if challenge.verification_code.len() > 32
                        || challenge.verification_code.chars().any(char::is_control)
                    {
                        return Err(invalid("Host returned an invalid verification code"));
                    }
                    eprintln!(
                        "Approve this envd in the Host's device settings. Verification code: {}",
                        challenge.verification_code
                    );
                    if let Some(link) = approval_url {
                        let approval = base
                            .join(&link)
                            .map_err(|_| invalid("invalid approval URL"))?;
                        if approval.origin() == base.origin() {
                            eprintln!("Open: {approval}");
                        }
                    }
                    displayed_code = Some(challenge.verification_code);
                }
                tokio::time::sleep(Duration::from_secs(poll_after_seconds.clamp(1, 10))).await;
            }
        }
    }
}

async fn post(
    url: &Url,
    credential: &str,
    body: &[u8],
    tls: Option<std::sync::Arc<rustls::ClientConfig>>,
) -> io::Result<Vec<u8>> {
    let host = match url
        .host()
        .ok_or_else(|| invalid("Host URL has no hostname"))?
    {
        url::Host::Domain(name) => name.to_owned(),
        url::Host::Ipv4(address) => address.to_string(),
        url::Host::Ipv6(address) => address.to_string(),
    };
    let stream =
        TcpStream::connect((host.as_str(), url.port_or_known_default().unwrap_or(443))).await?;
    if let Some(tls) = tls {
        let name = rustls::pki_types::ServerName::try_from(host)
            .map_err(|_| invalid("invalid TLS hostname"))?;
        let stream = tokio_rustls::TlsConnector::from(tls)
            .connect(name, stream)
            .await?;
        exchange(stream, url, credential, body).await
    } else {
        exchange(stream, url, credential, body).await
    }
}

async fn exchange<S: AsyncRead + AsyncWrite + Unpin + Send + 'static>(
    stream: S,
    url: &Url,
    credential: &str,
    body: &[u8],
) -> io::Result<Vec<u8>> {
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(stream))
        .await
        .map_err(io::Error::other)?;
    let mut tasks = JoinSet::new();
    tasks.spawn(connection);
    let mut authorization = hyper::header::HeaderValue::from_str(&format!("Bearer {credential}"))
        .map_err(|_| invalid("invalid saved credential"))?;
    authorization.set_sensitive(true);
    let request = Request::post(url.path())
        .header(
            header::HOST,
            &url[url::Position::BeforeHost..url::Position::AfterPort],
        )
        .header(header::AUTHORIZATION, authorization)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Full::new(Bytes::copy_from_slice(body)))
        .map_err(io::Error::other)?;
    let response = sender
        .send_request(request)
        .await
        .map_err(io::Error::other)?;
    if !response.status().is_success() {
        let status = response.status().as_u16();
        let kind = if matches!(status, 401 | 403 | 410) {
            io::ErrorKind::PermissionDenied
        } else {
            io::ErrorKind::ConnectionRefused
        };
        let detail = Limited::new(response.into_body(), 4096)
            .collect()
            .await
            .ok()
            .and_then(|body| pairing_error_detail(&body.to_bytes(), credential))
            .map(|detail| format!(": {detail}"))
            .unwrap_or_default();
        return Err(io::Error::new(
            kind,
            format!("Host rejected pairing (HTTP {status}){detail}; no credential was replaced"),
        ));
    }
    let bytes = Limited::new(response.into_body(), RESPONSE_LIMIT)
        .collect()
        .await
        .map_err(io::Error::other)?
        .to_bytes();
    Ok(bytes.to_vec())
}

fn pairing_error_detail(body: &[u8], credential: &str) -> Option<String> {
    let value: serde_json::Value = serde_json::from_slice(body).ok()?;
    let error = value.get("error")?;
    let code = error.get("code")?.as_str()?;
    let message = error.get("message")?.as_str()?;
    if code.is_empty()
        || code.len() > 128
        || !code
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        || message.len() > 512
        || message.chars().any(char::is_control)
    {
        return None;
    }
    Some(format!("{code}: {message}").replace(credential, "[redacted]"))
}

fn host_url(value: &str) -> io::Result<Url> {
    let mut url =
        Url::parse(value).map_err(|_| invalid("Host must be an HTTP(S) URL or saved name"))?;
    if !matches!(url.scheme(), "https" | "http")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid(
            "Host URL must be HTTP(S), without credentials, query or fragment",
        ));
    }
    if url.scheme() == "http" && !loopback(&url) {
        return Err(invalid(
            "remote Hosts require HTTPS; HTTP is supported only on loopback",
        ));
    }
    if !url.path().ends_with('/') {
        url.set_path(&format!("{}/", url.path()));
    }
    Ok(url)
}

fn loopback(url: &Url) -> bool {
    match url.host() {
        Some(url::Host::Domain("localhost")) => true,
        Some(url::Host::Ipv4(address)) => address.is_loopback(),
        Some(url::Host::Ipv6(address)) => address.is_loopback(),
        _ => false,
    }
}

fn websocket_url(base: &Url, value: &str) -> io::Result<Url> {
    let mut url = base
        .join(value)
        .map_err(|_| invalid("invalid Host connection URL"))?;
    match url.scheme() {
        "https" => {
            url.set_scheme("wss")
                .map_err(|_| invalid("invalid connection scheme"))?;
        }
        "http" => {
            url.set_scheme("ws")
                .map_err(|_| invalid("invalid connection scheme"))?;
        }
        _ => {}
    }
    let expected_scheme = if base.scheme() == "https" {
        "wss"
    } else {
        "ws"
    };
    if url.scheme() != expected_scheme
        || url.host_str() != base.host_str()
        || url.port_or_known_default() != base.port_or_known_default()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid(
            "connection URL must stay on the paired Host and retain TLS",
        ));
    }
    Ok(url)
}

fn websocket_url_check(base: &Url, value: &str) -> io::Result<()> {
    websocket_url(base, value).map(|_| ())
}

fn default_state_directory() -> io::Result<PathBuf> {
    if let Some(path) = std::env::var_os("A13N_ENVD_STATE_DIR") {
        return absolute(PathBuf::from(path));
    }
    #[cfg(windows)]
    let home = std::env::var_os("LOCALAPPDATA").or_else(|| std::env::var_os("USERPROFILE"));
    #[cfg(not(windows))]
    let home = std::env::var_os("HOME");
    absolute(
        PathBuf::from(
            home.ok_or_else(|| invalid("home directory is unavailable; pass --state-dir"))?,
        )
        .join(".a13n-envd"),
    )
}

fn load_or_create_credential(path: &Path) -> io::Result<String> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    match options.open(path) {
        Ok(mut file) => {
            let mut bytes = [0_u8; 32];
            getrandom::fill(&mut bytes)
                .map_err(|_| io::Error::other("cannot generate pairing credential"))?;
            let token = hex(&bytes);
            file.write_all(token.as_bytes())?;
            file.sync_all()?;
            Ok(token)
        }
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
            let token = fs::read_to_string(path)?;
            if token.len() != 64
                || !token
                    .bytes()
                    .all(|value| value.is_ascii_hexdigit() && !value.is_ascii_uppercase())
            {
                return Err(invalid(
                    "saved pairing credential is invalid; it was not replaced",
                ));
            }
            Ok(token)
        }
        Err(error) => Err(error),
    }
}

fn save_profile(path: &Path, profile: &HostProfile) -> io::Result<()> {
    let mut bytes = [0_u8; 8];
    getrandom::fill(&mut bytes)
        .map_err(|_| io::Error::other("cannot create Host configuration candidate"))?;
    let candidate = path.with_extension(format!("{}.tmp", hex(&bytes)));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)?;
        file.write_all(&serde_json::to_vec_pretty(profile)?)?;
        file.sync_all()?;
        fs::rename(&candidate, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(candidate);
    }
    result
}

fn validate_alias(value: &str) -> io::Result<()> {
    if value.is_empty()
        || value.len() > 64
        || matches!(value, "." | "..")
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return Err(invalid(
            "instance and Host names use 1..64 letters, digits, dot, dash or underscore",
        ));
    }
    Ok(())
}

fn absolute(path: PathBuf) -> io::Result<PathBuf> {
    Ok(if path.is_absolute() {
        path
    } else {
        std::env::current_dir()?.join(path)
    })
}

fn text(value: OsString) -> io::Result<String> {
    value
        .into_string()
        .map_err(|_| invalid("option must be UTF-8"))
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn targets_keep_credentials_off_urls_and_require_remote_tls() {
        for input in [
            "https://user:secret@example.com",
            "http://example.com",
            "https://example.com/?token=x",
        ] {
            assert!(host_url(input).is_err());
        }
        for input in [
            "https://example.com",
            "http://127.0.0.1:9876",
            "http://[::1]:9876",
        ] {
            assert!(host_url(input).is_ok());
        }
        let base = host_url("https://example.com").unwrap();
        assert!(websocket_url(&base, "/api/devices/device-test/connect").is_ok());
        assert!(websocket_url(&base, "ws://example.com/connect").is_err());
        assert!(websocket_url(&base, "wss://other.example/connect").is_err());
    }

    #[test]
    fn pairing_errors_are_bounded_structured_and_safe_for_terminals() {
        assert_eq!(
            pairing_error_detail(
                br#"{"error":{"code":"host_rejected","message":"Use the public origin."}}"#,
                "secret"
            ),
            Some("host_rejected: Use the public origin.".into())
        );
        assert_eq!(
            pairing_error_detail(
                br#"{"error":{"code":"denied","message":"secret"}}"#,
                "secret"
            ),
            Some("denied: [redacted]".into())
        );
        assert!(pairing_error_detail(b"<html>proxy failure</html>", "secret").is_none());
        assert!(
            pairing_error_detail(
                br#"{"error":{"code":"bad","message":"\u001b[31m"}}"#,
                "secret"
            )
            .is_none()
        );
        let oversized = serde_json::json!({"error": {"code": "bad", "message": "x".repeat(513)}});
        assert!(pairing_error_detail(&serde_json::to_vec(&oversized).unwrap(), "secret").is_none());
    }

    #[test]
    fn one_process_selects_exactly_one_host() {
        let parse = |values: &[&str]| Arguments::parse(values.iter().map(OsString::from).collect());
        let arguments = parse(&[
            "https://example.com",
            "--host",
            "team",
            "--instance",
            "work",
        ])
        .unwrap();
        assert_eq!(arguments.host.as_deref(), Some("team"));
        assert_eq!(arguments.instance, "work");
        assert!(parse(&["one", "two"]).is_err());
        assert!(parse(&["one", "--instance", "../escape"]).is_err());
    }
}
