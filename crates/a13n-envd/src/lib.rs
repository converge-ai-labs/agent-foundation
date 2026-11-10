use std::{error::Error, sync::Arc};

mod capacity;
mod computer;
mod config;
mod connect;
mod daemon;
mod daemon_supervisor;
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
mod monitoring;
mod operation;
mod process;
mod resource;
mod retention;
mod runtime;
mod shutdown;
mod stdio;
mod supervisor;
mod transfer;
mod websocket;
#[cfg(windows)]
mod windows_directory;
#[cfg(windows)]
mod windows_job;
#[cfg(windows)]
mod windows_path;

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
    #[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
    {
        execution::worker::run_session()
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        Err(std::io::Error::other(
            "Session workers are unavailable on this platform",
        ))
    }
}

/// Runs the private Linux VM guest engine without same-kernel descriptor passing.
#[doc(hidden)]
#[cfg(target_os = "linux")]
pub fn run_internal_vm_worker() -> std::io::Result<()> {
    execution::worker::guest::run()
}

/// Runs a one-shot directory observation under the execution identity.
#[doc(hidden)]
pub fn run_internal_directory_worker() -> std::io::Result<()> {
    #[cfg(any(target_os = "linux", target_os = "macos", target_os = "windows"))]
    {
        execution::discovery::run()
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        Err(std::io::Error::other(
            "Session workers are unavailable on this platform",
        ))
    }
}

/// Runs the strict native Windows MXC launcher before ordinary daemon startup.
#[doc(hidden)]
#[cfg(windows)]
pub fn run_internal_mxc_launcher() -> std::io::Result<i32> {
    execution::windows::run_launcher()
}

/// Runs one a13n-envd instance from trusted process configuration.
pub fn run_from_environment() -> Result<(), Box<dyn Error + Send + Sync>> {
    let mut arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if matches!(
        arguments.first().and_then(|value| value.to_str()),
        Some("daemon" | "supervise")
    ) && arguments.get(1).is_some_and(|value| value == "--help")
        && arguments.len() == 2
    {
        println!("{}", daemon_supervisor::HELP);
        return Ok(());
    }
    if arguments.first().is_some_and(|value| value == "supervise") {
        return daemon_supervisor::run(&arguments[1..]);
    }
    let supervised = arguments
        .first()
        .is_some_and(|value| value == "--internal-supervised-daemon");
    let foreground_daemon = supervised || arguments.first().is_some_and(|value| value == "daemon");
    if foreground_daemon {
        arguments.remove(0);
        let mut config = config::Config::from_environment(arguments)?;
        config.supervised = supervised;
        return run(config);
    }
    if arguments
        .first()
        .is_some_and(|value| value == "connect-link")
    {
        let link = arguments
            .get(1)
            .and_then(|value| value.to_str())
            .ok_or("connect-link requires a Console link")?;
        let connection = connect::service::link::arguments(link)?;
        if arguments.get(2).is_some_and(|value| value == "--recognize") {
            let mut options = connection;
            options.extend(arguments.into_iter().skip(3));
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()?;
            println!(
                "{}",
                runtime.block_on(connect::service::recognize_link(options))?
            );
            return Ok(());
        }
        if arguments.len() == 3 && (arguments[2] == "--check" || arguments[2] == "--check-json") {
            if arguments[2] == "--check-json" {
                println!(
                    "{}",
                    serde_json::json!({
                        "service": connection[0].to_string_lossy(),
                        "workspace": connection[2].to_string_lossy(),
                        "host": connection[4].to_string_lossy(),
                    })
                );
            } else {
                println!(
                    "Service: {}\nWorkspace: {}",
                    connection[0].to_string_lossy(),
                    connection[2].to_string_lossy()
                );
            }
            return Ok(());
        }
        let mut expanded = vec![std::ffi::OsString::from("connect-service")];
        expanded.extend(connection);
        expanded.extend(arguments.into_iter().skip(2));
        arguments = expanded;
    }
    if arguments
        .first()
        .is_some_and(|value| value == "connect-service")
        && arguments.iter().any(|value| value == "--launcher")
    {
        arguments.retain(|value| value != "--launcher");
        connect::launcher::enable()?;
    }
    if arguments.as_slice() == [std::ffi::OsString::from("--help")]
        || arguments.as_slice()
            == [
                std::ffi::OsString::from("connect"),
                std::ffi::OsString::from("--help"),
            ]
    {
        println!("{}", connect::HELP);
        println!("\n{}", daemon_supervisor::HELP);
        println!("\nConnect to Service: a13n-envd connect-service --help");
        println!("\nUpdate the native installation: a13n-envd update --help");
        return Ok(());
    }
    if arguments.as_slice() == ["connect-service", "--help"] {
        println!("{}", connect::service::HELP);
        return Ok(());
    }
    let service_connection = arguments
        .first()
        .is_some_and(|argument| argument == "connect-service");
    let config = if service_connection
        || arguments
            .first()
            .is_some_and(|argument| argument == "connect")
    {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        let options = arguments.into_iter().skip(1).collect();
        let config = if service_connection {
            runtime.block_on(connect::service::prepare(options))?
        } else {
            runtime.block_on(connect::prepare(options))?
        };
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
        config::Config::from_environment(arguments)?
    };
    run(config)
}

fn run(config: config::Config) -> Result<(), Box<dyn Error + Send + Sync>> {
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
    let monitoring = daemon.start_monitoring()?;
    let result = match &config.transport {
        config::TransportConfig::Stdio => stdio::serve(daemon, &config).await,
        config::TransportConfig::Http(http_config) => {
            http::serve(daemon, &config, http_config).await
        }
        config::TransportConfig::ReverseWebSocket(websocket_config) => {
            websocket::serve(daemon, &config, websocket_config).await
        }
    };
    if let Some(monitoring) = monitoring {
        monitoring.shutdown().await;
    }
    result?;
    Ok(())
}
