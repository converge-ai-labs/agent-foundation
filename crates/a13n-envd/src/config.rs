use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    error::Error,
    fmt, fs,
    path::{Path, PathBuf},
    time::Duration,
};

use serde::{Deserialize, Serialize};

use crate::{eip::EIPLimits, runtime::RuntimeState};

const DEFAULT_MAX_REQUEST_BYTES: u64 = 16 * 1024 * 1024;
const DEFAULT_MAX_RESPONSE_BYTES: u64 = 16 * 1024 * 1024;
const DEFAULT_MAX_CONCURRENT_OPERATIONS: u64 = 128;
const DEFAULT_MAX_TRANSFER_FRAME_BYTES: u64 = 4 * 1024 * 1024;
const DEFAULT_MAX_CONCURRENT_FILE_TRANSFERS: u64 = 64;
const DEFAULT_SESSION_IDLE_TTL_MS: u64 = 30 * 60 * 1000;
const INITIALIZATION_TIMEOUT: Duration = Duration::from_secs(30);
const DEFAULT_MAX_COMMAND_ARGUMENTS: usize = 1024;
const DEFAULT_MAX_COMMAND_ARGUMENT_BYTES: usize = 1024 * 1024;
const DEFAULT_MAX_COMMAND_ENVIRONMENT_ENTRIES: usize = 1024;
const DEFAULT_MAX_COMMAND_ENVIRONMENT_BYTES: usize = 2 * 1024 * 1024;

const KNOWN_ENVIRONMENT_VARIABLES: &[&str] = &[
    "A13N_ENVD_CONFIG_JSON",
    "A13N_ENVD_ALLOW_SUDO",
    "A13N_ENVD_EXECUTION_UID",
    "A13N_ENVD_EXECUTION_GID",
    "A13N_ENVD_EGRESS_ENABLED",
    "A13N_ENVD_NAME",
    "A13N_ENVD_DESCRIPTION",
    "A13N_ENVD_IDLE_TIMEOUT_MS",
    "A13N_ENVD_DISCONNECT_GRACE_MS",
    "A13N_ENVD_TRANSPORT",
    "A13N_ENVD_HTTP_BIND",
    "A13N_ENVD_HTTP_CREDENTIAL_FILE",
    "A13N_ENVD_HTTP_TLS_CERT_FILE",
    "A13N_ENVD_HTTP_TLS_KEY_FILE",
    "A13N_ENVD_HTTP_PLAINTEXT_SCOPE",
    "A13N_ENVD_REVERSE_WS_URL",
    "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE",
    "A13N_ENVD_REVERSE_WS_CA_FILE",
    "A13N_ENVD_DEVICE_ID",
    "A13N_ENVD_RUNTIME_DIR",
    "A13N_ENVD_STATE_DIR",
    "A13N_ENVD_DEFAULT_WORKING_DIRECTORY",
    "A13N_ENVD_DIRECTORY_DISCOVERY",
    "A13N_ENVD_FULL_CONTROL",
];

