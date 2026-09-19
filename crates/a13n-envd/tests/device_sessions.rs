use serde_json::{Value, json};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout},
};

struct Device {
    root: PathBuf,
    child: Child,
    input: Option<ChildStdin>,
    output: BufReader<ChildStdout>,
    descriptor: Value,
    next: u64,
}

fn device_path(path: &Path) -> String {
    let path = path.to_str().unwrap().replace('\\', "/");
    if cfg!(windows) {
        format!("/{}", path.strip_prefix("//?/").unwrap_or(&path))
    } else {
        path
    }
}

impl Device {
    async fn start(extra: Value) -> Self {
        let mut random = [0_u8; 8];
        getrandom::fill(&mut random).unwrap();
        let root =
            std::env::temp_dir().join(format!("a13n-device-{:016x}", u64::from_le_bytes(random)));
        std::fs::create_dir_all(root.join("alpha")).unwrap();
        std::fs::create_dir(root.join("beta")).unwrap();
        let mut config =
            json!({"device_id":"device-test", "default_working_directory":root.join("alpha")});
        config
            .as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        std::fs::write(root.join("envd.json"), serde_json::to_vec(&config).unwrap()).unwrap();
        let mut command = tokio::process::Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        command
            .env_clear()
            .env("A13N_ENVD_RUNTIME_DIR", root.join("runtime"))
            .args(["--config", root.join("envd.json").to_str().unwrap()])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);
        #[cfg(windows)]
        if let Some(system_root) = std::env::var_os("SystemRoot") {
            command.env("SystemRoot", system_root);
        }
        let mut child = command.spawn().unwrap();
        let input = child.stdin.take();
        let output = BufReader::new(child.stdout.take().unwrap());
        let mut device = Self {
            root,
            child,
            input,
            output,
            descriptor: Value::Null,
            next: 1,
        };
        let result = device
            .call(
                None,
                "initialize",
                json!({
                    "supported_protocol_versions":["0.1"], "expected_device_id":"device-test",
                    "client":{"name":"integration-test", "version":"0"}
                }),
            )
            .await;
        device.descriptor = result["descriptor"].clone();
        device
    }

    async fn send(&mut self, session: Option<&str>, method: &str, params: Value) -> u64 {
        let id = self.next;
        self.next += 1;
        let mut message = json!({"jsonrpc":"2.0", "id":id, "method":method, "params":params});
        if let Some(session) = session {
            message["eip_session"] = json!(session);
        }
        let bytes = serde_json::to_vec(&message).unwrap();
        let input = self.input.as_mut().unwrap();
        input
            .write_all(
                format!(
                    "Content-Length: {}\r\nContent-Type: application/json\r\n\r\n",
                    bytes.len()
                )
                .as_bytes(),
            )
            .await
            .unwrap();
        input.write_all(&bytes).await.unwrap();
        input.flush().await.unwrap();
        id
    }

    async fn receive(&mut self) -> Value {
        tokio::time::timeout(Duration::from_secs(15), async {
            let mut length = None;
            loop {
                let mut line = String::new();
                assert!(
                    self.output.read_line(&mut line).await.unwrap() != 0,
                    "daemon unexpectedly closed stdout"
                );
                if line == "\r\n" {
                    break;
                }
                if let Some(value) = line.strip_prefix("Content-Length: ") {
                    length = Some(value.trim().parse::<usize>().unwrap());
                }
            }
            let mut bytes = vec![0; length.unwrap()];
            self.output.read_exact(&mut bytes).await.unwrap();
            serde_json::from_slice(&bytes).unwrap()
        })
        .await
        .expect("bounded protocol response")
    }

    async fn response(&mut self, session: Option<&str>, method: &str, params: Value) -> Value {
        let id = self.send(session, method, params).await;
        let response = self.receive().await;
        assert_eq!(response["id"], id);
        assert_eq!(response.get("eip_session").and_then(Value::as_str), session);
        response
    }

    async fn call(&mut self, session: Option<&str>, method: &str, params: Value) -> Value {
        let response = self.response(session, method, params).await;
        assert!(response.get("error").is_none(), "{method}: {response}");
        response["result"].clone()
    }

    async fn open(&mut self, folder: &str) -> String {
        let result = self.call(None, "session.open", json!({
            "expected_device_id":"device-test", "expected_generation":self.descriptor["generation"],
            "protocol_version":"0.1", "working_directory":device_path(&self.root.join(folder)), "required_methods":["file.read_text"]
        })).await;
        let session = result["descriptor"]["session_id"]
            .as_str()
            .unwrap()
            .to_owned();
        assert_eq!(
            result["descriptor"]["working_directory"],
            device_path(&std::fs::canonicalize(self.root.join(folder)).unwrap())
        );
        let ready = self
            .call(Some(&session), "environment.readiness", context("ready"))
            .await;
        assert_eq!(ready["session_id"], session);
        assert_eq!(ready["device_id"], "device-test");
        session
    }

    async fn stop(mut self) {
        self.input.take();
        let status = tokio::time::timeout(Duration::from_secs(15), self.child.wait())
            .await
            .unwrap()
            .unwrap();
        assert!(status.success(), "daemon drain failed: {status}");
        std::fs::remove_dir_all(&self.root).unwrap();
    }
}

