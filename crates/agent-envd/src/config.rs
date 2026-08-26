use std::{
    collections::{BTreeMap, BTreeSet},
    env,
    error::Error,
    fmt, fs,
    path::{Path, PathBuf},
    time::Duration,
};

use serde::Deserialize;

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
    "AGENT_ENVD_API_KEY",
    "AGENT_ENVD_TRANSPORT",
    "AGENT_ENVD_LISTEN_ADDRESS",
    "AGENT_ENVD_HTTP_ENABLED",
    "AGENT_ENVD_WEBSOCKET_ENABLED",
    "AGENT_ENVD_REVERSE_WS_URL",
    "AGENT_ENVD_REVERSE_WS_CREDENTIAL_FILE",
    "AGENT_ENVD_REVERSE_WS_CA_FILE",
    "AGENT_ENVD_ENVIRONMENT_ID",
    "AGENT_ENVD_RUNTIME_DIR",
    "AGENT_ENVD_EXECUTION_ISOLATION",
    "AGENT_ENVD_EXECUTION_NETWORK",
    "AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS",
    "AGENT_ENVD_EXECUTION_UID",
    "AGENT_ENVD_EXECUTION_GID",
];

const LEGACY_NETWORK_VARIABLES: &[&str] = &[
    "AGENT_ENVD_LISTEN_ADDRESS",
    "AGENT_ENVD_HTTP_ENABLED",
    "AGENT_ENVD_WEBSOCKET_ENABLED",
];
const REVERSE_WEBSOCKET_VARIABLES: &[&str] = &[
    "AGENT_ENVD_REVERSE_WS_URL",
    "AGENT_ENVD_REVERSE_WS_CREDENTIAL_FILE",
    "AGENT_ENVD_REVERSE_WS_CA_FILE",
];

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct TrustedMountConfig {
    pub(crate) mount_id: String,
    pub(crate) native_root: PathBuf,
    pub(crate) writable: bool,
    #[serde(default = "default_allow_command_execution")]
    pub(crate) allow_command_execution: bool,
    pub(crate) max_file_bytes: u64,
    #[serde(default)]
    pub(crate) allowed_operations: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
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

#[derive(Debug, Clone)]
pub(crate) struct CommandConfig {
    pub(crate) private_home: PathBuf,
    pub(crate) private_temp: PathBuf,
    pub(crate) base_environment: BTreeMap<String, String>,
    pub(crate) trusted_executable_roots: Vec<PathBuf>,
    pub(crate) shell_profiles: Vec<TrustedShellProfileConfig>,
    pub(crate) max_arguments: usize,
    pub(crate) max_argument_bytes: usize,
    pub(crate) max_environment_entries: usize,
    pub(crate) max_environment_bytes: usize,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileConfig {
    #[serde(default)]
    root_mount_id: Option<String>,
    #[serde(default)]
    limits: FileLimitsConfig,
    #[serde(default)]
    mounts: Vec<TrustedMountConfig>,
    #[serde(default)]
    trusted_executable_roots: Vec<PathBuf>,
    #[serde(default)]
    shell_profiles: Vec<TrustedShellProfileConfig>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileLimitsConfig {
    #[serde(default)]
    max_output_preview_bytes: Option<u64>,
    #[serde(default)]
    max_output_bytes_per_stream: Option<u64>,
    #[serde(default)]
    max_spool_bytes: Option<u64>,
}

#[derive(Debug, Clone)]
pub(crate) struct DaemonLimits {
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

impl DaemonLimits {
    pub(crate) fn descriptor(&self) -> EIPLimits {
        EIPLimits {
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

#[derive(Debug, Clone)]
pub(crate) enum TransportConfig {
    Stdio,
    ReverseWebSocket(ReverseWebSocketConfig),
}

#[derive(Debug, Clone)]
pub(crate) struct ReverseWebSocketConfig {
    pub(crate) endpoint: String,
    pub(crate) credential_file: PathBuf,
    pub(crate) tls_ca_file: Option<PathBuf>,
}

#[derive(Debug, Clone)]
pub(crate) struct Config {
    pub(crate) environment_id: String,
    pub(crate) transport: TransportConfig,
    pub(crate) limits: DaemonLimits,
    pub(crate) initialization_timeout: Duration,
    pub(crate) session_idle_timeout: Duration,
    pub(crate) root_mount_id: Option<String>,
    pub(crate) mounts: Vec<TrustedMountConfig>,
    pub(crate) command: Option<CommandConfig>,
    pub(crate) runtime: Option<RuntimeState>,
}

impl Config {
    pub(crate) fn from_environment() -> Result<Self, ConfigError> {
        reject_unknown_environment_variables()?;
        let file = load_file_config(config_file_argument()?)?;

        if env::var_os("AGENT_ENVD_API_KEY").is_some() {
            return Err(ConfigError::new(
                "AGENT_ENVD_API_KEY is unsupported; reverse WebSocket uses AGENT_ENVD_REVERSE_WS_CREDENTIAL_FILE",
            ));
        }
        for name in LEGACY_NETWORK_VARIABLES {
            if env::var_os(name).is_some() {
                return Err(ConfigError::new(format!(
                    "{name} is unsupported because agent-envd never listens for network connections"
                )));
            }
        }
        let transport_name =
            optional_unicode("AGENT_ENVD_TRANSPORT")?.unwrap_or_else(|| "stdio".to_owned());
        let transport = match transport_name.as_str() {
            "stdio" => {
                for name in REVERSE_WEBSOCKET_VARIABLES {
                    if env::var_os(name).is_some() {
                        return Err(ConfigError::new(format!(
                            "{name} is not valid with stdio transport"
                        )));
                    }
                }
                TransportConfig::Stdio
            }
            "reverse_websocket" => {
                TransportConfig::ReverseWebSocket(parse_reverse_websocket_config(
                    required_unicode("AGENT_ENVD_REVERSE_WS_URL")?,
                    PathBuf::from(required_unicode("AGENT_ENVD_REVERSE_WS_CREDENTIAL_FILE")?),
                    optional_unicode("AGENT_ENVD_REVERSE_WS_CA_FILE")?.map(PathBuf::from),
                )?)
            }
            _ => {
                return Err(ConfigError::new(
                    "AGENT_ENVD_TRANSPORT must be stdio or reverse_websocket",
                ));
            }
        };

        let isolation = optional_unicode("AGENT_ENVD_EXECUTION_ISOLATION")?
            .unwrap_or_else(|| "required".to_owned());
        match isolation.as_str() {
            "disabled" => {}
            "required" => {
                return Err(ConfigError::new(
                    "required execution isolation is not available yet; explicitly set AGENT_ENVD_EXECUTION_ISOLATION=disabled only inside an outer sandbox",
                ));
            }
            _ => {
                return Err(ConfigError::new(
                    "AGENT_ENVD_EXECUTION_ISOLATION must be required or disabled",
                ));
            }
        }

        if let Some(network) = optional_unicode("AGENT_ENVD_EXECUTION_NETWORK")?
            && network != "host"
        {
            return Err(ConfigError::new(
                "disabled execution isolation supports only AGENT_ENVD_EXECUTION_NETWORK=host",
            ));
        }
        if let Some(paths) = optional_unicode("AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS")? {
            let paths = serde_json::from_str::<Vec<String>>(&paths).map_err(|_| {
                ConfigError::new(
                    "AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS must be a JSON array of strings",
                )
            })?;
            if !paths.is_empty() {
                return Err(ConfigError::new(
                    "extra read-only execution paths require native isolation",
                ));
            }
        }
        if env::var_os("AGENT_ENVD_EXECUTION_UID").is_some()
            || env::var_os("AGENT_ENVD_EXECUTION_GID").is_some()
        {
            return Err(ConfigError::new(
                "execution UID and GID require native isolation",
            ));
        }

        let runtime_dir = optional_unicode("AGENT_ENVD_RUNTIME_DIR")?.map(PathBuf::from);
        if runtime_dir.as_ref().is_some_and(|path| !path.is_absolute()) {
            return Err(ConfigError::new(
                "AGENT_ENVD_RUNTIME_DIR must be an absolute path",
            ));
        }

        let environment_id = required_unicode("AGENT_ENVD_ENVIRONMENT_ID")?;
        if environment_id.trim() != environment_id
            || environment_id.is_empty()
            || environment_id.len() > 256
            || environment_id.chars().any(char::is_control)
        {
            return Err(ConfigError::new(
                "AGENT_ENVD_ENVIRONMENT_ID must be 1..=256 non-control characters without surrounding whitespace",
            ));
        }

        let mut limits = default_limits();
        apply_file_limits(&mut limits, file.limits)?;
        let runtime = runtime_dir
            .as_deref()
            .map(RuntimeState::prepare)
            .transpose()
            .map_err(ConfigError::new)?;
        let command = prepare_command_config(
            runtime.as_ref(),
            file.trusted_executable_roots,
            file.shell_profiles,
        )?;
        Ok(Self {
            environment_id,
            transport,
            initialization_timeout: INITIALIZATION_TIMEOUT,
            session_idle_timeout: Duration::from_millis(DEFAULT_SESSION_IDLE_TTL_MS),
            root_mount_id: file.root_mount_id,
            mounts: file.mounts,
            command,
            runtime,
            limits,
        })
    }

    #[cfg(test)]
    pub(crate) fn for_test(environment_id: &str) -> Self {
        Self {
            environment_id: environment_id.to_owned(),
            transport: TransportConfig::Stdio,
            limits: default_limits(),
            initialization_timeout: Duration::from_millis(20),
            session_idle_timeout: Duration::from_secs(1),
            root_mount_id: None,
            mounts: Vec::new(),
            command: None,
            runtime: None,
        }
    }
}

fn parse_reverse_websocket_config(
    endpoint: String,
    credential_file: PathBuf,
    tls_ca_file: Option<PathBuf>,
) -> Result<ReverseWebSocketConfig, ConfigError> {
    let endpoint = url::Url::parse(&endpoint)
        .map_err(|_| ConfigError::new("AGENT_ENVD_REVERSE_WS_URL must be a valid ws or wss URL"))?;
    if !matches!(endpoint.scheme(), "ws" | "wss")
        || endpoint.host_str().is_none()
        || !endpoint.username().is_empty()
        || endpoint.password().is_some()
        || endpoint.query().is_some()
        || endpoint.fragment().is_some()
    {
        return Err(ConfigError::new(
            "AGENT_ENVD_REVERSE_WS_URL must be ws or wss with a host and without user info, query, or fragment",
        ));
    }
    validate_bootstrap_file(&credential_file, "AGENT_ENVD_REVERSE_WS_CREDENTIAL_FILE")?;
    let credential_file = fs::canonicalize(&credential_file).map_err(|error| {
        ConfigError::new(format!(
            "cannot canonicalize AGENT_ENVD_REVERSE_WS_CREDENTIAL_FILE: {error}"
        ))
    })?;
    let tls_ca_file = tls_ca_file
        .map(|path| {
            validate_bootstrap_file(&path, "AGENT_ENVD_REVERSE_WS_CA_FILE")?;
            fs::canonicalize(&path).map_err(|error| {
                ConfigError::new(format!(
                    "cannot canonicalize AGENT_ENVD_REVERSE_WS_CA_FILE: {error}"
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

fn apply_file_limits(
    limits: &mut DaemonLimits,
    configured: FileLimitsConfig,
) -> Result<(), ConfigError> {
    limits.max_output_preview_bytes = configured
        .max_output_preview_bytes
        .unwrap_or(limits.max_output_preview_bytes);
    limits.max_output_bytes_per_stream = configured
        .max_output_bytes_per_stream
        .unwrap_or(limits.max_output_bytes_per_stream);
    limits.max_spool_bytes = configured.max_spool_bytes.unwrap_or(limits.max_spool_bytes);
    if limits.max_output_preview_bytes == 0
        || limits.max_output_bytes_per_stream == 0
        || limits.max_spool_bytes == 0
    {
        return Err(ConfigError::new("output and spool limits must be positive"));
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

fn default_allow_command_execution() -> bool {
    true
}

fn prepare_command_config(
    runtime: Option<&RuntimeState>,
    trusted_roots: Vec<PathBuf>,
    shell_profiles: Vec<TrustedShellProfileConfig>,
) -> Result<Option<CommandConfig>, ConfigError> {
    if trusted_roots.is_empty() && shell_profiles.is_empty() {
        return Ok(None);
    }
    let runtime = runtime.ok_or_else(|| {
        ConfigError::new(
            "AGENT_ENVD_RUNTIME_DIR is required when command execution policy is configured",
        )
    })?;
    let private_home = runtime.home().to_path_buf();
    let private_temp = runtime.temp().to_path_buf();

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
        private_home,
        private_temp,
        base_environment: inherited_command_environment(),
        trusted_executable_roots: canonical_roots,
        shell_profiles: prepared_profiles,
        max_arguments: DEFAULT_MAX_COMMAND_ARGUMENTS,
        max_argument_bytes: DEFAULT_MAX_COMMAND_ARGUMENT_BYTES,
        max_environment_entries: DEFAULT_MAX_COMMAND_ENVIRONMENT_ENTRIES,
        max_environment_bytes: DEFAULT_MAX_COMMAND_ENVIRONMENT_BYTES,
    }))
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
    matches!(upper.as_str(), "PATH" | "HOME" | "TMPDIR" | "TMP" | "TEMP")
        || upper.starts_with("AGENT_ENVD_")
        || upper.starts_with("LD_")
        || upper.starts_with("DYLD_")
        || upper.starts_with("EIP_")
}

fn inherited_command_environment() -> BTreeMap<String, String> {
    env::vars_os()
        .filter_map(|(name, value)| Some((name.into_string().ok()?, value.into_string().ok()?)))
        .filter(|(name, value)| {
            common_environment_name(name)
                && valid_environment_name(name)
                && !reserved_environment_name(name)
                && !value.contains('\0')
        })
        .collect()
}

fn common_environment_name(name: &str) -> bool {
    matches!(
        name,
        "LANG"
            | "LANGUAGE"
            | "TZ"
            | "TERM"
            | "COLORTERM"
            | "NO_COLOR"
            | "FORCE_COLOR"
            | "SSL_CERT_FILE"
            | "SSL_CERT_DIR"
            | "REQUESTS_CA_BUNDLE"
            | "CURL_CA_BUNDLE"
            | "HTTP_PROXY"
            | "HTTPS_PROXY"
            | "ALL_PROXY"
            | "NO_PROXY"
            | "http_proxy"
            | "https_proxy"
            | "all_proxy"
            | "no_proxy"
            | "CARGO_HOME"
            | "RUSTUP_HOME"
            | "GOPATH"
            | "GOMODCACHE"
            | "NPM_CONFIG_PREFIX"
            | "PNPM_HOME"
            | "UV_CACHE_DIR"
            | "PIP_CACHE_DIR"
            | "XDG_CACHE_HOME"
            | "XDG_CONFIG_HOME"
            | "XDG_DATA_HOME"
    ) || name.starts_with("LC_")
}

fn config_file_argument() -> Result<Option<PathBuf>, ConfigError> {
    let mut arguments = env::args_os().skip(1);
    let Some(flag) = arguments.next() else {
        return Ok(None);
    };
    if flag != "--config" {
        return Err(ConfigError::new(
            "the only supported argument is --config <absolute-json-path>",
        ));
    }
    let path = arguments
        .next()
        .ok_or_else(|| ConfigError::new("--config requires a path"))?;
    if arguments.next().is_some() {
        return Err(ConfigError::new("unexpected arguments after --config path"));
    }
    let path = PathBuf::from(path);
    if !path.is_absolute() {
        return Err(ConfigError::new("--config path must be absolute"));
    }
    Ok(Some(path))
}

fn load_file_config(path: Option<PathBuf>) -> Result<FileConfig, ConfigError> {
    let Some(path) = path else {
        return Ok(FileConfig::default());
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
    serde_json::from_slice(&bytes)
        .map_err(|error| ConfigError::new(format!("invalid config file JSON: {error}")))
}

fn default_limits() -> DaemonLimits {
    DaemonLimits {
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
        if name.starts_with("AGENT_ENVD_") && !KNOWN_ENVIRONMENT_VARIABLES.contains(&name) {
            return Err(ConfigError::new(format!(
                "unknown agent-envd environment variable: {name}"
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
    fn reverse_websocket_accepts_ws_and_wss_but_rejects_credential_bearing_urls() {
        let unique = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .expect("clock follows Unix epoch")
            .as_nanos();
        let directory = std::env::temp_dir().join(format!("agent-envd-config-{unique}"));
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
    fn test_configuration_has_finite_valid_limits() {
        let config = Config::for_test("env-test");

        config
            .limits
            .descriptor()
            .validate()
            .expect("limits are valid");
        assert_eq!(config.environment_id, "env-test");
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
