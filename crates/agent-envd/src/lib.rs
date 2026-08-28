use std::{error::Error, sync::Arc};

mod config;
mod daemon;
pub mod eip;
mod http;
mod isolation;
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

/// Runs one private isolation-probe payload mode used by this binary.
#[doc(hidden)]
pub fn run_internal_isolation_probe(
    arguments: &[std::ffi::OsString],
) -> Result<i32, Box<dyn Error + Send + Sync>> {
    Ok(isolation::run_internal_probe(arguments)?)
}

/// Runs one private isolation-probe descendant mode used by this binary.
#[doc(hidden)]
pub fn run_internal_isolation_probe_child(
    arguments: &[std::ffi::OsString],
) -> Result<i32, Box<dyn Error + Send + Sync>> {
    Ok(isolation::run_internal_probe_child(arguments)?)
}

/// Runs one private isolation-probe sleeper mode used by this binary.
#[doc(hidden)]
pub fn run_internal_isolation_probe_sleeper(
    arguments: &[std::ffi::OsString],
) -> Result<i32, Box<dyn Error + Send + Sync>> {
    Ok(isolation::run_internal_probe_sleeper(arguments)?)
}

/// Runs the side-effect-bounded execution-isolation preflight.
pub fn run_isolation_probe(
    config_file: Option<std::path::PathBuf>,
) -> Result<(), Box<dyn Error + Send + Sync>> {
    let execution = config::execution_config_for_probe(config_file)?;
    let report = isolation::IsolationRuntime::probe_config(&execution)?;
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}

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
    if config.execution.isolation == config::ExecutionIsolationMode::Disabled {
        let warning = serde_json::json!({
            "level": "warning",
            "event": "agent-envd.execution_isolation.disabled",
            "message": "envd-native execution isolation is disabled; the outer host owns containment",
        });
        eprintln!("{warning}");
    }
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