fn context(operation: &str) -> Value {
    json!({"context":{"operation_id":operation, "timeout_ms":5000}})
}

fn read_params(operation: &str, path: &Path) -> Value {
    let mut params = context(operation);
    params["path"] = json!({"path":device_path(path)});
    params["line_limit"] = json!(10);
    params["max_line_length"] = json!(1024);
    params
}

#[tokio::test]
async fn discovery_and_independent_sessions_use_device_absolute_paths() {
    let mut device = Device::start(json!({})).await;
    let directories = device.call(None, "directory.list", json!({
        "expected_device_id":"device-test", "expected_generation":device.descriptor["generation"],
        "path":device_path(&device.root), "offset":0, "limit":1
    })).await;
    assert_eq!(directories["entries"][0]["name"], "alpha");
    assert_eq!(directories["next_offset"], 1);
    let alpha = device.open("alpha").await;
    let beta = device.open("beta").await;
    assert_ne!(alpha, beta);
    let outside = device.root.join("outside.txt");
    let mut write = context("same-operation");
    write["path"] = json!({"path":device_path(&outside)});
    write["text"] = json!("outside both working directories\n");
    write["mode"] = json!("create");
    let written = device.call(Some(&alpha), "file.write_text", write).await;
    assert_eq!(written["receipt"]["session_id"], alpha);
    let read = device
        .call(
            Some(&beta),
            "file.read_text",
            read_params("same-operation", &outside),
        )
        .await;
    assert_eq!(read["text"], "outside both working directories\n");
    device.call(Some(&alpha), "session.close", json!({})).await;
    let stale = device
        .response(Some(&alpha), "session.keepalive", json!({}))
        .await;
    assert_eq!(stale["error"]["data"]["error_type"], "not_initialized");
    device
        .call(Some(&beta), "session.keepalive", json!({}))
        .await;
    let read = device
        .call(
            Some(&beta),
            "file.read_text",
            read_params("after-sibling-close", &outside),
        )
        .await;
    assert_eq!(read["text"], "outside both working directories\n");
    device.call(Some(&beta), "session.close", json!({})).await;
    assert!(
        outside.exists(),
        "Session cleanup must not delete working files"
    );
    device.stop().await;
}

#[tokio::test]
async fn failed_open_and_disabled_discovery_do_not_poison_device() {
    let mut device = Device::start(json!({"directory_discovery":false})).await;
    let result = device.response(None, "directory.list", json!({
        "expected_device_id":"device-test", "expected_generation":device.descriptor["generation"],
        "path":device_path(&device.root), "limit":10
    })).await;
    assert_eq!(result["error"]["data"]["error_type"], "unsupported");
    let failed = device.response(None, "session.open", json!({
        "expected_device_id":"device-test", "expected_generation":device.descriptor["generation"],
        "protocol_version":"0.1", "working_directory":device_path(&device.root.join("missing")), "required_methods":[]
    })).await;
    assert_eq!(failed["error"]["data"]["error_type"], "not_found_or_denied");
    let session = device.open("beta").await;
    let wrong_scope = device
        .response(Some(&session), "device.describe", json!({}))
        .await;
    assert_eq!(
        wrong_scope["error"]["data"]["error_type"],
        "invalid_request"
    );
    device
        .call(Some(&session), "session.close", json!({}))
        .await;
    device.stop().await;
}

