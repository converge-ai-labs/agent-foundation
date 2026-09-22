#[cfg(target_os = "macos")]
#[test]
fn restricted_session_public_api() {
    let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/sandbox_macos.py");
    let result = std::process::Command::new("python3")
        .arg(script)
        .arg(env!("CARGO_BIN_EXE_a13n-envd"))
        .status()
        .expect("start native Seatbelt integration test");
    assert!(result.success());
}
