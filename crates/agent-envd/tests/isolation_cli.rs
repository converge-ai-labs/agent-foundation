use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Output},
    time::{SystemTime, UNIX_EPOCH},
};

use serde_json::Value;

fn fixture_directory(label: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock follows Unix epoch")
        .as_nanos();
    let directory = std::env::temp_dir().join(format!(
        "agent-envd-isolation-cli-{label}-{}-{unique}",
        std::process::id()
    ));
    fs::create_dir(&directory).expect("creates fixture directory");
    fs::canonicalize(directory).expect("canonicalizes fixture directory")
}

fn run_probe(config: Option<&Path>, environment: &[(&str, &str)]) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_agent-envd"));
    command.args(["isolation", "probe", "--json"]).env_clear();
    if let Some(config) = config {
        command.arg("--config").arg(config);
    }
    for (name, value) in environment {
        command.env(name, value);
    }
    command.output().expect("runs isolation probe")
}

#[test]
fn disabled_probe_reports_outer_host_without_native_containment() {
    let output = run_probe(
        None,
        &[
            ("AGENT_ENVD_EXECUTION_ISOLATION", "disabled"),
            ("AGENT_ENVD_EXECUTION_NETWORK", "host"),
        ],
    );
    assert!(
        output.status.success(),
        "probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value = serde_json::from_slice(&output.stdout).expect("probe emits JSON");
    assert_eq!(report["ready"], true);
    assert_eq!(report["isolation"], false);
    assert_eq!(report["backend"], "outer_host");
    assert_eq!(report["network_isolation"], false);
    assert_eq!(report["filesystem_containment"], false);
    assert_eq!(report["process_containment"], false);
    assert_eq!(report["cleanup"], "outer_host");
}

#[test]
fn execution_environment_fields_override_file_fields_independently() {
    let directory = fixture_directory("precedence");
    let extra = directory.join("extra");
    fs::create_dir(&extra).expect("creates extra read-only fixture");
    let config = directory.join("agent-envd.json");
    fs::write(
        &config,
        serde_json::to_vec(&serde_json::json!({
            "execution": {
                "isolation": "required",
                "network": "deny",
                "extra_read_only_paths": [extra],
            }
        }))
        .expect("serializes fixture"),
    )
    .expect("writes fixture");

    let output = run_probe(
        Some(&config),
        &[
            ("AGENT_ENVD_EXECUTION_ISOLATION", "disabled"),
            ("AGENT_ENVD_EXECUTION_NETWORK", "host"),
            ("AGENT_ENVD_EXECUTION_EXTRA_READ_ONLY_PATHS", "[]"),
        ],
    );
    let _ = fs::remove_dir_all(&directory);
    assert!(
        output.status.success(),
        "probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value = serde_json::from_slice(&output.stdout).expect("probe emits JSON");
    assert_eq!(report["backend"], "outer_host");
}

#[test]
fn payload_identity_configuration_fails_closed() {
    let cases = [
        (
            &[
                ("AGENT_ENVD_EXECUTION_ISOLATION", "required"),
                ("AGENT_ENVD_EXECUTION_UID", "1000"),
            ][..],
            "must be provided together",
        ),
        (
            &[
                ("AGENT_ENVD_EXECUTION_ISOLATION", "required"),
                ("AGENT_ENVD_EXECUTION_UID", "0"),
                ("AGENT_ENVD_EXECUTION_GID", "1000"),
            ][..],
            "must be a positive integer",
        ),
        (
            &[
                ("AGENT_ENVD_EXECUTION_ISOLATION", "disabled"),
                ("AGENT_ENVD_EXECUTION_UID", "1000"),
                ("AGENT_ENVD_EXECUTION_GID", "1000"),
            ][..],
            "require required Linux native isolation",
        ),
    ];

    for (environment, expected_error) in cases {
        let output = run_probe(None, environment);
        assert!(!output.status.success());
        assert!(
            String::from_utf8_lossy(&output.stderr).contains(expected_error),
            "unexpected failure: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
}

#[test]
fn invalid_execution_configuration_fails_closed() {
    let directory = fixture_directory("invalid");
    let extra = directory.join("extra");
    fs::create_dir(&extra).expect("creates extra read-only fixture");
    let cases = [
        (
            "disabled-deny",
            serde_json::json!({
                "execution": {"isolation": "disabled", "network": "deny"}
            }),
            "disabled execution isolation supports only",
        ),
        (
            "disabled-extra",
            serde_json::json!({
                "execution": {
                    "isolation": "disabled",
                    "network": "host",
                    "extra_read_only_paths": [extra],
                }
            }),
            "extra read-only execution paths require native isolation",
        ),
        (
            "unknown-field",
            serde_json::json!({
                "execution": {
                    "isolation": "disabled",
                    "network": "host",
                    "backend": "macos_seatbelt",
                }
            }),
            "unknown field `backend`",
        ),
    ];

    for (label, value, expected_error) in cases {
        let config = directory.join(format!("{label}.json"));
        fs::write(
            &config,
            serde_json::to_vec(&value).expect("serializes fixture"),
        )
        .expect("writes fixture");
        let output = run_probe(Some(&config), &[]);
        assert!(!output.status.success(), "invalid case succeeded: {label}");
        assert!(
            String::from_utf8_lossy(&output.stderr).contains(expected_error),
            "unexpected failure for {label}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    let _ = fs::remove_dir_all(directory);
}
