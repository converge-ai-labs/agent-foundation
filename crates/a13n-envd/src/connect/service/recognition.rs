//! Read and prove a saved registration without taking the runtime lock or starting a carrier.
use super::*;

pub(crate) async fn recognize_link(arguments: Vec<OsString>) -> io::Result<serde_json::Value> {
    let (options, arguments) = Options::parse(arguments)?;
    let url = host_url(&arguments.target)?;
    let workspace = options
        .workspace_id
        .ok_or_else(|| invalid("missing workspace"))?;
    let alias = arguments.host.ok_or_else(|| invalid("missing host"))?;
    let state = arguments
        .state_directory
        .map(Ok)
        .unwrap_or_else(default_state_directory)?;
    let directory = state
        .join("instances")
        .join(arguments.instance)
        .join("services")
        .join(&alias);
    let profile: Profile = match fs::read(directory.join("service.json")) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map_err(|_| invalid("saved Service configuration is invalid; it was not replaced"))?,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Ok(serde_json::json!({"registered": false}));
        }
        Err(error) => return Err(error),
    };
    if profile.url != url.as_str() || profile.workspace_id != workspace {
        return Err(invalid("saved Service selects another URL or workspace"));
    }
    if profile.environment_id.is_none() {
        return Ok(serde_json::json!({"registered": false}));
    }
    let credential = crate::websocket::read_credential(&directory.join("credential")).await?;
    report(
        &url,
        options
            .pairing_id
            .as_deref()
            .ok_or_else(|| invalid("missing pairing"))?,
        &credential,
        &profile,
    )
    .await?;
    Ok(serde_json::json!({"registered": true}))
}

pub(super) async fn report(
    url: &Url,
    pairing_id: &str,
    credential: &str,
    profile: &Profile,
) -> io::Result<String> {
    let id = profile
        .environment_id
        .as_deref()
        .ok_or_else(|| invalid("missing saved environment"))?;
    let endpoint = url
        .join(&format!(
            "api/v1/client-environments/pairings/{pairing_id}/recognize"
        ))
        .map_err(|_| invalid("invalid recognition URL"))?;
    let tls = if url.scheme() == "https" {
        Some(crate::websocket::tls_client_config(
            profile.ca_file.as_deref(),
        )?)
    } else {
        None
    };
    let body = serde_json::to_vec(
        &serde_json::json!({"environment_id": id, "device_id": profile.device_id}),
    )?;
    let bytes = tokio::time::timeout(
        REQUEST_TIMEOUT,
        post_with_header(
            &endpoint,
            credential,
            &body,
            tls,
            "x-a13n-device-credential",
        ),
    )
    .await
    .map_err(|_| {
        io::Error::new(
            io::ErrorKind::TimedOut,
            "Existing registration check timed out; retry without registering again",
        )
    })??;
    let reply: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|_| invalid("invalid recognition response"))?;
    if reply["state"] != "approved" || reply["reused"] != true || reply["environment_id"] != id {
        return Err(invalid("Service returned a different registration binding"));
    }
    crate::connect::launcher::emit("approved", serde_json::json!({"environment_id": id}));
    Ok(id.to_owned())
}
