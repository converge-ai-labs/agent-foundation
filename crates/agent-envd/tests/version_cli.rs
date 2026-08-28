use std::process::Command;

#[test]
fn version_is_exact_and_side_effect_free() {
    let output = Command::new(env!("CARGO_BIN_EXE_agent-envd"))
        .arg("--version")
        .env_clear()
        .output()
        .expect("run agent-envd --version");

    assert!(output.status.success());
    assert_eq!(
        output.stdout,
        format!("agent-envd {}\n", env!("CARGO_PKG_VERSION")).as_bytes()
    );
    assert!(output.stderr.is_empty());
}

#[test]
fn version_rejects_additional_arguments() {
    let output = Command::new(env!("CARGO_BIN_EXE_agent-envd"))
        .args(["--version", "--config", "/tmp/unused.json"])
        .env_clear()
        .output()
        .expect("run invalid agent-envd --version command");

    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert_eq!(
        String::from_utf8(output.stderr).expect("UTF-8 stderr"),
        "agent-envd failed: --version cannot be combined with other arguments\n"
    );
}