#[cfg(unix)]
#[tokio::test]
async fn symlink_outside_cwd_is_readable_and_session_processes_have_unique_outputs() {
    let mut device = Device::start(json!({
        "trusted_executable_roots":["/bin", "/usr/bin"],
        "shell_profiles":[{"profile_id":"sh", "display_name":"Shell", "native_executable":"/bin/sh",
            "fixed_arguments":["-c"], "executable_search_roots":["/bin", "/usr/bin"], "max_script_bytes":4096}]
    })).await;
    let outside = device.root.join("outside.txt");
    std::fs::write(&outside, "native target\n").unwrap();
    std::os::unix::fs::symlink(&outside, device.root.join("alpha/link")).unwrap();
    let alpha = device.open("alpha").await;
    let beta = device.open("beta").await;
    let read = device
        .call(
            Some(&alpha),
            "file.read_text",
            read_params("link", &device.root.join("alpha/link")),
        )
        .await;
    assert_eq!(read["text"], "native target\n");
    let mut command = context("command");
    command["request"] = json!({"command":{"kind":"shell", "profile_id":"sh", "script":"pwd"}});
    let first = device
        .send(Some(&alpha), "shell.exec", command.clone())
        .await;
    let second = device.send(Some(&beta), "shell.exec", command).await;
    let one = device.receive().await;
    let two = device.receive().await;
    assert!(one.get("error").is_none(), "{one}");
    assert!(two.get("error").is_none(), "{two}");
    assert!([first, second].contains(&one["id"].as_u64().unwrap()));
    assert_ne!(one["id"], two["id"]);
    assert_ne!(
        one["result"]["output"]["stdout"]["reference"],
        two["result"]["output"]["stdout"]["reference"]
    );
    assert_eq!(one["result"]["status"]["exit_code"], 0);
    assert_eq!(two["result"]["status"]["exit_code"], 0);
    device.call(Some(&alpha), "session.close", json!({})).await;
    device.call(Some(&beta), "session.close", json!({})).await;
    device.stop().await;
}

#[tokio::test]
async fn keepalive_renews_only_its_owner_and_device_reads_do_not_keep_abandoned_sessions() {
    let mut device = Device::start(json!({"idle_timeout_ms":300, "disconnect_grace_ms":100})).await;
    let alpha = device.open("alpha").await;
    let beta = device.open("beta").await;
    for _ in 0..6 {
        tokio::time::sleep(Duration::from_millis(80)).await;
        device
            .call(Some(&beta), "session.keepalive", json!({}))
            .await;
        device.call(None, "device.describe", json!({})).await;
    }
    let expired = device
        .response(Some(&alpha), "session.keepalive", json!({}))
        .await;
    assert_eq!(expired["error"]["data"]["error_type"], "not_initialized");
    device
        .call(Some(&beta), "session.keepalive", json!({}))
        .await;
    device.call(Some(&beta), "session.close", json!({})).await;
    device.stop().await;
}

#[cfg(unix)]
fn shell_config(limits: Value) -> Value {
    json!({
        "trusted_executable_roots":["/bin", "/usr/bin"],
        "shell_profiles":[{"profile_id":"sh", "display_name":"Shell", "native_executable":"/bin/sh",
            "fixed_arguments":["-c"], "executable_search_roots":["/bin", "/usr/bin"], "max_script_bytes":4096}],
        "limits": limits
    })
}

#[cfg(unix)]
fn shell_params(operation: &str, script: &str) -> Value {
    let mut params = context(operation);
    params["request"] = json!({"command":{"kind":"shell", "profile_id":"sh", "script":script}});
    params
}

