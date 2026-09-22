use std::{
    fs,
    io::Write,
    path::PathBuf,
    process::{Command, Output, Stdio},
};

use serde_json::{Value, json};

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        loop {
            let mut random = [0_u8; 16];
            getrandom::fill(&mut random).expect("generates fixture directory name");
            let directory = std::env::temp_dir().join(format!(
                "a13n-envd-startup-{:032x}",
                u128::from_ne_bytes(random)
            ));
            match fs::create_dir(&directory) {
                Ok(()) => return Self(fs::canonicalize(directory).unwrap()),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => panic!("cannot allocate startup fixture: {error}"),
            }
        }
    }

    fn command(&self) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        for (name, _) in std::env::vars_os() {
            if name.to_string_lossy().starts_with("A13N_ENVD_") {
                command.env_remove(name);
            }
        }
        command
            .current_dir(&self.0)
            .env("A13N_ENVD_RUNTIME_DIR", self.0.join("runtime"))
            .env("A13N_ENVD_STATE_DIR", self.0.join("installation"));
        command
    }

    fn initialize(&self, mut command: Command) -> Value {
        let payload = json!({"jsonrpc":"2.0", "id":1, "method":"initialize", "params":{
            "supported_protocol_versions":["0.1"], "client":{"name":"startup-test","version":"1"}
        }})
        .to_string();
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        write!(
            child.stdin.take().unwrap(),
            "Content-Length: {}\r\nContent-Type: application/json\r\n\r\n{}",
            payload.len(),
            payload
        )
        .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let frame = String::from_utf8(output.stdout).unwrap();
        let (_, payload) = frame.split_once("\r\n\r\n").expect("initialize response");
        let result: Value = serde_json::from_str(payload).unwrap();
        assert!(result.get("result").is_some(), "{result}");
        assert!(!fs::read_dir(self.0.join("runtime")).unwrap().any(|entry| {
            entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with("generation-")
        }));
        result["result"]["descriptor"].clone()
    }

    fn reject(&self, mut command: Command) -> Output {
        let output = command.stdin(Stdio::null()).output().unwrap();
        assert!(!output.status.success());
        output
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn standalone_device_identity_survives_restart_and_eof_cleans_runtime() {
    let fixture = Fixture::new();
    let first = fixture.initialize(fixture.command());
    let second = fixture.initialize(fixture.command());
    assert_eq!(first["device_id"], second["device_id"]);
    assert!(first["device_id"].as_str().unwrap().starts_with("device-"));
    assert_ne!(first["generation"], second["generation"]);
    assert_eq!(first["directory_discovery"], true);
    assert!(first.get("mounts").is_none());
    assert!(first.get("session_id").is_none());
    let expected = fixture.0.to_string_lossy().replace('\\', "/");
    assert!(
        first["default_working_directory"]
            .as_str()
            .unwrap()
            .ends_with(expected.trim_start_matches("//?/"))
    );
}

#[test]
fn startup_arguments_override_file_metadata_without_materializing_mounts() {
    let fixture = Fixture::new();
    let cwd = fixture.0.join("work");
    fs::create_dir(&cwd).unwrap();
    let config = fixture.0.join("envd.json");
    fs::write(
        &config,
        json!({"device_id":"device-file", "name":"File name", "directory_discovery":false})
            .to_string(),
    )
    .unwrap();
    let mut command = fixture.command();
    command
        .arg("--config")
        .arg(&config)
        .args([
            "--device-id",
            "device-cli",
            "--name",
            "CLI name",
            "--default-working-directory",
        ])
        .arg(&cwd);
    let descriptor = fixture.initialize(command);
    assert_eq!(descriptor["device_id"], "device-cli");
    assert_eq!(descriptor["display_name"], "CLI name");
    assert_eq!(descriptor["directory_discovery"], false);
    assert!(
        descriptor["default_working_directory"]
            .as_str()
            .unwrap()
            .ends_with("/work")
    );
}

#[test]
fn environment_overrides_file_and_cli_overrides_environment() {
    let fixture = Fixture::new();
    let config = fixture.0.join("layers.json");
    fs::write(
        &config,
        json!({
            "device_id":"device-file", "name":"File", "description":"Retained file description",
            "directory_discovery":false, "idle_timeout_ms":60000, "disconnect_grace_ms":1000
        })
        .to_string(),
    )
    .unwrap();
    let layered = || {
        let mut command = fixture.command();
        command
            .arg("--config")
            .arg(&config)
            .env(
                "A13N_ENVD_CONFIG_JSON",
                json!({"device_id":"device-json", "name":"JSON", "idle_timeout_ms":90000})
                    .to_string(),
            )
            .env("A13N_ENVD_DEVICE_ID", "device-env")
            .env("A13N_ENVD_NAME", "Environment")
            .env("A13N_ENVD_DIRECTORY_DISCOVERY", "1");
        command
    };
    let descriptor = fixture.initialize(layered());
    assert_eq!(descriptor["device_id"], "device-env");
    assert_eq!(descriptor["display_name"], "Environment");
    assert_eq!(descriptor["description"], "Retained file description");
    assert_eq!(descriptor["directory_discovery"], true);
    let mut command = layered();
    command.args(["--device-id", "device-cli", "--name", "CLI"]);
    let descriptor = fixture.initialize(command);
    assert_eq!(descriptor["device_id"], "device-cli");
    assert_eq!(descriptor["display_name"], "CLI");
}

#[test]
fn malformed_environment_configuration_is_not_silently_ignored() {
    let fixture = Fixture::new();
    for (name, value) in [
        ("A13N_ENVD_CONFIG_JSON", "[]"),
        (
            "A13N_ENVD_CONFIG_JSON",
            r#"{"execution":{"allow_sudoo":false}}"#,
        ),
        ("A13N_ENVD_ALLOW_SUDO", "yes"),
        ("A13N_ENVD_EXECUTION_UID", "-1"),
        ("A13N_ENVD_EGRESS_ENABLED", ""),
        ("A13N_ENVD_IDLE_TIMEOUT_MS", "0"),
        ("A13N_ENVD_DISCONNECT_GRACE_MS", "not-a-number"),
    ] {
        let mut command = fixture.command();
        command.env(name, value);
        fixture.reject(command);
    }
}

#[test]
fn removed_isolation_command_and_configuration_fields_are_rejected() {
    let fixture = Fixture::new();
    let mut command = fixture.command();
    command.args(["isolation", "probe", "--json"]);
    fixture.reject(command);
    for (field, value) in [
        ("environment_id", json!("old-environment")),
        ("mounts", json!([])),
        ("root_mount_id", json!("workspace")),
    ] {
        let config = fixture.0.join("invalid.json");
        fs::write(&config, json!({field:value}).to_string()).unwrap();
        let mut command = fixture.command();
        command.arg("--config").arg(&config);
        let output = fixture.reject(command);
        assert!(
            String::from_utf8_lossy(&output.stderr).contains(&format!("unknown field `{field}`"))
        );
    }
}

#[test]
fn execution_configuration_rejects_obsolete_isolation_and_invalid_identity() {
    let fixture = Fixture::new();
    for execution in [
        json!({"isolation":"required","network":"deny","uid":12345,"gid":12346}),
        json!({"uid":4294967295_u32,"gid":1000}),
        json!({"uid":1000,"gid":4294967295_u32}),
        json!({"uid":1000}),
        json!({"uid":-1,"gid":1000}),
    ] {
        let config = fixture.0.join("invalid-execution.json");
        fs::write(&config, json!({"execution":execution}).to_string()).unwrap();
        let mut command = fixture.command();
        command.arg("--config").arg(&config);
        let output = fixture.reject(command);
        assert!(String::from_utf8_lossy(&output.stderr).contains("a13n-envd failed"));
    }
}

#[cfg(unix)]
#[test]
fn configuration_hard_links_are_ordinary_account_accessible_files() {
    let fixture = Fixture::new();
    let config = fixture.0.join("envd.json");
    fs::write(&config, json!({"device_id":"device-linked"}).to_string()).unwrap();
    let alias = fixture.0.join("alias.json");
    fs::hard_link(&config, &alias).unwrap();
    let mut command = fixture.command();
    command.arg("--config").arg(alias);
    assert_eq!(fixture.initialize(command)["device_id"], "device-linked");
}
