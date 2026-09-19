use std::{error::Error, sync::Arc};

mod capacity;
mod config;
mod daemon;
mod data_dispatch;
mod device_path;
pub mod eip;
mod filesystem;
mod http;
mod operation;
mod process;
mod resource;
mod retention;
mod runtime;
mod stdio;
mod supervisor;
mod transfer;
mod websocket;
#[cfg(windows)]
mod windows_job;

/// Runs the private gated command supervisor used by this binary.
#[doc(hidden)]
pub async fn run_internal_supervisor() -> Result<(), Box<dyn Error + Send + Sync>> {
    supervisor::run_internal().await?;
    Ok(())
}

/// Runs one a13n-envd instance from trusted process configuration.
pub async fn run_from_environment() -> Result<(), Box<dyn Error + Send + Sync>> {
    let config = config::Config::from_environment()?;
    let daemon = Arc::new(daemon::Daemon::new(&config)?);
    match &config.transport {
        config::TransportConfig::Stdio => stdio::serve(daemon, &config).await?,
        config::TransportConfig::Http(http_config) => {
            http::serve(daemon, &config, http_config).await?;
        }
        config::TransportConfig::ReverseWebSocket(websocket_config) => {
            websocket::serve(daemon, &config, websocket_config).await?;
        }
    }
    Ok(())
}