#[cfg(unix)]
#[tokio::test]
async fn saturated_session_preserves_sibling_progress_keepalive_and_close() {
    let mut device = Device::start(shell_config(json!({
        "max_concurrent_operations":1, "max_device_concurrent_operations":2
    })))
    .await;
    let alpha = device.open("alpha").await;
    let beta = device.open("beta").await;
    let running = device
        .send(
            Some(&alpha),
            "shell.exec",
            shell_params("long", "printf started > started; sleep 30"),
        )
        .await;
    tokio::time::timeout(Duration::from_secs(5), async {
        while !device.root.join("alpha/started").exists() {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let busy = device
        .response(Some(&alpha), "environment.describe", context("busy"))
        .await;
    assert_eq!(busy["error"]["data"]["error_type"], "busy");
    device
        .call(Some(&beta), "environment.describe", context("sibling"))
        .await;
    device
        .call(Some(&alpha), "session.keepalive", json!({}))
        .await;
    let close = device.send(Some(&alpha), "session.close", json!({})).await;
    let first = device.receive().await;
    let second = device.receive().await;
    let responses = [first, second];
    assert!(responses.iter().any(|response| response["id"] == running));
    assert!(
        responses
            .iter()
            .any(|response| response["id"] == close && response.get("error").is_none()),
        "{responses:?}"
    );
    device
        .call(Some(&beta), "session.keepalive", json!({}))
        .await;
    device.call(Some(&beta), "session.close", json!({})).await;
    device.stop().await;
}

#[cfg(unix)]
#[tokio::test]
async fn output_inactivity_is_refreshed_by_access_but_not_keepalive() {
    let mut device = Device::start(shell_config(json!({"operation_record_ttl_ms":200}))).await;
    let session = device.open("alpha").await;
    let result = device
        .call(
            Some(&session),
            "shell.exec",
            shell_params("short", "printf retained"),
        )
        .await;
    let reference = result["output"]["stdout"]["reference"].clone();
    for index in 0..5 {
        tokio::time::sleep(Duration::from_millis(80)).await;
        let mut params = context(&format!("read-{index}"));
        params["reference"] = reference.clone();
        params["start_offset"] = json!(0);
        let output = device.call(Some(&session), "output.read", params).await;
        assert_eq!(output["next_offset"], 8);
    }
    for _ in 0..5 {
        tokio::time::sleep(Duration::from_millis(80)).await;
        device
            .call(Some(&session), "session.keepalive", json!({}))
            .await;
    }
    let mut params = context("after-collection");
    params["reference"] = reference;
    params["start_offset"] = json!(0);
    let expired = device.response(Some(&session), "output.read", params).await;
    assert!(expired.get("error").is_some(), "{expired}");
    device
        .call(Some(&session), "session.close", json!({}))
        .await;
    device.stop().await;
}

#[cfg(unix)]
#[tokio::test]
async fn process_record_pressure_reclaims_completed_output_groups() {
    let mut device = Device::start(shell_config(json!({
        "max_processes":1, "max_process_records":1,
        "max_device_processes":2, "max_device_process_records":2
    })))
    .await;
    let session = device.open("alpha").await;
    let first = device
        .call(
            Some(&session),
            "process.start",
            shell_params("first", "printf one"),
        )
        .await;
    let handle = first["process"]["handle"].clone();
    let mut wait = context("wait");
    wait["handle"] = handle.clone();
    wait["condition"] = json!("tree_cleaned");
    device.call(Some(&session), "process.wait", wait).await;
    // Let the supervisor reader release its native ownership after reap.
    tokio::time::sleep(Duration::from_millis(50)).await;
    device
        .call(
            Some(&session),
            "process.start",
            shell_params("second", "printf two"),
        )
        .await;
    let mut inspect = context("collected");
    inspect["handle"] = handle;
    let old = device
        .response(Some(&session), "process.inspect", inspect)
        .await;
    assert!(old.get("error").is_some(), "{old}");
    device
        .call(Some(&session), "session.close", json!({}))
        .await;
    device.stop().await;
}
