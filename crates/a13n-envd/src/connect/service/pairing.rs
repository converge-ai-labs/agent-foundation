//! A Console-created invitation is claimed by the locally retained device credential.

use super::*;

#[derive(Deserialize)]
struct Reply {
    state: String,
    verification_code: String,
    environment_id: Option<String>,
    connection_path: Option<String>,
}

pub(super) async fn enroll(
    url: &Url,
    pairing_id: &str,
    credential: &str,
    profile: &Profile,
    config: &Config,
) -> io::Result<String> {
    let endpoint = url
        .join(&format!(
            "api/v1/client-environments/pairings/{pairing_id}/claim"
        ))
        .map_err(|_| invalid("invalid pairing URL"))?;
    let tls = if url.scheme() == "https" {
        Some(crate::websocket::tls_client_config(
            profile.ca_file.as_deref(),
        )?)
    } else {
        None
    };
    let body = serde_json::to_vec(&serde_json::json!({
        "environment_id": profile.environment_id,
        "device_id": config.device_id,
        "name": profile.name,
        "default_working_directory": config.default_working_directory,
        "computer_use": config.computer_use,
        "full_control": config.command.as_ref().is_some_and(|command| command.full_control),
    }))?;
    eprintln!(
        "Connecting to {}. Approve this computer in Console; Ctrl+C cancels this process.",
        url.origin().ascii_serialization()
    );
    let wait = async {
        let mut shown = false;
        loop {
            let bytes = tokio::time::timeout(
                REQUEST_TIMEOUT,
                post_with_header(
                    &endpoint,
                    credential,
                    &body,
                    tls.clone(),
                    "x-a13n-device-credential",
                ),
            )
            .await
            .map_err(|_| {
                io::Error::new(io::ErrorKind::TimedOut, "Console pairing request timed out")
            })??;
            let reply: Reply = serde_json::from_slice(&bytes)
                .map_err(|_| invalid("invalid Console pairing response"))?;
            if !shown {
                if reply.verification_code.len() != 6
                    || !reply.verification_code.bytes().all(|v| v.is_ascii_digit())
                {
                    return Err(invalid("invalid Console verification code"));
                }
                eprintln!(
                    "Verification code: {}. Check this code and the computer details in Console before approving.",
                    reply.verification_code
                );
                crate::connect::launcher::emit(
                    "verification",
                    serde_json::json!({"code": reply.verification_code}),
                );
                shown = true;
            }
            match reply.state.as_str() {
                "pending" => tokio::time::sleep(Duration::from_secs(2)).await,
                "approved" => {
                    let id = reply
                        .environment_id
                        .ok_or_else(|| invalid("approved pairing has no environment"))?;
                    validate_alias(&id)?;
                    if profile
                        .environment_id
                        .as_ref()
                        .is_some_and(|expected| expected != &id)
                        || reply.connection_path.as_deref()
                            != Some(format!("/api/v1/client-environments/{id}/connect").as_str())
                    {
                        return Err(invalid(
                            "Console pairing returned a different environment binding",
                        ));
                    }
                    eprintln!("Console approved environment {id}. Starting the device connection.");
                    crate::connect::launcher::emit(
                        "approved",
                        serde_json::json!({"environment_id": id}),
                    );
                    return Ok(id);
                }
                _ => return Err(invalid("invalid Console pairing state")),
            }
        }
    };
    tokio::select! {
        result = tokio::time::timeout(PAIRING_TIMEOUT, wait) => {
            result.map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "Console pairing expired; create a new request"))?
        }
        _ = tokio::signal::ctrl_c() => Err(io::Error::new(io::ErrorKind::Interrupted, "Console pairing cancelled")),
    }
}