const HTTP_VARIABLES: &[&str] = &[
    "A13N_ENVD_HTTP_BIND",
    "A13N_ENVD_HTTP_CREDENTIAL_FILE",
    "A13N_ENVD_HTTP_TLS_CERT_FILE",
    "A13N_ENVD_HTTP_TLS_KEY_FILE",
    "A13N_ENVD_HTTP_PLAINTEXT_SCOPE",
];
const REVERSE_WEBSOCKET_VARIABLES: &[&str] = &[
    "A13N_ENVD_REVERSE_WS_URL",
    "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE",
    "A13N_ENVD_REVERSE_WS_CA_FILE",
];

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct TrustedShellProfileConfig {
    pub(crate) profile_id: String,
    pub(crate) display_name: String,
    pub(crate) native_executable: PathBuf,
    #[serde(default)]
    pub(crate) fixed_arguments: Vec<String>,
    #[serde(default)]
    pub(crate) safe_base_environment: BTreeMap<String, String>,
    pub(crate) executable_search_roots: Vec<PathBuf>,
    pub(crate) max_script_bytes: u64,
    #[serde(default)]
    pub(crate) allow_login_mode: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub(crate) struct CommandConfig {
    pub(crate) full_control: bool,
    pub(crate) base_environment: BTreeMap<String, String>,
    pub(crate) trusted_executable_roots: Vec<PathBuf>,
    pub(crate) shell_profiles: Vec<TrustedShellProfileConfig>,
    pub(crate) max_arguments: usize,
    pub(crate) max_argument_bytes: usize,
    pub(crate) max_environment_entries: usize,
    pub(crate) max_environment_bytes: usize,
}

#[derive(Debug, Clone, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct EgressConfig {
    pub(crate) enabled: bool,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileConfig {
    #[serde(default)]
    execution: crate::execution::Options,
    #[serde(default)]
    egress: EgressConfig,
    device_id: Option<String>,
    name: Option<String>,
    description: Option<String>,
    default_working_directory: Option<PathBuf>,
    installation_state_directory: Option<PathBuf>,
    directory_discovery: Option<bool>,
    full_control: Option<bool>,
    idle_timeout_ms: Option<u64>,
    disconnect_grace_ms: Option<u64>,
    #[serde(default)]
    limits: DaemonLimits,
    #[serde(default)]
    trusted_executable_roots: Vec<PathBuf>,
    #[serde(default)]
    shell_profiles: Vec<TrustedShellProfileConfig>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct DaemonLimits {
    pub(crate) max_device_spool_bytes: u64,
    pub(crate) max_device_spool_objects: u64,
    pub(crate) max_device_staged_file_bytes: u64,
    pub(crate) max_device_staged_file_objects: u64,
    pub(crate) max_device_concurrent_operations: u64,
    pub(crate) max_device_processes: u64,
    pub(crate) max_device_operation_records: u64,
    pub(crate) max_device_process_records: u64,
    pub(crate) max_device_file_transfers: u64,
    pub(crate) max_device_file_transfer_records: u64,
    pub(crate) max_sessions: usize,
    pub(crate) max_file_bytes: u64,
    pub(crate) max_request_bytes: u64,
    pub(crate) max_response_bytes: u64,
    pub(crate) max_concurrent_operations: u64,
    pub(crate) max_processes: u64,
    pub(crate) max_operation_duration_ms: u64,
    pub(crate) max_output_preview_bytes: u64,
    pub(crate) max_output_bytes_per_stream: u64,
    pub(crate) max_spool_bytes: u64,
    pub(crate) max_spool_objects: u64,
    pub(crate) max_operation_records: u64,
    pub(crate) operation_record_ttl_ms: u64,
    pub(crate) max_process_records: u64,
    pub(crate) max_transfer_frame_bytes: u64,
    pub(crate) max_concurrent_file_transfers: u64,
    pub(crate) max_file_transfer_records: u64,
    pub(crate) file_transfer_record_ttl_ms: u64,
    pub(crate) max_staged_file_bytes: u64,
    pub(crate) max_staged_file_objects: u64,
    pub(crate) file_transfer_idle_ttl_ms: u64,
    pub(crate) max_file_transfer_duration_ms: u64,
}

impl Default for DaemonLimits {
    fn default() -> Self {
        default_limits()
    }
}

impl DaemonLimits {
    pub(crate) fn descriptor(&self) -> EIPLimits {
        EIPLimits {
            max_file_bytes: self.max_file_bytes,
            max_request_bytes: self.max_request_bytes,
            max_response_bytes: self.max_response_bytes,
            max_concurrent_operations: self.max_concurrent_operations,
            max_processes: self.max_processes,
            max_operation_duration_ms: self.max_operation_duration_ms,
            max_output_preview_bytes: self.max_output_preview_bytes,
            max_output_bytes_per_stream: self.max_output_bytes_per_stream,
            max_transfer_frame_bytes: self.max_transfer_frame_bytes,
            max_concurrent_file_transfers: self.max_concurrent_file_transfers,
            max_file_transfer_bytes: self.max_staged_file_bytes,
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub(crate) enum TransportConfig {
    Stdio,
    Http(HttpConfig),
    ReverseWebSocket(ReverseWebSocketConfig),
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub(crate) struct HttpConfig {
    pub(crate) bind: std::net::SocketAddr,
    pub(crate) credential_file: PathBuf,
    pub(crate) tls_certificate_file: Option<PathBuf>,
    pub(crate) tls_private_key_file: Option<PathBuf>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub(crate) struct ReverseWebSocketConfig {
    pub(crate) endpoint: String,
    pub(crate) credential_file: PathBuf,
    pub(crate) tls_ca_file: Option<PathBuf>,
}

pub(crate) struct ConnectionBootstrap {
    pub(crate) device_id: String,
    pub(crate) name: Option<String>,
    pub(crate) runtime_directory: PathBuf,
    pub(crate) transport: ReverseWebSocketConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub(crate) struct Config {
    #[serde(skip)]
    pub(crate) managed: bool,
    pub(crate) execution: Option<crate::execution::Identity>,
    pub(crate) allow_sudo: bool,
    pub(crate) egress: EgressConfig,
    #[cfg_attr(
        not(target_os = "linux"),
        allow(
            dead_code,
            reason = "only Linux Session isolation masks bootstrap files"
        )
    )]
    pub(crate) bootstrap_files: Vec<PathBuf>,
    pub(crate) device_id: String,
    pub(crate) default_working_directory: String,
    pub(crate) directory_discovery: bool,
    pub(crate) display_name: Option<String>,
    pub(crate) description: Option<String>,
    pub(crate) transport: TransportConfig,
    pub(crate) limits: DaemonLimits,
    pub(crate) initialization_timeout: Duration,
    pub(crate) session_idle_timeout: Duration,
    pub(crate) disconnect_grace: Duration,
    pub(crate) command: Option<CommandConfig>,
    #[serde(skip)]
    pub(crate) runtime: Option<RuntimeState>,
}

impl Config {
    pub(crate) fn from_environment() -> Result<Self, ConfigError> {
        reject_unknown_environment_variables()?;
        Self::from_arguments(StartupArguments::parse(env::args_os().skip(1))?, None)
    }

    pub(crate) fn for_connection(
        arguments: Vec<std::ffi::OsString>,
        connection: ConnectionBootstrap,
    ) -> Result<Self, ConfigError> {
        reject_unknown_environment_variables()?;
        let mut arguments = StartupArguments::parse(arguments)?;
        arguments.device_id = Some(connection.device_id.clone());
        if connection.name.is_some() {
            arguments.name = connection.name.clone();
        }
        Self::from_arguments(arguments, Some(connection))
    }

    fn from_arguments(
        arguments: StartupArguments,
        connection: Option<ConnectionBootstrap>,
    ) -> Result<Self, ConfigError> {
        let bootstrap_files = arguments
            .config
            .iter()
            .map(fs::canonicalize)
            .collect::<Result<Vec<_>, _>>()
            .map_err(|_| ConfigError::new("cannot resolve configuration file"))?;
        let mut value = load_file_config(arguments.config.clone())?;
        if let Some(json) = optional_unicode("A13N_ENVD_CONFIG_JSON")? {
            let overlay = parse_config_json(json.as_bytes(), "A13N_ENVD_CONFIG_JSON")?;
            merge_config(&mut value, overlay);
        }
        apply_environment(&mut value)?;
        let mut file: FileConfig = serde_json::from_value(value)
            .map_err(|error| ConfigError::new(format!("invalid merged configuration: {error}")))?;
        arguments.apply(&mut file)?;

        let transport_name =
            optional_unicode("A13N_ENVD_TRANSPORT")?.unwrap_or_else(|| "stdio".to_owned());
        let transport = if let Some(connection) = &connection {
            TransportConfig::ReverseWebSocket(connection.transport.clone())
        } else {
            match transport_name.as_str() {
                "stdio" => {
                    reject_transport_variables(HTTP_VARIABLES, "stdio")?;
                    reject_transport_variables(REVERSE_WEBSOCKET_VARIABLES, "stdio")?;
                    TransportConfig::Stdio
                }
                "http" => {
                    reject_transport_variables(REVERSE_WEBSOCKET_VARIABLES, "http")?;
                    TransportConfig::Http(parse_http_config()?)
                }
                "reverse_websocket" => {
                    reject_transport_variables(HTTP_VARIABLES, "reverse_websocket")?;
                    TransportConfig::ReverseWebSocket(parse_reverse_websocket_config(
                        required_unicode("A13N_ENVD_REVERSE_WS_URL")?,
                        PathBuf::from(required_unicode("A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE")?),
                        optional_unicode("A13N_ENVD_REVERSE_WS_CA_FILE")?.map(PathBuf::from),
                    )?)
                }
                _ => {
                    return Err(ConfigError::new(
                        "A13N_ENVD_TRANSPORT must be stdio, http, or reverse_websocket",
                    ));
                }
            }
        };

        let runtime_dir = match connection {
            Some(connection) => connection.runtime_directory,
            None => PathBuf::from(required_unicode("A13N_ENVD_RUNTIME_DIR")?),
        };
        if !runtime_dir.is_absolute() {
            return Err(ConfigError::new(
                "A13N_ENVD_RUNTIME_DIR must be an absolute path",
            ));
        }

        let device_id = match file.device_id {
            Some(identity) => identity,
            None => {
                let state_dir = file
                    .installation_state_directory
                    .unwrap_or_else(|| runtime_dir.with_extension("state"));
                crate::runtime::installation_identity(&state_dir).map_err(ConfigError::new)?
            }
        };
        validate_identity(&device_id, "device_id")?;
        let default_native = file
            .default_working_directory
            .map(Ok)
            .unwrap_or_else(env::current_dir)
            .map_err(|error| ConfigError::new(format!("cannot resolve startup cwd: {error}")))?;
        let default_native = fs::canonicalize(&default_native).unwrap_or(default_native);
        let default_working_directory =
            crate::device_path::from_native(&default_native).map_err(|_| {
                ConfigError::new("default_working_directory must be an absolute native path")
            })?;
        let directory_discovery = file.directory_discovery.unwrap_or(true);
        validate_presentation(file.name.as_deref(), "name", 256)?;
        validate_presentation(file.description.as_deref(), "description", 4096)?;

        let idle_timeout_ms = file.idle_timeout_ms.unwrap_or(DEFAULT_SESSION_IDLE_TTL_MS);
        let disconnect_grace_ms = file.disconnect_grace_ms.unwrap_or(30_000);
        if idle_timeout_ms == 0
            || disconnect_grace_ms == 0
            || disconnect_grace_ms >= idle_timeout_ms
        {
            return Err(ConfigError::new(
                "Session lifecycle durations must be positive and disconnect grace shorter than idle timeout",
            ));
        }
        let execution =
            crate::execution::resolve(file.execution.identity().map_err(ConfigError::new)?)
                .map_err(ConfigError::new)?;
        let allow_sudo = file.execution.allow_sudo().map_err(ConfigError::new)?;
        let limits = file.limits;
        validate_limits(&limits)?;
        let runtime = Some(RuntimeState::prepare(&runtime_dir).map_err(ConfigError::new)?);
        let full_control = file.full_control.unwrap_or(false);
        let command = if full_control {
            if !file.trusted_executable_roots.is_empty() || !file.shell_profiles.is_empty() {
                return Err(ConfigError::new(
                    "full_control cannot be combined with executable roots or shell profiles",
                ));
            }
            Some(full_control_commands()?)
        } else {
            prepare_command_config(file.trusted_executable_roots, file.shell_profiles)?
        };
        Ok(Self {
            managed: false,
            execution,
            allow_sudo,
            egress: file.egress,
            bootstrap_files,
            device_id,
            default_working_directory,
            directory_discovery,
            display_name: file.name,
            description: file.description,
            transport,
            initialization_timeout: INITIALIZATION_TIMEOUT,
            session_idle_timeout: Duration::from_millis(idle_timeout_ms),
            disconnect_grace: Duration::from_millis(disconnect_grace_ms),
            command,
            runtime,
            limits,
        })
    }

    #[cfg(test)]
    pub(crate) fn for_test(device_id: &str) -> Self {
        Self {
            managed: false,
            execution: None,
            allow_sudo: true,
            egress: EgressConfig::default(),
            bootstrap_files: Vec::new(),
            device_id: device_id.to_owned(),
            default_working_directory: crate::device_path::from_native(
                &env::current_dir().unwrap(),
            )
            .unwrap(),
            directory_discovery: true,
            display_name: None,
            description: None,
            transport: TransportConfig::Stdio,
            limits: default_limits(),
            initialization_timeout: Duration::from_millis(20),
            session_idle_timeout: Duration::from_secs(1),
            disconnect_grace: Duration::from_millis(100),
            command: None,
            runtime: None,
        }
    }
}

fn reject_transport_variables(names: &[&str], transport: &str) -> Result<(), ConfigError> {
    for name in names {
        if env::var_os(name).is_some() {
            return Err(ConfigError::new(format!(
                "{name} is not valid with {transport} transport"
            )));
        }
    }
    Ok(())
}

fn parse_http_config() -> Result<HttpConfig, ConfigError> {
    let bind_text = required_unicode("A13N_ENVD_HTTP_BIND")?;
    let bind = bind_text.parse::<std::net::SocketAddr>().map_err(|_| {
        ConfigError::new("A13N_ENVD_HTTP_BIND must be a numeric IP address and port")
    })?;
    let credential_file = PathBuf::from(required_unicode("A13N_ENVD_HTTP_CREDENTIAL_FILE")?);
    validate_bootstrap_file(&credential_file, "A13N_ENVD_HTTP_CREDENTIAL_FILE")?;
    let credential_file = fs::canonicalize(&credential_file).map_err(|error| {
        ConfigError::new(format!(
            "cannot canonicalize A13N_ENVD_HTTP_CREDENTIAL_FILE: {error}"
        ))
    })?;
    let certificate = optional_unicode("A13N_ENVD_HTTP_TLS_CERT_FILE")?.map(PathBuf::from);
    let private_key = optional_unicode("A13N_ENVD_HTTP_TLS_KEY_FILE")?.map(PathBuf::from);
    if certificate.is_some() != private_key.is_some() {
        return Err(ConfigError::new(
            "A13N_ENVD_HTTP_TLS_CERT_FILE and A13N_ENVD_HTTP_TLS_KEY_FILE must be configured together",
        ));
    }
    let (tls_certificate_file, tls_private_key_file) = match (certificate, private_key) {
        (Some(certificate), Some(private_key)) => {
            validate_bootstrap_file(&certificate, "A13N_ENVD_HTTP_TLS_CERT_FILE")?;
            validate_bootstrap_file(&private_key, "A13N_ENVD_HTTP_TLS_KEY_FILE")?;
            (
                Some(fs::canonicalize(certificate).map_err(|error| {
                    ConfigError::new(format!(
                        "cannot canonicalize A13N_ENVD_HTTP_TLS_CERT_FILE: {error}"
                    ))
                })?),
                Some(fs::canonicalize(private_key).map_err(|error| {
                    ConfigError::new(format!(
                        "cannot canonicalize A13N_ENVD_HTTP_TLS_KEY_FILE: {error}"
                    ))
                })?),
            )
        }
        (None, None) => {
            let scope = required_unicode("A13N_ENVD_HTTP_PLAINTEXT_SCOPE")?;
            if !matches!(scope.as_str(), "loopback" | "provider_private_link") {
                return Err(ConfigError::new(
                    "A13N_ENVD_HTTP_PLAINTEXT_SCOPE must be loopback or provider_private_link",
                ));
            }
            if scope == "loopback" && !bind.ip().is_loopback() {
                return Err(ConfigError::new(
                    "loopback HTTP plaintext scope requires a loopback bind address",
                ));
            }
            (None, None)
        }
        _ => unreachable!("paired TLS configuration checked above"),
    };
    if tls_certificate_file.is_some() && env::var_os("A13N_ENVD_HTTP_PLAINTEXT_SCOPE").is_some() {
        return Err(ConfigError::new(
            "A13N_ENVD_HTTP_PLAINTEXT_SCOPE is not valid with native HTTP TLS",
        ));
    }
    Ok(HttpConfig {
        bind,
        credential_file,
        tls_certificate_file,
        tls_private_key_file,
    })
}

fn parse_reverse_websocket_config(
    endpoint: String,
    credential_file: PathBuf,
    tls_ca_file: Option<PathBuf>,
) -> Result<ReverseWebSocketConfig, ConfigError> {
    let endpoint = url::Url::parse(&endpoint)
        .map_err(|_| ConfigError::new("A13N_ENVD_REVERSE_WS_URL must be a valid ws or wss URL"))?;
    if !matches!(endpoint.scheme(), "ws" | "wss")
        || endpoint.host_str().is_none()
        || !endpoint.username().is_empty()
        || endpoint.password().is_some()
        || endpoint.query().is_some()
        || endpoint.fragment().is_some()
    {
        return Err(ConfigError::new(
            "A13N_ENVD_REVERSE_WS_URL must be ws or wss with a host and without user info, query, or fragment",
        ));
    }
    validate_bootstrap_file(&credential_file, "A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE")?;
    let credential_file = fs::canonicalize(&credential_file).map_err(|error| {
        ConfigError::new(format!(
            "cannot canonicalize A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE: {error}"
        ))
    })?;
    let tls_ca_file = tls_ca_file
        .map(|path| {
            validate_bootstrap_file(&path, "A13N_ENVD_REVERSE_WS_CA_FILE")?;
            fs::canonicalize(&path).map_err(|error| {
                ConfigError::new(format!(
                    "cannot canonicalize A13N_ENVD_REVERSE_WS_CA_FILE: {error}"
                ))
            })
        })
        .transpose()?;
    Ok(ReverseWebSocketConfig {
        endpoint: endpoint.to_string(),
        credential_file,
        tls_ca_file,
    })
}

fn validate_bootstrap_file(path: &Path, name: &str) -> Result<(), ConfigError> {
    if !path.is_absolute() {
        return Err(ConfigError::new(format!("{name} must be an absolute path")));
    }
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| ConfigError::new(format!("cannot inspect {name}: {error}")))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(ConfigError::new(format!(
            "{name} must identify a regular file, not a symlink"
        )));
    }
    Ok(())
}

fn validate_limits(limits: &DaemonLimits) -> Result<(), ConfigError> {
    let values = [
        limits.max_sessions as u64,
        limits.max_file_bytes,
        limits.max_request_bytes,
        limits.max_response_bytes,
        limits.max_concurrent_operations,
        limits.max_processes,
        limits.max_operation_duration_ms,
        limits.max_output_preview_bytes,
        limits.max_output_bytes_per_stream,
        limits.max_spool_bytes,
        limits.max_spool_objects,
        limits.max_operation_records,
        limits.operation_record_ttl_ms,
        limits.max_process_records,
        limits.max_transfer_frame_bytes,
        limits.max_concurrent_file_transfers,
        limits.max_file_transfer_records,
        limits.file_transfer_record_ttl_ms,
        limits.max_staged_file_bytes,
        limits.max_staged_file_objects,
        limits.file_transfer_idle_ttl_ms,
        limits.max_file_transfer_duration_ms,
        limits.max_device_spool_bytes,
        limits.max_device_spool_objects,
        limits.max_device_staged_file_bytes,
        limits.max_device_staged_file_objects,
        limits.max_device_concurrent_operations,
        limits.max_device_processes,
        limits.max_device_operation_records,
        limits.max_device_process_records,
        limits.max_device_file_transfers,
        limits.max_device_file_transfer_records,
    ];
    if values
        .iter()
        .any(|value| *value == 0 || *value > (usize::MAX >> 3) as u64)
    {
        return Err(ConfigError::new(
            "resource limits must be positive and fit this platform",
        ));
    }
    for (session, device) in [
        (limits.max_spool_bytes, limits.max_device_spool_bytes),
        (limits.max_spool_objects, limits.max_device_spool_objects),
        (
            limits.max_staged_file_bytes,
            limits.max_device_staged_file_bytes,
        ),
        (
            limits.max_staged_file_objects,
            limits.max_device_staged_file_objects,
        ),
        (
            limits.max_concurrent_operations,
            limits.max_device_concurrent_operations,
        ),
        (limits.max_processes, limits.max_device_processes),
        (
            limits.max_operation_records,
            limits.max_device_operation_records,
        ),
        (
            limits.max_process_records,
            limits.max_device_process_records,
        ),
        (
            limits.max_concurrent_file_transfers,
            limits.max_device_file_transfers,
        ),
        (
            limits.max_file_transfer_records,
            limits.max_device_file_transfer_records,
        ),
    ] {
        if session > device {
            return Err(ConfigError::new(
                "Session capacity must not exceed Device capacity",
            ));
        }
    }
    if limits.max_process_records < limits.max_processes
        || limits.max_file_transfer_records < limits.max_concurrent_file_transfers
    {
        return Err(ConfigError::new(
            "record capacity must cover active resource capacity",
        ));
    }
    if limits.max_output_preview_bytes > limits.max_output_bytes_per_stream {
        return Err(ConfigError::new(
            "max_output_preview_bytes must not exceed max_output_bytes_per_stream",
        ));
    }
    let required_spool = limits
        .max_output_bytes_per_stream
        .checked_mul(2)
        .ok_or_else(|| ConfigError::new("max_output_bytes_per_stream is too large"))?;
    if limits.max_spool_bytes < required_spool {
        return Err(ConfigError::new(
            "max_spool_bytes must be at least twice max_output_bytes_per_stream",
        ));
    }
    Ok(())
}

fn prepare_command_config(
    trusted_roots: Vec<PathBuf>,
    shell_profiles: Vec<TrustedShellProfileConfig>,
) -> Result<Option<CommandConfig>, ConfigError> {
    if trusted_roots.is_empty() && shell_profiles.is_empty() {
        return Ok(None);
    }
    let mut canonical_roots = Vec::with_capacity(trusted_roots.len());
    for root in trusted_roots {
        canonical_roots.push(canonical_directory(&root, "trusted executable root")?);
    }
    canonical_roots.sort();
    canonical_roots.dedup();

    let mut profile_ids = BTreeSet::new();
    let mut prepared_profiles = Vec::with_capacity(shell_profiles.len());
    for mut profile in shell_profiles {
        if !valid_policy_id(&profile.profile_id) || !profile_ids.insert(profile.profile_id.clone())
        {
            return Err(ConfigError::new(
                "shell profile IDs must be unique and use 1..=128 ASCII letters, digits, dot, dash, or underscore",
            ));
        }
        if profile.display_name.trim() != profile.display_name
            || profile.display_name.is_empty()
            || profile.display_name.len() > 256
            || profile.display_name.chars().any(char::is_control)
        {
            return Err(ConfigError::new("shell profile display_name is invalid"));
        }
        if profile.max_script_bytes == 0 {
            return Err(ConfigError::new(
                "shell profile max_script_bytes must be positive",
            ));
        }
        validate_string_vector(&profile.fixed_arguments, "shell fixed arguments")?;
        validate_safe_environment(&profile.safe_base_environment)?;
        profile.native_executable =
            canonical_regular_file(&profile.native_executable, "shell native_executable")?;
        let mut roots = Vec::with_capacity(profile.executable_search_roots.len());
        for root in profile.executable_search_roots {
            roots.push(canonical_directory(&root, "shell executable search root")?);
        }
        roots.sort();
        roots.dedup();
        if roots.is_empty() {
            return Err(ConfigError::new(
                "shell profiles require at least one executable search root",
            ));
        }
        profile.executable_search_roots = roots;
        prepared_profiles.push(profile);
    }
    prepared_profiles.sort_by(|left, right| left.profile_id.cmp(&right.profile_id));

    Ok(Some(CommandConfig {
        full_control: false,
        base_environment: inherited_command_environment(),
        trusted_executable_roots: canonical_roots,
        shell_profiles: prepared_profiles,
        max_arguments: DEFAULT_MAX_COMMAND_ARGUMENTS,
        max_argument_bytes: DEFAULT_MAX_COMMAND_ARGUMENT_BYTES,
        max_environment_entries: DEFAULT_MAX_COMMAND_ENVIRONMENT_ENTRIES,
        max_environment_bytes: DEFAULT_MAX_COMMAND_ENVIRONMENT_BYTES,
    }))
}

fn full_control_commands() -> Result<CommandConfig, ConfigError> {
    let base_environment = inherited_command_environment();
    let cwd = env::current_dir().map_err(|error| ConfigError::new(error.to_string()))?;
    // PATH order is meaningful. Missing entries are normal and do not prevent startup.
    let roots: Vec<PathBuf> = env::split_paths(&env::var_os("PATH").unwrap_or_default())
        .map(|path| {
            if path.is_absolute() {
                path
            } else {
                cwd.join(path)
            }
        })
        .filter(|path| path.is_dir())
        .collect();
    #[cfg(unix)]
    let (executable, arguments, display_name) = (
        PathBuf::from("/bin/sh"),
        vec!["-c".to_owned()],
        "POSIX shell",
    );
    #[cfg(windows)]
    let (executable, arguments, display_name) = {
        let powershell = ["pwsh.exe", "powershell.exe"]
            .into_iter()
            .find_map(|name| {
                roots
                    .iter()
                    .map(|root| root.join(name))
                    .find(|path| path.is_file())
            })
            .or_else(|| {
                env::var_os("SystemRoot").map(|root| {
                    PathBuf::from(root).join("System32/WindowsPowerShell/v1.0/powershell.exe")
                })
            })
            .ok_or_else(|| ConfigError::new("Full Control requires PowerShell"))?;
        (
            powershell,
            vec!["-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
                .into_iter()
                .map(str::to_owned)
                .collect(),
            "PowerShell",
        )
    };
    let executable = canonical_regular_file(&executable, "Full Control shell")?;
    Ok(CommandConfig {
        full_control: true,
        base_environment,
        trusted_executable_roots: roots.clone(),
        shell_profiles: vec![TrustedShellProfileConfig {
            profile_id: "default".to_owned(),
            display_name: display_name.to_owned(),
            native_executable: executable,
            fixed_arguments: arguments,
            safe_base_environment: BTreeMap::new(),
            executable_search_roots: roots,
            max_script_bytes: DEFAULT_MAX_COMMAND_ARGUMENT_BYTES as u64,
            allow_login_mode: false,
        }],
        max_arguments: DEFAULT_MAX_COMMAND_ARGUMENTS,
        max_argument_bytes: DEFAULT_MAX_COMMAND_ARGUMENT_BYTES,
        max_environment_entries: DEFAULT_MAX_COMMAND_ENVIRONMENT_ENTRIES,
        max_environment_bytes: DEFAULT_MAX_COMMAND_ENVIRONMENT_BYTES,
    })
}

fn canonical_directory(path: &Path, label: &str) -> Result<PathBuf, ConfigError> {
    if !path.is_absolute() {
        return Err(ConfigError::new(format!("{label} must be absolute")));
    }
    let canonical = fs::canonicalize(path)
        .map_err(|error| ConfigError::new(format!("cannot canonicalize {label}: {error}")))?;
    if !fs::metadata(&canonical)
        .map_err(|error| ConfigError::new(format!("cannot inspect {label}: {error}")))?
        .is_dir()
    {
        return Err(ConfigError::new(format!("{label} must be a directory")));
    }
    Ok(canonical)
}

fn canonical_regular_file(path: &Path, label: &str) -> Result<PathBuf, ConfigError> {
    if !path.is_absolute() {
        return Err(ConfigError::new(format!("{label} must be absolute")));
    }
    let canonical = fs::canonicalize(path)
        .map_err(|error| ConfigError::new(format!("cannot canonicalize {label}: {error}")))?;
    if !fs::metadata(&canonical)
        .map_err(|error| ConfigError::new(format!("cannot inspect {label}: {error}")))?
        .is_file()
    {
        return Err(ConfigError::new(format!("{label} must be a regular file")));
    }
    Ok(canonical)
}

fn valid_policy_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
}

fn validate_string_vector(values: &[String], label: &str) -> Result<(), ConfigError> {
    if values.len() > DEFAULT_MAX_COMMAND_ARGUMENTS
        || values.iter().map(String::len).sum::<usize>() > DEFAULT_MAX_COMMAND_ARGUMENT_BYTES
        || values.iter().any(|value| value.contains('\0'))
    {
        return Err(ConfigError::new(format!(
            "{label} exceeds command safety limits"
        )));
    }
    Ok(())
}

fn validate_safe_environment(values: &BTreeMap<String, String>) -> Result<(), ConfigError> {
    if values.len() > DEFAULT_MAX_COMMAND_ENVIRONMENT_ENTRIES
        || values
            .iter()
            .map(|(name, value)| name.len().saturating_add(value.len()))
            .sum::<usize>()
            > DEFAULT_MAX_COMMAND_ENVIRONMENT_BYTES
    {
        return Err(ConfigError::new(
            "shell safe_base_environment exceeds command safety limits",
        ));
    }
    for (name, value) in values {
        if !valid_environment_name(name) || value.contains('\0') || reserved_environment_name(name)
        {
            return Err(ConfigError::new(
                "shell safe_base_environment contains an unsafe name or value",
            ));
        }
    }
    Ok(())
}

pub(crate) fn valid_environment_name(name: &str) -> bool {
    let mut bytes = name.bytes();
    matches!(bytes.next(), Some(b'A'..=b'Z' | b'a'..=b'z' | b'_'))
        && bytes.all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

pub(crate) fn reserved_environment_name(name: &str) -> bool {
    let upper = name.to_ascii_uppercase();
    upper.starts_with("A13N_ENVD_") || upper.starts_with("EIP_")
}

fn inherited_command_environment() -> BTreeMap<String, String> {
    env::vars_os()
        .filter_map(|(name, value)| Some((name.into_string().ok()?, value.into_string().ok()?)))
        .filter(|(name, value)| {
            valid_environment_name(name)
                && !reserved_environment_name(name)
                && !value.contains('\0')
        })
        .collect()
}

#[derive(Debug, Default)]
struct StartupArguments {
    config: Option<PathBuf>,
    default_working_directory: Option<PathBuf>,
    device_id: Option<String>,
    name: Option<String>,
    description: Option<String>,
    allow_sudo: Option<bool>,
    execution_uid: Option<u32>,
    execution_gid: Option<u32>,
    egress_enabled: Option<bool>,
}

impl StartupArguments {
    fn parse(arguments: impl IntoIterator<Item = std::ffi::OsString>) -> Result<Self, ConfigError> {
        let mut result = Self::default();
        let mut arguments = arguments.into_iter();
        let mut seen = BTreeSet::new();
        while let Some(flag) = arguments.next() {
            let flag = flag
                .into_string()
                .map_err(|_| ConfigError::new("argument must be UTF-8"))?;
            if !matches!(
                flag.as_str(),
                "--config"
                    | "--default-working-directory"
                    | "--device-id"
                    | "--name"
                    | "--description"
                    | "--allow-sudo"
                    | "--execution-uid"
                    | "--execution-gid"
                    | "--egress-enabled"
            ) {
                return Err(ConfigError::new(format!("unknown argument: {flag}")));
            }
            if !seen.insert(flag.clone()) {
                return Err(ConfigError::new(format!("duplicate argument: {flag}")));
            }
            let value = arguments
                .next()
                .ok_or_else(|| ConfigError::new(format!("{flag} requires a value")))?;
            if matches!(flag.as_str(), "--config" | "--default-working-directory") {
                let path = PathBuf::from(value);
                if !path.is_absolute() {
                    return Err(ConfigError::new(format!("{flag} path must be absolute")));
                }
                if flag == "--config" {
                    result.config = Some(path);
                } else {
                    result.default_working_directory = Some(path);
                }
                continue;
            }
            let value = value
                .into_string()
                .map_err(|_| ConfigError::new(format!("{flag} must be UTF-8")))?;
            match flag.as_str() {
                "--device-id" => result.device_id = Some(value),
                "--name" => result.name = Some(value),
                "--description" => result.description = Some(value),
                "--allow-sudo" => result.allow_sudo = Some(parse_bool(&value, &flag)?),
                "--egress-enabled" => result.egress_enabled = Some(parse_bool(&value, &flag)?),
                "--execution-uid" | "--execution-gid" => {
                    let value = value.parse().map_err(|_| {
                        ConfigError::new(format!("{flag} must be an unsigned 32-bit integer"))
                    })?;
                    if flag == "--execution-uid" {
                        result.execution_uid = Some(value);
                    } else {
                        result.execution_gid = Some(value);
                    }
                }
                _ => unreachable!(),
            }
        }
        Ok(result)
    }

    fn apply(self, file: &mut FileConfig) -> Result<(), ConfigError> {
        file.execution.allow_sudo = self.allow_sudo.or(file.execution.allow_sudo);
        file.execution.uid = self.execution_uid.or(file.execution.uid);
        file.execution.gid = self.execution_gid.or(file.execution.gid);
        if let Some(enabled) = self.egress_enabled {
            file.egress.enabled = enabled;
        }
        file.device_id = self.device_id.or(file.device_id.take());
        file.name = self.name.or(file.name.take());
        file.description = self.description.or(file.description.take());
        file.default_working_directory = self
            .default_working_directory
            .or(file.default_working_directory.take());
        Ok(())
    }
}

fn validate_identity(value: &str, field: &str) -> Result<(), ConfigError> {
    if value.trim() != value
        || value.is_empty()
        || value.len() > 256
        || value.chars().any(char::is_control)
    {
        return Err(ConfigError::new(format!(
            "{field} must be 1..=256 non-control bytes without surrounding whitespace"
        )));
    }
    Ok(())
}

fn validate_presentation(
    value: Option<&str>,
    field: &str,
    max_chars: usize,
) -> Result<(), ConfigError> {
    if let Some(value) = value
        && (value.chars().count() > max_chars || value.chars().any(char::is_control))
    {
        return Err(ConfigError::new(format!(
            "{field} must contain at most {max_chars} non-control characters"
        )));
    }
    Ok(())
}

fn load_file_config(path: Option<PathBuf>) -> Result<serde_json::Value, ConfigError> {
    let Some(path) = path else {
        return Ok(serde_json::json!({}));
    };
    let metadata = fs::symlink_metadata(&path)
        .map_err(|error| ConfigError::new(format!("cannot inspect config file: {error}")))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(ConfigError::new(
            "--config must identify a regular file, not a symlink",
        ));
    }
    let bytes = fs::read(&path)
        .map_err(|error| ConfigError::new(format!("cannot read config file: {error}")))?;
    parse_config_json(&bytes, "config file")
}

fn parse_config_json(bytes: &[u8], source: &str) -> Result<serde_json::Value, ConfigError> {
    let value: serde_json::Value = serde_json::from_slice(bytes)
        .map_err(|error| ConfigError::new(format!("invalid {source} JSON: {error}")))?;
    // Validate each source before overlaying it; an override must not hide typos.
    serde_json::from_value::<FileConfig>(value.clone())
        .map_err(|error| ConfigError::new(format!("invalid {source} JSON: {error}")))?;
    Ok(value)
}

/// Objects merge recursively; arrays and scalar values replace the lower layer.
fn merge_config(base: &mut serde_json::Value, overlay: serde_json::Value) {
    match (base, overlay) {
        (serde_json::Value::Object(base), serde_json::Value::Object(overlay)) => {
            for (key, value) in overlay {
                merge_config(base.entry(key).or_insert(serde_json::Value::Null), value);
            }
        }
        (base, overlay) => *base = overlay,
    }
}

fn parse_bool(value: &str, name: &str) -> Result<bool, ConfigError> {
    match value {
        "true" | "1" => Ok(true),
        "false" | "0" => Ok(false),
        _ => Err(ConfigError::new(format!(
            "{name} must be true, false, 1, or 0"
        ))),
    }
}

fn apply_environment(value: &mut serde_json::Value) -> Result<(), ConfigError> {
    for (name, field) in [
        ("A13N_ENVD_DEVICE_ID", "device_id"),
        ("A13N_ENVD_NAME", "name"),
        ("A13N_ENVD_DESCRIPTION", "description"),
        ("A13N_ENVD_STATE_DIR", "installation_state_directory"),
        (
            "A13N_ENVD_DEFAULT_WORKING_DIRECTORY",
            "default_working_directory",
        ),
    ] {
        if let Some(text) = optional_unicode(name)? {
            value[field] = text.into();
        }
    }
    for (name, path) in [
        ("A13N_ENVD_ALLOW_SUDO", &["execution", "allow_sudo"][..]),
        ("A13N_ENVD_EGRESS_ENABLED", &["egress", "enabled"][..]),
        (
            "A13N_ENVD_DIRECTORY_DISCOVERY",
            &["directory_discovery"][..],
        ),
        ("A13N_ENVD_FULL_CONTROL", &["full_control"][..]),
    ] {
        if let Some(text) = optional_unicode(name)? {
            set_config_field(value, path, parse_bool(&text, name)?.into());
        }
    }
    for (name, path) in [
        ("A13N_ENVD_EXECUTION_UID", &["execution", "uid"][..]),
        ("A13N_ENVD_EXECUTION_GID", &["execution", "gid"][..]),
        ("A13N_ENVD_IDLE_TIMEOUT_MS", &["idle_timeout_ms"][..]),
        (
            "A13N_ENVD_DISCONNECT_GRACE_MS",
            &["disconnect_grace_ms"][..],
        ),
    ] {
        if let Some(text) = optional_unicode(name)? {
            let number: u64 = text
                .parse()
                .map_err(|_| ConfigError::new(format!("{name} must be an unsigned integer")))?;
            set_config_field(value, path, number.into());
        }
    }
    Ok(())
}

fn set_config_field(value: &mut serde_json::Value, path: &[&str], field: serde_json::Value) {
    let mut target = value;
    for key in path {
        target = &mut target[*key];
    }
    *target = field;
}

fn default_limits() -> DaemonLimits {
    DaemonLimits {
        max_device_spool_bytes: 4 * 1024 * 1024 * 1024,
        max_device_spool_objects: 65_536,
        max_device_staged_file_bytes: 32 * 1024 * 1024 * 1024,
        max_device_staged_file_objects: 2048,
        max_device_concurrent_operations: 256,
        max_device_processes: 512,
        max_device_operation_records: 65_536,
        max_device_process_records: 4096,
        max_device_file_transfers: 128,
        max_device_file_transfer_records: 2048,
        max_sessions: 128,
        max_file_bytes: 8 * 1024 * 1024 * 1024,
        max_request_bytes: DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: DEFAULT_MAX_RESPONSE_BYTES,
        max_concurrent_operations: DEFAULT_MAX_CONCURRENT_OPERATIONS,
        max_processes: 128,
        max_operation_duration_ms: 24 * 60 * 60 * 1000,
        max_output_preview_bytes: 2 * 1024 * 1024,
        max_output_bytes_per_stream: 256 * 1024 * 1024,
        max_spool_bytes: 1024 * 1024 * 1024,
        max_spool_objects: 16_384,
        max_operation_records: 16_384,
        operation_record_ttl_ms: 24 * 60 * 60 * 1000,
        max_process_records: 1024,
        max_transfer_frame_bytes: DEFAULT_MAX_TRANSFER_FRAME_BYTES,
        max_concurrent_file_transfers: DEFAULT_MAX_CONCURRENT_FILE_TRANSFERS,
        max_file_transfer_records: 512,
        file_transfer_record_ttl_ms: 24 * 60 * 60 * 1000,
        max_staged_file_bytes: 8 * 1024 * 1024 * 1024,
        max_staged_file_objects: 512,
        file_transfer_idle_ttl_ms: 10 * 60 * 1000,
        max_file_transfer_duration_ms: 2 * 60 * 60 * 1000,
    }
}

fn reject_unknown_environment_variables() -> Result<(), ConfigError> {
    for (name, _) in env::vars_os() {
        let Some(name) = name.to_str() else {
            continue;
        };
        if name.starts_with("A13N_ENVD_") && !KNOWN_ENVIRONMENT_VARIABLES.contains(&name) {
            return Err(ConfigError::new(format!(
                "unknown a13n-envd environment variable: {name}"
            )));
        }
    }
    Ok(())
}

fn required_unicode(name: &str) -> Result<String, ConfigError> {
    optional_unicode(name)?.ok_or_else(|| ConfigError::new(format!("{name} is required")))
}

fn optional_unicode(name: &str) -> Result<Option<String>, ConfigError> {
    env::var_os(name)
        .map(|value| {
            value
                .into_string()
                .map_err(|_| ConfigError::new(format!("{name} must be valid UTF-8")))
        })
        .transpose()
}

#[derive(Debug)]
pub(crate) struct ConfigError {
    message: String,
}

impl ConfigError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for ConfigError {}

#[cfg(test)]
mod tests {
    use std::{fs, time::SystemTime};

    use crate::eip::EipValidate;

    use super::{
        Config, DEFAULT_MAX_COMMAND_ARGUMENT_BYTES, DEFAULT_MAX_COMMAND_ARGUMENTS,
        DEFAULT_MAX_COMMAND_ENVIRONMENT_BYTES, DEFAULT_MAX_COMMAND_ENVIRONMENT_ENTRIES,
        parse_reverse_websocket_config,
    };

    #[test]
    fn startup_booleans_accept_only_explicit_values() {
        for value in ["0", "false"] {
            assert!(!super::parse_bool(value, "flag").unwrap());
        }
        for value in ["1", "true"] {
            assert!(super::parse_bool(value, "flag").unwrap());
        }
        for value in ["", "yes", "False", " true", "2"] {
            assert!(super::parse_bool(value, "flag").is_err());
        }
    }

    #[test]
    fn startup_layers_merge_nested_fields_then_apply_cli() {
        use serde_json::json;
        let mut value = json!({
            "execution": {"uid":1000,"gid":1001,"allow_sudo":true},
            "limits": {"max_sessions":8, "max_processes":4},
            "trusted_executable_roots":["/bin"]
        });
        super::merge_config(
            &mut value,
            json!({
                "execution": {"allow_sudo":false},
                "limits": {"max_sessions":2},
                "trusted_executable_roots":["/usr/bin"]
            }),
        );
        super::set_config_field(&mut value, &["execution", "gid"], json!(1002));
        let mut file: super::FileConfig = serde_json::from_value(value).unwrap();
        assert_eq!(file.execution.uid, Some(1000));
        assert_eq!(file.execution.gid, Some(1002));
        assert_eq!(file.execution.allow_sudo, Some(false));
        assert_eq!(file.limits.max_sessions, 2);
        assert_eq!(file.limits.max_processes, 4);
        assert_eq!(
            file.trusted_executable_roots,
            [std::path::PathBuf::from("/usr/bin")]
        );
        super::StartupArguments::parse(
            ["--allow-sudo", "true", "--execution-gid", "1003"].map(Into::into),
        )
        .unwrap()
        .apply(&mut file)
        .unwrap();
        assert_eq!(file.execution.allow_sudo, Some(true));
        assert_eq!(file.execution.gid, Some(1003));
        assert_eq!(file.execution.uid, Some(1000));
    }

    #[test]
    fn json_environment_uses_the_strict_file_schema() {
        for text in [
            r#"[]"#,
            r#"null"#,
            r#"{"execution":{"allow_sudoo":false}}"#,
            r#"{"limits":{"max_sessions":"8"}}"#,
            r#"{"egress":{"enabled":1}}"#,
        ] {
            assert!(super::parse_config_json(text.as_bytes(), "environment").is_err());
        }
        let value =
            super::parse_config_json(br#"{"execution":{"allow_sudo":false}}"#, "environment")
                .unwrap();
        let file: super::FileConfig = serde_json::from_value(value).unwrap();
        assert_eq!(file.execution.allow_sudo, Some(false));
        assert!(file.execution.identity().unwrap().is_none());
        assert!(super::FileConfig::default().execution.allow_sudo().unwrap());
        assert!(!super::FileConfig::default().full_control.unwrap_or(false));
    }

    #[test]
    fn full_control_provides_a_native_shell_without_profile_configuration() {
        let commands = super::full_control_commands().unwrap();
        assert!(commands.full_control);
        assert_eq!(commands.shell_profiles.len(), 1);
        assert_eq!(commands.shell_profiles[0].profile_id, "default");
        assert!(commands.shell_profiles[0].native_executable.is_file());
        assert_eq!(
            commands.base_environment,
            super::inherited_command_environment()
        );
        assert!(
            commands
                .base_environment
                .keys()
                .all(|name| !super::reserved_environment_name(name))
        );
    }

    #[test]
    fn reverse_websocket_accepts_ws_and_wss_but_rejects_credential_bearing_urls() {
        let unique = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .expect("clock follows Unix epoch")
            .as_nanos();
        let directory = std::env::temp_dir().join(format!("a13n-envd-config-{unique}"));
        fs::create_dir(&directory).expect("creates fixture directory");
        let credential = directory.join("token");
        let ca = directory.join("ca.pem");
        fs::write(&credential, "token").expect("writes token fixture");
        fs::write(&ca, "certificate").expect("writes CA fixture");

        let plain = parse_reverse_websocket_config(
            "ws://127.0.0.1:8000/eip".to_owned(),
            credential.clone(),
            None,
        )
        .expect("ws endpoint is accepted");
        assert_eq!(plain.endpoint, "ws://127.0.0.1:8000/eip");
        let tls = parse_reverse_websocket_config(
            "wss://control.example/eip".to_owned(),
            credential.clone(),
            Some(ca),
        )
        .expect("wss endpoint is accepted");
        assert_eq!(tls.endpoint, "wss://control.example/eip");

        for endpoint in [
            "http://control.example/eip",
            "ws://token@control.example/eip",
            "wss://control.example/eip?token=secret",
            "wss://control.example/eip#fragment",
        ] {
            assert!(
                parse_reverse_websocket_config(endpoint.to_owned(), credential.clone(), None,)
                    .is_err(),
                "endpoint must be rejected: {endpoint}"
            );
        }
        let _ = fs::remove_dir_all(directory);
    }

    #[test]
    fn startup_arguments_override_device_metadata_and_default_working_directory() {
        let root = std::env::temp_dir();
        let mut file: super::FileConfig = serde_json::from_value(serde_json::json!({
            "device_id": "device-shared", "name": "Old"
        }))
        .unwrap();
        let args = [
            std::ffi::OsString::from("--default-working-directory"),
            root.clone().into_os_string(),
            "--device-id".into(),
            "env-build".into(),
            "--name".into(),
            "Build Linux".into(),
        ];
        super::StartupArguments::parse(args)
            .unwrap()
            .apply(&mut file)
            .unwrap();

        assert_eq!(file.device_id.as_deref(), Some("env-build"));
        assert_eq!(file.name.as_deref(), Some("Build Linux"));
        assert_eq!(file.default_working_directory, Some(root));
    }

    #[test]
    fn startup_arguments_reject_ambiguous_or_unbounded_configuration() {
        for args in [
            vec!["--default-working-directory"],
            vec!["--default-working-directory", "relative"],
            vec!["--name", "a", "--name", "b"],
            vec!["--unknown", "a"],
        ] {
            assert!(super::StartupArguments::parse(args.into_iter().map(Into::into)).is_err());
        }
        assert!(super::validate_identity(" device", "device_id").is_err());
        assert!(super::validate_presentation(Some("bad\nname"), "name", 256).is_err());
        assert!(super::validate_presentation(Some(&"a".repeat(257)), "name", 256).is_err());
    }

    #[test]
    fn test_configuration_has_finite_valid_limits() {
        let config = Config::for_test("env-test");

        config
            .limits
            .descriptor()
            .validate()
            .expect("limits are valid");
        assert_eq!(config.device_id, "env-test");
        assert_eq!(config.limits.max_request_bytes, 16 * 1024 * 1024);
        assert_eq!(config.limits.max_response_bytes, 16 * 1024 * 1024);
        assert_eq!(config.limits.max_processes, 128);
        assert_eq!(config.limits.max_operation_duration_ms, 24 * 60 * 60 * 1000);
        assert_eq!(config.limits.max_output_bytes_per_stream, 256 * 1024 * 1024);
        assert_eq!(config.limits.max_spool_bytes, 1024 * 1024 * 1024);
        assert_eq!(config.limits.max_staged_file_bytes, 8 * 1024 * 1024 * 1024);
        assert_eq!(DEFAULT_MAX_COMMAND_ARGUMENTS, 1024);
        assert_eq!(DEFAULT_MAX_COMMAND_ARGUMENT_BYTES, 1024 * 1024);
        assert_eq!(DEFAULT_MAX_COMMAND_ENVIRONMENT_ENTRIES, 1024);
        assert_eq!(DEFAULT_MAX_COMMAND_ENVIRONMENT_BYTES, 2 * 1024 * 1024);
    }
}
