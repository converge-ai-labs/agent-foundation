#![cfg(target_os = "macos")]

use std::process::{Command, Stdio};

use serde_json::Value;

fn probe(network: &str) -> Value {
    let output = Command::new(env!("CARGO_BIN_EXE_agent-envd"))
        .args(["isolation", "probe", "--json"])
        .env_remove("AGENT_ENVD_EXECUTION_ISOLATION")
        .env("AGENT_ENVD_EXECUTION_NETWORK", network)
        .output()
        .expect("runs isolation probe");
    assert!(
        output.status.success(),
        "probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("probe emits JSON")
}

#[test]
fn required_isolation_is_default_and_probes_host_and_deny_networking() {
    let host = probe("host");
    assert_eq!(host["ready"], true);
    assert_eq!(host["isolation"], true);
    assert_eq!(host["backend"], "macos_seatbelt");
    assert_eq!(host["network_isolation"], false);

    let deny = probe("deny");
    assert_eq!(deny["ready"], true);
    assert_eq!(deny["isolation"], true);
    assert_eq!(deny["network_isolation"], true);
}

#[test]
fn standalone_daemon_starts_with_required_isolation_when_override_is_omitted() {
    let status = Command::new(env!("CARGO_BIN_EXE_agent-envd"))
        .env("AGENT_ENVD_ENVIRONMENT_ID", "isolation-default-test")
        .env_remove("AGENT_ENVD_EXECUTION_ISOLATION")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .status()
        .expect("starts standalone daemon");
    assert!(status.success());
}
