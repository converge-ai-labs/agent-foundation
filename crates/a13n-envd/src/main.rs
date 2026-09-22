use std::{ffi::OsString, time::Duration};

fn main() {
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    if arguments.as_slice() == [OsString::from("--internal-egress-worker")]
        || arguments.as_slice() == [OsString::from("--internal-egress-ready")]
    {
        if let Err(error) =
            a13n_envd::run_internal_egress_worker(arguments[0] == "--internal-egress-ready")
        {
            eprintln!("a13n-envd failed: isolated Session worker unavailable: {error}");
            std::process::exit(1);
        }
        return;
    }
    if arguments.as_slice() == [OsString::from("--internal-session-worker")] {
        if let Err(error) = a13n_envd::run_internal_session_worker() {
            eprintln!("a13n-envd failed: Session worker unavailable: {error}");
            std::process::exit(1);
        }
        return;
    }
    if arguments.as_slice() == [OsString::from("--internal-directory-worker")] {
        if let Err(error) = a13n_envd::run_internal_directory_worker() {
            eprintln!("a13n-envd failed: directory worker unavailable: {error}");
            std::process::exit(1);
        }
        return;
    }
    if arguments.as_slice() == [OsString::from("--version")] {
        println!("a13n-envd {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if arguments.first().is_some_and(|value| value == "--version") {
        eprintln!("a13n-envd failed: --version cannot be combined with other arguments");
        std::process::exit(1);
    }
    if arguments.as_slice() != [OsString::from("--internal-supervisor")] {
        if let Err(error) = a13n_envd::run_from_environment() {
            eprintln!("a13n-envd failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => {
            eprintln!("a13n-envd failed to start its async runtime: {error}");
            std::process::exit(1);
        }
    };
    let result = runtime.block_on(a13n_envd::run_internal_supervisor());
    // Tokio's portable stdin adapter may have one blocking read in progress when
    // an operator signal wins the shutdown race. Keep process shutdown bounded.
    runtime.shutdown_timeout(Duration::from_secs(1));
    if let Err(error) = result {
        eprintln!("a13n-envd failed: {error}");
        std::process::exit(1);
    }
}
