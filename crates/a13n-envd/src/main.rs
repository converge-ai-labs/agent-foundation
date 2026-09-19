use std::{ffi::OsString, time::Duration};

fn main() {
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    if arguments.as_slice() == [OsString::from("--version")] {
        println!("a13n-envd {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    if arguments.first().is_some_and(|value| value == "--version") {
        eprintln!("a13n-envd failed: --version cannot be combined with other arguments");
        std::process::exit(1);
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
    let internal_supervisor = arguments.as_slice() == [OsString::from("--internal-supervisor")];
    let result = if internal_supervisor {
        runtime.block_on(a13n_envd::run_internal_supervisor())
    } else {
        runtime.block_on(a13n_envd::run_from_environment())
    };
    // Tokio's portable stdin adapter may have one blocking read in progress when
    // an operator signal wins the shutdown race. Keep process shutdown bounded.
    runtime.shutdown_timeout(Duration::from_secs(1));
    if let Err(error) = result {
        eprintln!("a13n-envd failed: {error}");
        std::process::exit(1);
    }
}
