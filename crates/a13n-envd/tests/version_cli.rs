use std::process::Command;

#[test]
fn version_is_exact_and_side_effect_free() {
    let output = Command::new(env!("CARGO_BIN_EXE_a13n-envd"))
        .arg("--version")
        .env_clear()
        .output()
        .expect("run a13n-envd --version");

    assert!(output.status.success());
    assert_eq!(
        output.stdout,
        format!("a13n-envd {}\n", env!("CARGO_PKG_VERSION")).as_bytes()
    );
    assert!(output.stderr.is_empty());
}

#[test]
fn version_rejects_additional_arguments() {
    let output = Command::new(env!("CARGO_BIN_EXE_a13n-envd"))
        .args(["--version", "--config", "/tmp/unused.json"])
        .env_clear()
        .output()
        .expect("run invalid a13n-envd --version command");

    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert_eq!(
        String::from_utf8(output.stderr).expect("UTF-8 stderr"),
        "a13n-envd failed: --version cannot be combined with other arguments\n"
    );
}

#[test]
fn update_help_and_check_do_not_start_daemon() {
    let temp = tempfile::tempdir().unwrap();
    let copied = temp.path().join(if cfg!(windows) {
        "a13n-envd.exe"
    } else {
        "a13n-envd"
    });
    std::fs::copy(env!("CARGO_BIN_EXE_a13n-envd"), &copied).unwrap();
    for args in [
        vec!["update", "--help"],
        vec!["update", "--version", env!("CARGO_PKG_VERSION"), "--check"],
        vec!["update", "--version", env!("CARGO_PKG_VERSION")],
    ] {
        let output = Command::new(&copied)
            .args(args)
            .env_clear()
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(output.stderr.is_empty());
    }
    assert_eq!(std::fs::read_dir(temp.path()).unwrap().count(), 1);
    std::fs::write(
        temp.path().join(".a13n-envd-version"),
        env!("CARGO_PKG_VERSION"),
    )
    .unwrap();
    let output = Command::new(&copied)
        .args(["update", "--version", "9.9.9", "--check"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("pinned"));
}
