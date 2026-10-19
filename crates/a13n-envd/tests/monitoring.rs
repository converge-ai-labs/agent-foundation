#![cfg(target_os = "linux")]

use serde_json::{Value, json};
use std::{fs, path::PathBuf, process::Stdio, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    time::{sleep, timeout},
};

struct Fixture {
    root: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let mut random = [0; 16];
        getrandom::fill(&mut random).unwrap();
        let root = std::env::temp_dir().join(format!(
            "a13n-monitoring-{:032x}",
            u128::from_ne_bytes(random)
        ));
        fs::create_dir(&root).unwrap();
        Self { root }
    }
    fn options(&self) -> Value {
        json!({"sample_interval_ms":1000, "filesystems":{"workspace":self.root, "missing":self.root.join("missing")}})
    }
    fn command(&self, monitoring: Value) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        command.env_clear().env("A13N_ENVD_RUNTIME_DIR", self.root.join("runtime"))
            .env("A13N_ENVD_CONFIG_JSON", json!({"device_id":"metrics-device", "default_working_directory":self.root, "monitoring":monitoring}).to_string())
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).kill_on_drop(true);
        command
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}
struct Peer {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    generation: Value,
    id: u64,
}
impl Peer {
    async fn start(mut command: Command) -> Self {
        let mut child = command.spawn().unwrap();
        let input = child.stdin.take().unwrap();
        let output = BufReader::new(child.stdout.take().unwrap());
        let mut peer = Self {
            child,
            input,
            output,
            generation: Value::Null,
            id: 0,
        };
        let initialized = peer.call("initialize", json!({"supported_protocol_versions":["0.3"], "client":{"name":"metrics-test", "version":"1"}})).await;
        peer.generation = initialized["descriptor"]["generation"].clone();
        peer
    }
    async fn call(&mut self, method: &str, params: Value) -> Value {
        self.id += 1;
        let payload = serde_json::to_vec(
            &json!({"jsonrpc":"2.0", "id":self.id, "method":method, "params":params}),
        )
        .unwrap();
        timeout(Duration::from_secs(5), async {
            self.input
                .write_all(format!("Content-Length: {}\r\n\r\n", payload.len()).as_bytes())
                .await
                .unwrap();
            self.input.write_all(&payload).await.unwrap();
            self.input.flush().await.unwrap();
            let mut length = None;
            loop {
                let mut line = String::new();
                assert!(self.output.read_line(&mut line).await.unwrap() > 0);
                if line == "\r\n" {
                    break;
                }
                if let Some(value) = line.strip_prefix("Content-Length:") {
                    length = Some(value.trim().parse::<usize>().unwrap());
                }
            }
            let mut body = vec![0; length.unwrap()];
            self.output.read_exact(&mut body).await.unwrap();
            let response: Value = serde_json::from_slice(&body).unwrap();
            assert!(response.get("error").is_none(), "{response}");
            response["result"].clone()
        })
        .await
        .unwrap()
    }
    async fn metrics(&mut self) -> Value {
        self.call(
            "device.metrics",
            json!({"expected_device_id":"metrics-device", "expected_generation":self.generation}),
        )
        .await
    }
    async fn ready(&mut self) -> Value {
        timeout(Duration::from_secs(5), async {
            loop {
                let report = self.metrics().await;
                if report.get("sample").is_some() {
                    break report;
                }
                sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .unwrap()
    }
}
async fn eof(peer: Peer) {
    drop(peer.input);
    drop(peer.output);
    let output = timeout(Duration::from_secs(5), peer.child.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[tokio::test]
async fn standalone_daemon_keeps_monitoring_opt_in() {
    for config in [json!({}), json!({"monitoring":null})] {
        let fixture = Fixture::new();
        let mut command = fixture.command(Value::Null);
        command.env("A13N_ENVD_CONFIG_JSON", config.to_string());
        let mut peer = Peer::start(command).await;
        let described = peer.call("device.describe", json!({})).await;
        assert!(
            !described["descriptor"]["available_methods"]
                .as_array()
                .unwrap()
                .contains(&json!("device.metrics"))
        );
        eof(peer).await;
    }
}

#[tokio::test]
async fn selected_cgroup_is_separate_from_system_and_missing_fields_recover() {
    let fixture = Fixture::new();
    let cgroup = fixture.root.join("cgroup");
    fs::create_dir(&cgroup).unwrap();
    fs::write(
        cgroup.join("cpu.stat"),
        "usage_usec 12345\nuser_usec 10000\nsystem_usec 2345\nnr_periods 100\nnr_throttled 12\nthrottled_usec 7654\n",
    )
    .unwrap();
    fs::write(cgroup.join("cpu.max"), "150000 100000\n").unwrap();
    fs::write(cgroup.join("memory.max"), "max\n").unwrap();
    fs::write(
        cgroup.join("memory.events"),
        "low 0\nhigh 7\nmax 4\noom 2\noom_kill 1\n",
    )
    .unwrap();
    fs::write(cgroup.join("cpu.pressure"), "some avg10=1.25 avg60=2.50 avg300=3.75 total=123456\nfull avg10=0.10 avg60=0.20 avg300=0.30 total=789\n").unwrap();
    fs::write(
        cgroup.join("memory.pressure"),
        "some avg10=0 avg60=0 avg300=0 total=0\nfull avg10=NaN avg60=0 avg300=0 total=0\n",
    )
    .unwrap();
    let mut options = fixture.options();
    options["cgroup_v2_directory"] = json!(cgroup);
    let mut child = Peer::start(fixture.command(options)).await;
    let first = child.ready().await;
    let metrics = &first["sample"]["cgroup_v2"];
    assert_eq!(metrics["cpu_usage_usec"], 12345);
    assert_eq!(metrics["cpu_user_usec"], 10000);
    assert_eq!(metrics["cpu_system_usec"], 2345);
    assert_eq!(metrics["cpu_periods_total"], 100);
    assert_eq!(metrics["cpu_throttled_periods_total"], 12);
    assert_eq!(metrics["cpu_throttled_usec"], 7654);
    assert_eq!(metrics["memory_events"]["low"], 0);
    assert_eq!(metrics["memory_events"]["high"], 7);
    assert_eq!(metrics["memory_events"]["oom_kill"], 1);
    assert!(metrics["memory_events"]["oom_group_kill"].is_null());
    assert_eq!(metrics["pressure"]["cpu"]["some"]["avg10_percent"], 1.25);
    assert_eq!(metrics["pressure"]["cpu"]["full"]["total_usec"], 789);
    assert_eq!(metrics["pressure"]["memory"]["some"]["total_usec"], 0);
    assert!(metrics["pressure"]["memory"]["full"].is_null());
    assert!(metrics["pressure"]["io"].is_null());
    assert_eq!(
        metrics["cpu_max"],
        json!({"kind":"limited","quota_usec":150000,"period_usec":100000})
    );
    assert_eq!(metrics["memory_max"], json!({"kind":"unlimited"}));
    assert!(metrics["memory_current_bytes"].is_null());
    assert!(
        first["sample"]["system"]["memory_total_bytes"]
            .as_u64()
            .unwrap()
            > 0
    );
    // A reset remains a raw counter decrease, not a fabricated rate or zero.
    fs::write(
        cgroup.join("cpu.stat"),
        "usage_usec 5\nuser_usec 3\nsystem_usec 2\nnr_throttled broken\n",
    )
    .unwrap();
    fs::write(cgroup.join("memory.events"), "high 8\noom_kill broken\n").unwrap();
    fs::write(
        cgroup.join("io.pressure"),
        "some avg10=4 avg60=5 avg300=6 total=100\n",
    )
    .unwrap();
    fs::write(cgroup.join("memory.current"), "4096\n").unwrap();
    timeout(Duration::from_secs(4), async {
        loop {
            let report = child.metrics().await;
            if report["sample"]["sequence"] != first["sample"]["sequence"] {
                let metrics = &report["sample"]["cgroup_v2"];
                assert_eq!(metrics["memory_current_bytes"], 4096);
                assert_eq!(metrics["cpu_usage_usec"], 5);
                assert!(metrics["cpu_periods_total"].is_null());
                assert!(metrics["cpu_throttled_periods_total"].is_null());
                assert!(metrics["cpu_throttled_usec"].is_null());
                assert_eq!(metrics["memory_events"]["high"], 8);
                assert!(metrics["memory_events"]["oom_kill"].is_null());
                assert_eq!(metrics["pressure"]["io"]["some"]["total_usec"], 100);
                assert!(metrics["pressure"]["io"]["full"].is_null());
                break;
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    eof(child).await;
}

#[tokio::test]
async fn stalled_collection_keeps_one_aging_snapshot_and_recovers() {
    use std::{
        ffi::CString,
        io::Write,
        os::unix::{ffi::OsStrExt, fs::OpenOptionsExt},
    };

    let fixture = Fixture::new();
    let cgroup = fixture.root.join("cgroup");
    fs::create_dir(&cgroup).unwrap();
    let cpu = cgroup.join("cpu.stat");
    fs::write(&cpu, "usage_usec 10\n").unwrap();
    let mut options = fixture.options();
    options["cgroup_v2_directory"] = json!(cgroup);
    let mut child = Peer::start(fixture.command(options)).await;
    let first = child.ready().await;

    // A FIFO deterministically holds the next native read in flight without
    // requiring a stalled network mount or blocking the test's async runtime.
    fs::remove_file(&cpu).unwrap();
    let path = CString::new(cpu.as_os_str().as_bytes()).unwrap();
    // SAFETY: path is NUL-terminated and remains alive for the call.
    assert_eq!(unsafe { libc::mkfifo(path.as_ptr(), 0o600) }, 0);
    let mut writer = timeout(Duration::from_secs(5), async {
        loop {
            match fs::OpenOptions::new()
                .write(true)
                .custom_flags(libc::O_NONBLOCK)
                .open(&cpu)
            {
                Ok(writer) => break writer,
                Err(error) if error.raw_os_error() == Some(libc::ENXIO) => {
                    sleep(Duration::from_millis(20)).await
                }
                Err(error) => panic!("opening observation FIFO: {error}"),
            }
        }
    })
    .await
    .unwrap();

    // The EIP connection remains responsive, and repeated reads do not turn
    // the retained snapshot into new observations even after its interval.
    timeout(Duration::from_secs(4), async {
        loop {
            let report = child.metrics().await;
            assert_eq!(report["sample"]["sequence"], first["sample"]["sequence"]);
            assert_eq!(report["sample"]["cgroup_v2"], first["sample"]["cgroup_v2"]);
            if report["sample"]["sample_age_ms"].as_u64().unwrap()
                > 2 * report["sample_interval_ms"].as_u64().unwrap()
            {
                break;
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();

    // Replace the path before releasing the in-flight FIFO read, so every
    // later collection sees the ordinary source without a missing-file gap.
    let replacement = cgroup.join("cpu.next");
    fs::write(&replacement, "usage_usec 20\n").unwrap();
    fs::rename(replacement, &cpu).unwrap();
    writer.write_all(b"usage_usec 20\n").unwrap();
    drop(writer);
    timeout(Duration::from_secs(5), async {
        loop {
            let report = child.metrics().await;
            if report["sample"]["sequence"].as_u64().unwrap()
                > first["sample"]["sequence"].as_u64().unwrap()
                && report["sample"]["sample_age_ms"].as_u64().unwrap()
                    < report["sample_interval_ms"].as_u64().unwrap()
            {
                assert_eq!(report["sample"]["cgroup_v2"]["cpu_usage_usec"], 20);
                break;
            }
            sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    eof(child).await;
}

#[tokio::test]
async fn monitoring_configuration_supports_partial_json_overlays() {
    let fixture = Fixture::new();
    let mut options = fixture.options();
    options["sample_interval_ms"] = json!(10000);
    fs::write(
        fixture.root.join("config.json"),
        json!({"device_id":"metrics-device", "monitoring":options}).to_string(),
    )
    .unwrap();
    let mut command = fixture.command(Value::Null);
    command
        .arg("--config")
        .arg(fixture.root.join("config.json"))
        .env(
            "A13N_ENVD_CONFIG_JSON",
            r#"{"monitoring":{"sample_interval_ms":1000}}"#,
        );
    let mut child = Peer::start(command).await;
    let report = child.ready().await;
    assert_eq!(report["sample_interval_ms"], 1000);
    eof(child).await;
}
