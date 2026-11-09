//! Service enrollment with API authentication and locally saved device credentials.
use super::*;

pub(crate) mod link;
mod pairing;
mod recognition;
pub(crate) use recognition::recognize_link;

pub(crate) const HELP: &str = "a13n-envd connect-service <URL or saved name> [options]

First use: --workspace-id ID --api-token-file PATH
Console pairing: --workspace-id ID --pairing-id ID (no API token)
Reconnect using the saved name; the API token is no longer needed.

  --environment-id ID     Select an existing environment; never create a replacement
  --device-id ID          Override the installation identity file
  --credential-file PATH Import a device credential after explicit rotation
  --host NAME            Save this Service/workspace binding under NAME

Also accepts connect options: --instance, --name, --state-dir, --ca-file,
--config, --default-working-directory, --execution-uid, --execution-gid,
--allow-sudo, --egress-mode and --computer-use.

Keep token files private. The API token is not saved.
Startup never rotates credentials. Use a different saved name when switching accounts or workspaces.";

#[derive(Default)]
struct Options {
    pairing_id: Option<String>,
    api_token_file: Option<PathBuf>,
    credential_file: Option<PathBuf>,
    workspace_id: Option<String>,
    environment_id: Option<String>,
    device_id: Option<String>,
}

impl Options {
    fn parse(arguments: Vec<OsString>) -> io::Result<(Self, Arguments)> {
        let mut result = Self::default();
        let mut common = Vec::new();
        let mut seen = BTreeSet::new();
        let mut arguments = arguments.into_iter();
        while let Some(argument) = arguments.next() {
            let flag = argument.to_str().unwrap_or("");
            if matches!(
                flag,
                "--api-token-file"
                    | "--pairing-id"
                    | "--credential-file"
                    | "--workspace-id"
                    | "--environment-id"
                    | "--device-id"
            ) {
                if !seen.insert(flag.to_owned()) {
                    return Err(invalid("duplicate Service option"));
                }
                let value = arguments
                    .next()
                    .ok_or_else(|| invalid("Service option requires a value"))?;
                match flag {
                    "--pairing-id" => result.pairing_id = Some(text(value)?),
                    "--api-token-file" => result.api_token_file = Some(absolute(value.into())?),
                    "--credential-file" => result.credential_file = Some(absolute(value.into())?),
                    "--workspace-id" => result.workspace_id = Some(text(value)?),
                    "--environment-id" => result.environment_id = Some(text(value)?),
                    "--device-id" => result.device_id = Some(text(value)?),
                    _ => unreachable!(),
                }
            } else {
                let option = flag.starts_with('-');
                common.push(argument);
                if option {
                    common.push(
                        arguments
                            .next()
                            .ok_or_else(|| invalid("connect option requires a value"))?,
                    );
                }
            }
        }
        if result.pairing_id.is_some()
            && (result.api_token_file.is_some() || result.credential_file.is_some())
        {
            return Err(invalid(
                "Console pairing cannot import API or device credentials",
            ));
        }
        if let Some(id) = &result.pairing_id {
            validate_alias(id)?;
        }
        Ok((result, Arguments::parse(common)?))
    }
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Profile {
    url: String,
    workspace_id: String,
    device_id: String,
    environment_id: Option<String>,
    name: Option<String>,
    ca_file: Option<PathBuf>,
}

#[derive(Deserialize)]
struct Enrolled {
    client: Client,
    token: Option<String>,
    connection_path: String,
}
#[derive(Deserialize)]
struct Client {
    environment: Environment,
}
#[derive(Deserialize)]
struct Environment {
    id: String,
    device_id: String,
}

pub(crate) async fn prepare(
    arguments: Vec<OsString>,
) -> Result<Config, Box<dyn std::error::Error + Send + Sync>> {
    let (options, arguments) = Options::parse(arguments)?;
    let state = arguments
        .state_directory
        .clone()
        .map(Ok)
        .unwrap_or_else(default_state_directory)?
        .join("instances")
        .join(&arguments.instance);
    let identity = match options.device_id {
        Some(value) => value,
        None => crate::runtime::installation_identity(&state).map_err(io::Error::other)?,
    };
    let target = if arguments.target.contains("://") {
        Some(host_url(&arguments.target)?)
    } else {
        validate_alias(&arguments.target)?;
        None
    };
    let alias = match (&arguments.host, &target) {
        (Some(name), _) => name.clone(),
        (None, Some(url)) => format!(
            "service-{}",
            &hex(&Sha256::digest(
                format!("{}\n{}", url, options.workspace_id.as_deref().unwrap_or("")).as_bytes()
            ))[..16]
        ),
        (None, None) => arguments.target.clone(),
    };
    let directory = state.join("services").join(&alias);
    fs::create_dir_all(&directory)?;
    crate::runtime::protect_directory(&directory).map_err(io::Error::other)?;
    let profile_path = directory.join("service.json");
    let mut profile: Profile = match fs::read(&profile_path) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map_err(|_| invalid("saved Service configuration is invalid; it was not replaced"))?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => Profile {
            url: target
                .as_ref()
                .ok_or_else(|| invalid("saved Service not found; use its URL first"))?
                .to_string(),
            workspace_id: options
                .workspace_id
                .clone()
                .ok_or_else(|| invalid("first connection requires --workspace-id"))?,
            device_id: identity.clone(),
            environment_id: options.environment_id.clone(),
            name: arguments.name.clone(),
            ca_file: arguments.ca_file.clone(),
        },
        Err(error) => return Err(error.into()),
    };
    if target
        .as_ref()
        .is_some_and(|url| url.as_str() != profile.url)
        || options
            .workspace_id
            .as_ref()
            .is_some_and(|id| id != &profile.workspace_id)
        || options.environment_id.as_ref().is_some_and(|id| {
            profile
                .environment_id
                .as_ref()
                .is_some_and(|saved| id != saved)
        })
    {
        return Err(invalid("saved Service selects another URL, workspace or environment; use a different --host name").into());
    }
    if profile.device_id != identity {
        return Err(invalid("device_id_mismatch: saved Service belongs to another device_id; pass --device-id with the original ID, or use a different --host name for a new registration").into());
    }
    if profile.environment_id.is_none() {
        profile.environment_id = options.environment_id;
    }
    if arguments.ca_file.is_some() {
        profile.ca_file = arguments.ca_file;
    }
    let url = host_url(&profile.url)?;
    let credential_path = directory.join("credential");
    // Config validates bootstrap paths before enrollment. An empty placeholder
    // cannot authorize a WebSocket and is treated as a missing credential.
    let mut open = OpenOptions::new();
    open.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        open.mode(0o600);
    }
    match open.open(&credential_path) {
        Ok(file) => file.sync_all()?,
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.into()),
    }
    let mut config = Config::for_connection(
        arguments.daemon,
        ConnectionBootstrap {
            device_id: identity,
            name: arguments.name.or_else(|| profile.name.clone()),
            runtime_directory: directory.join("runtime"),
            transport: ReverseWebSocketConfig {
                endpoint: websocket_url(&url, "/api/v1/client-environments/connect")?.to_string(),
                credential_file: credential_path.clone(),
                tls_ca_file: profile.ca_file.clone(),
            },
        },
    )?;
    // Mask enrollment/import sources from subsequently executed commands too.
    for path in [&options.api_token_file, &options.credential_file]
        .into_iter()
        .flatten()
    {
        config.bootstrap_files.push(fs::canonicalize(path)?);
    }
    config
        .sandbox
        .validate(&config.bootstrap_files)
        .map_err(io::Error::other)?;
    profile.name = Some(config.display_name.clone().unwrap_or(arguments.instance));
    let mut credential = if let Some(path) = &options.credential_file {
        Some(crate::websocket::read_credential(path).await?)
    } else if fs::metadata(&credential_path)?.len() == 0 {
        None
    } else {
        Some(crate::websocket::read_credential(&credential_path).await?)
    };
    save(&profile_path, &serde_json::to_vec_pretty(&profile)?)?;
    if let Some(pairing_id) = options.pairing_id.as_deref() {
        if credential.is_none() {
            if profile.environment_id.is_some() {
                return Err(credential_required(profile.environment_id.as_deref()).into());
            }
            let mut bytes = [0_u8; 32];
            getrandom::fill(&mut bytes).map_err(io::Error::other)?;
            let token = hex(&bytes);
            // Save before contacting Service: a lost approval response is recoverable.
            save(&credential_path, token.as_bytes())?;
            credential = Some(token);
        }
        let id = if profile.environment_id.is_some() {
            recognition::report(&url, pairing_id, credential.as_deref().unwrap(), &profile).await?
        } else {
            pairing::enroll(
                &url,
                pairing_id,
                credential.as_deref().unwrap(),
                &profile,
                &config,
            )
            .await?
        };
        profile.environment_id = Some(id);
        save(&profile_path, &serde_json::to_vec_pretty(&profile)?)?;
    } else if profile.environment_id.is_none() || credential.is_none() {
        let api_path = options
            .api_token_file
            .as_ref()
            .ok_or_else(|| credential_required(profile.environment_id.as_deref()))?;
        let api_token = crate::websocket::read_credential(api_path).await?;
        let mut endpoint = url.join("api/v1/client-environments/connect")?;
        endpoint
            .query_pairs_mut()
            .append_pair("workspace_id", &profile.workspace_id);
        let body = serde_json::to_vec(&serde_json::json!({
            "environment_id": profile.environment_id, "device_id": config.device_id,
            "name": profile.name, "default_working_directory": config.default_working_directory,
        }))?;
        let tls = if url.scheme() == "https" {
            Some(crate::websocket::tls_client_config(
                profile.ca_file.as_deref(),
            )?)
        } else {
            None
        };
        let bytes = tokio::time::timeout(REQUEST_TIMEOUT, post(&endpoint, &api_token, &body, tls)).await
            .map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "Service enrollment timed out; retry discovery, then explicitly rotate if the credential was lost"))??;
        let response: Enrolled = serde_json::from_slice(&bytes)?;
        let id = &response.client.environment.id;
        validate_alias(id)?;
        if response.client.environment.device_id != config.device_id
            || profile
                .environment_id
                .as_ref()
                .is_some_and(|expected| expected != id)
            || response.connection_path != format!("/api/v1/client-environments/{id}/connect")
        {
            return Err(invalid(
                "Service enrollment returned a different device or environment binding",
            )
            .into());
        }
        websocket_url(&url, &response.connection_path)?;
        profile.environment_id = Some(id.clone());
        // Persist the ID first: lost credential delivery requires rotation.
        save(&profile_path, &serde_json::to_vec_pretty(&profile)?)?;
        match response.token.or(credential) {
            Some(token) => save(&credential_path, token.as_bytes())?,
            None => return Err(credential_required(Some(id)).into()),
        }
    } else if let Some(token) = credential
        && options.credential_file.is_some()
    {
        save(&credential_path, token.as_bytes())?;
    }
    crate::websocket::read_credential(&credential_path).await?;
    let id = profile
        .environment_id
        .as_deref()
        .expect("enrollment completed");
    validate_alias(id)?;
    if let TransportConfig::ReverseWebSocket(transport) = &mut config.transport {
        transport.endpoint =
            websocket_url(&url, &format!("/api/v1/client-environments/{id}/connect"))?.to_string();
    }
    Ok(config)
}

fn credential_required(id: Option<&str>) -> io::Error {
    invalid(&match id {
        Some(id) => format!(
            "client_credential_required: environment {id} has no local device credential. Use an authorized API token and the current ETag to POST /api/v1/client-environments/{id}/rotate-credential, then restart with --credential-file PATH. Startup does not rotate credentials."
        ),
        None => {
            "client_credential_required: first connection requires --api-token-file PATH".to_owned()
        }
    })
}

fn save(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut file = tempfile::NamedTempFile::new_in(
        path.parent().ok_or_else(|| invalid("invalid state path"))?,
    )?;
    file.write_all(bytes)?;
    file.as_file().sync_all()?;
    file.persist(path).map_err(|error| error.error)?;
    Ok(())
}
