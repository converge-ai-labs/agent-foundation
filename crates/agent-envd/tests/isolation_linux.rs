#![cfg(target_os = "linux")]

use std::{
    fs,
    path::PathBuf,
    process::{Command, Stdio},
    time::{SystemTime, UNIX_EPOCH},
};

use serde_json::Value;

fn fixture_directory(label: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock follows Unix epoch")
        .as_nanos();
    let directory = std::env::temp_dir().join(format!(
        "agent-envd-linux-isolation-{label}-{}-{unique}",
        std::process::id()
    ));
    fs::create_dir(&directory).expect("creates fixture directory");
    fs::canonicalize(directory).expect("canonicalizes fixture directory")
}

fn probe(network: &str, identity: Option<(u32, u32)>) -> Value {
    let mut command = Command::new(env!("CARGO_BIN_EXE_agent-envd"));
    command
        .args(["isolation", "probe", "--json"])
        .env_clear()
        .env("AGENT_ENVD_EXECUTION_NETWORK", network);
    if let Some((uid, gid)) = identity {
        command
            .env("AGENT_ENVD_EXECUTION_UID", uid.to_string())
            .env("AGENT_ENVD_EXECUTION_GID", gid.to_string());
    }
    let output = command.output().expect("runs isolation probe");
    assert!(
        output.status.success(),
        "probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("probe emits JSON")
}

#[test]
fn required_isolation_is_default_and_probes_host_and_deny_networking() {
    let host = probe("host", None);
    assert_eq!(host["ready"], true);
    assert_eq!(host["isolation"], true);
    assert_eq!(host["backend"], "linux_bubblewrap");
    assert_eq!(host["network_isolation"], false);
    assert_eq!(host["filesystem_containment"], true);
    assert_eq!(host["process_containment"], true);
    assert_eq!(host["cleanup"], "namespace_complete");

    let deny = probe("deny", None);
    assert_eq!(deny["ready"], true);
    assert_eq!(deny["isolation"], true);
    assert_eq!(deny["network_isolation"], true);
}

#[test]
fn required_probe_applies_configured_payload_identity() {
    let report = probe("host", Some((12_345, 12_346)));
    assert_eq!(report["ready"], true);
    assert_eq!(report["backend"], "linux_bubblewrap");
}

#[test]
fn protected_config_hard_links_fail_closed() {
    let directory = fixture_directory("hard-link");
    let runtime = directory.join("runtime");
    fs::create_dir(&runtime).expect("creates runtime parent");
    let config = directory.join("agent-envd.json");
    let alias = directory.join("config-alias.json");
    fs::write(&config, "{}").expect("writes config");
    fs::hard_link(&config, &alias).expect("creates config hard link");

    let output = Command::new(env!("CARGO_BIN_EXE_agent-envd"))
        .args(["--config", config.to_str().expect("UTF-8 fixture path")])
        .env_clear()
        .env("AGENT_ENVD_ENVIRONMENT_ID", "hard-link-test")
        .env("AGENT_ENVD_RUNTIME_DIR", &runtime)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .expect("starts standalone daemon");
    let _ = fs::remove_dir_all(&directory);

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("protected execution files must not have hard-link aliases"),
        "unexpected failure: {stderr}"
    );
    assert!(
        stderr.contains("required execution isolation preflight failed"),
        "unexpected failure: {stderr}"
    );
    assert!(
        stderr.contains("AGENT_ENVD_EXECUTION_ISOLATION=disabled"),
        "unexpected failure: {stderr}"
    );
    assert!(
        stderr.contains("Only when a trusted outer sandbox owns command containment"),
        "unexpected failure: {stderr}"
    );
}

#[test]
fn standalone_daemon_starts_with_required_isolation_when_override_is_omitted() {
    let status = Command::new(env!("CARGO_BIN_EXE_agent-envd"))
        .env_clear()
        .env("AGENT_ENVD_ENVIRONMENT_ID", "isolation-default-test")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .status()
        .expect("starts standalone daemon");
    assert!(status.success());
}
