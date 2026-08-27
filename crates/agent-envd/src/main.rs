use std::{ffi::OsString, path::PathBuf, time::Duration};

fn main() {
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    if let Some(result) = run_private_mode(&arguments) {
        match result {
            Ok(code) => std::process::exit(code),
            Err(error) => {
                eprintln!("agent-envd failed: {error}");
                std::process::exit(1);
            }
        }
    }
    if arguments.first().is_some_and(|value| value == "isolation") {
        match isolation_probe_config(&arguments).and_then(agent_envd::run_isolation_probe) {
            Ok(()) => return,
            Err(error) => {
                eprintln!("agent-envd failed: {error}");
                std::process::exit(1);
            }
        }
    }

    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => {
            eprintln!("agent-envd failed to start its async runtime: {error}");
            std::process::exit(1);
        }
    };
    let internal_supervisor = arguments.as_slice() == [OsString::from("--internal-supervisor")];
    let result = if internal_supervisor {
        runtime.block_on(agent_envd::run_internal_supervisor())
    } else {
        runtime.block_on(agent_envd::run_from_environment())
    };
    // Tokio's portable stdin adapter may have one blocking read in progress when
    // an operator signal wins the shutdown race. Keep process shutdown bounded.
    runtime.shutdown_timeout(Duration::from_secs(1));
    if let Err(error) = result {
        eprintln!("agent-envd failed: {error}");
        std::process::exit(1);
    }
}

fn run_private_mode(
    arguments: &[OsString],
) -> Option<Result<i32, Box<dyn std::error::Error + Send + Sync>>> {
    let (mode, rest) = arguments.split_first()?;
    match mode.to_str()? {
        "--internal-isolation-probe" => Some(agent_envd::run_internal_isolation_probe(rest)),
        "--internal-isolation-probe-child" => {
            Some(agent_envd::run_internal_isolation_probe_child(rest))
        }
        "--internal-isolation-probe-sleeper" => {
            Some(agent_envd::run_internal_isolation_probe_sleeper(rest))
        }
        _ => None,
    }
}

fn isolation_probe_config(
    arguments: &[OsString],
) -> Result<Option<PathBuf>, Box<dyn std::error::Error + Send + Sync>> {
    if arguments.len() < 3 || arguments[0] != "isolation" || arguments[1] != "probe" {
        return Err(
            "usage: agent-envd isolation probe [--config <absolute-json-path>] --json".into(),
        );
    }
    let mut json = false;
    let mut config = None;
    let mut index = 2;
    while index < arguments.len() {
        if arguments[index] == "--json" {
            if json {
                return Err("--json may be specified only once".into());
            }
            json = true;
            index += 1;
        } else if arguments[index] == "--config" {
            if config.is_some() || index + 1 >= arguments.len() {
                return Err("--config requires exactly one path".into());
            }
            let path = PathBuf::from(&arguments[index + 1]);
            if !path.is_absolute() {
                return Err("--config path must be absolute".into());
            }
            config = Some(path);
            index += 2;
        } else {
            return Err("unexpected isolation probe argument".into());
        }
    }
    if !json {
        return Err("agent-envd isolation probe requires --json".into());
    }
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isolation_probe_cli_accepts_json_with_optional_absolute_config() {
        assert_eq!(
            isolation_probe_config(&["isolation".into(), "probe".into(), "--json".into(),])
                .expect("probe CLI"),
            None
        );
        let config = std::env::temp_dir().join("envd.json");
        assert_eq!(
            isolation_probe_config(&[
                "isolation".into(),
                "probe".into(),
                "--config".into(),
                config.clone().into_os_string(),
                "--json".into(),
            ])
            .expect("probe CLI"),
            Some(config)
        );
    }
}
