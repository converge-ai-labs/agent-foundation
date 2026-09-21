#[cfg(target_os = "linux")]
#[test]
#[ignore = "requires a disposable privileged Linux sandbox, Python/curl, and outbound HTTPS"]
fn controlled_session_public_api() {
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/egress_linux.py");
    let result = std::process::Command::new("python3")
        .arg(script)
        .arg(env!("CARGO_BIN_EXE_a13n-envd"))
        .arg("--local-network-fixture")
        .status()
        .expect("start egress integration test");
    assert!(result.success());
}
