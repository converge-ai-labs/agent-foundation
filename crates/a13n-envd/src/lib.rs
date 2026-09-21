use std::{error::Error, sync::Arc};

mod capacity;
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
        egress::worker::run_internal(ready)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = ready;
        Err(std::io::Error::other("egress isolation requires Linux"))
    }
}

/// Runs one a13n-envd instance from trusted process configuration.
pub async fn run_from_environment() -> Result<(), Box<dyn Error + Send + Sync>> {
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
        connect::prepare(arguments.into_iter().skip(1).collect()).await?
    } else {
        config::Config::from_environment()?
    };
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
