use std::{error::Error, sync::Arc};

mod config;
mod daemon;
pub mod eip;
mod mount;
mod operation;
mod process;
mod resource;
mod retention;
mod runtime;
mod stdio;
mod supervisor;
mod transfer;
mod websocket;

/// Runs the private gated command supervisor used by this binary.
#[doc(hidden)]
pub async fn run_internal_supervisor() -> Result<(), Box<dyn Error + Send + Sync>> {
    supervisor::run_internal().await?;
    Ok(())
}

/// Runs one agent-envd instance from trusted process configuration.
pub async fn run_from_environment() -> Result<(), Box<dyn Error + Send + Sync>> {
    let config = config::Config::from_environment()?;
    let daemon = Arc::new(daemon::Daemon::new(&config)?);
    let warning = serde_json::json!({
        "level": "warning",
        "event": "agent-envd.execution_isolation.disabled",
        "message": "envd-native execution isolation is disabled; the outer host owns containment",
    });
    eprintln!("{warning}");
    match &config.transport {
        config::TransportConfig::Stdio => stdio::serve(daemon, &config).await?,
        config::TransportConfig::ReverseWebSocket(websocket_config) => {
            websocket::serve(daemon, &config, websocket_config).await?;
        }
    }
    Ok(())
}
