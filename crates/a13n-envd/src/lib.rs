use std::{error::Error, sync::Arc};

mod capacity;
mod computer;
mod config;
mod connect;
mod daemon;
mod data_dispatch;
mod device_path;
#[cfg(any(target_os = "linux", test))]
#[cfg_attr(
    not(target_os = "linux"),
    allow(
        dead_code,
        reason = "portable policy tests also run without the Linux runtime"
    )
)]
mod egress;
pub mod eip;
mod execution;
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

/// Runs the private isolated Session bootstrap before constructing any threads.
#[doc(hidden)]
pub fn run_internal_egress_worker(ready: bool) -> std::io::Result<()> {
    #[cfg(target_os = "linux")]
    {
        execution::worker::run_internal(ready)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = ready;
        Err(std::io::Error::other("egress isolation requires Linux"))
    }
}

/// Runs the immutable restricted bootstrap before loading payload-tree code.
#[doc(hidden)]
pub fn run_internal_restricted_worker() -> std::io::Result<()> {
    #[cfg(target_os = "linux")]
    {
        execution::worker::run_restricted()
    }
    #[cfg(not(target_os = "linux"))]
    {
        Err(std::io::Error::other("restricted bootstrap requires Linux"))
    }
}

/// Runs an ordinary Session under its trusted startup identity.
#[doc(hidden)]
pub fn run_internal_session_worker() -> std::io::Result<()> {
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        execution::worker::run_session()
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        Err(std::io::Error::other(
            "Session workers are unavailable on this platform",
        ))
    }
}

/// Runs a one-shot directory observation under the execution identity.
#[doc(hidden)]
pub fn run_internal_directory_worker() -> std::io::Result<()> {
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        execution::discovery::run()
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        Err(std::io::Error::other(
            "Session workers are unavailable on this platform",
        ))
    }
}

/// Runs one a13n-envd instance from trusted process configuration.
pub fn run_from_environment() -> Result<(), Box<dyn Error + Send + Sync>> {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if arguments.as_slice() == [std::ffi::OsString::from("--help")]
        || arguments.as_slice()
            == [
                std::ffi::OsString::from("connect"),
                std::ffi::OsString::from("--help"),
            ]
    {
        println!("{}", connect::HELP);
        return Ok(());
    }
    let config = if arguments
        .first()
        .is_some_and(|argument| argument == "connect")
    {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        let config = runtime.block_on(connect::prepare(arguments.into_iter().skip(1).collect()))?;
        drop(runtime);
        config
    } else if arguments.as_slice() == [std::ffi::OsString::from("--internal-managed-broker")] {
        #[cfg(target_os = "linux")]
        {
            execution::management::restore()?
        }
        #[cfg(not(target_os = "linux"))]
        {
            return Err("managed broker requires Linux".into());
        }
    } else {
        config::Config::from_environment()?
    };
    #[cfg(target_os = "linux")]
    if execution::boundary::managed(&config.sandbox, config.egress) && !config.managed {
        execution::management::enter(config)?;
        unreachable!("management bootstrap reexecs or fails");
    }
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;
    let result = runtime.block_on(serve(config));
    runtime.shutdown_timeout(std::time::Duration::from_secs(1));
    result
}

async fn serve(config: config::Config) -> Result<(), Box<dyn Error + Send + Sync>> {
    if config.computer_use {
        computer::startup::prepare(config.computer_use_permission_timeout).await?;
    }
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
