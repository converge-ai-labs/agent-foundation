use std::{
    fs,
    net::{SocketAddr, TcpListener},
    process::{Child, Command, ExitStatus, Stdio},
    thread::sleep,
    time::{Duration, Instant},
};

use serde_json::{Value, json};

struct Fixture {
    root: tempfile::TempDir,
    address: SocketAddr,
    client: reqwest::blocking::Client,
}

impl Fixture {
    fn new() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        #[cfg(windows)]
        ensure_console();
        let root = tempfile::tempdir().unwrap();
        let credential = root.path().join("credential");
        fs::write(&credential, "native-supervision-test-credential").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(credential, fs::Permissions::from_mode(0o600)).unwrap();
        }
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        Self {
            root,
            address,
            client: reqwest::blocking::Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(2))
                .build()
                .unwrap(),
        }
    }

    fn command(&self, mode: &str) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_a13n-envd"));
        for (name, _) in std::env::vars_os() {
            if name.to_string_lossy().starts_with("A13N_ENVD_") {
                command.env_remove(name);
            }
        }
        command
            .arg(mode)
            .current_dir(self.root.path())
            .env("TOKIO_WORKER_THREADS", "2")
            .env("A13N_ENVD_TRANSPORT", "http")
            .env("A13N_ENVD_RUNTIME_DIR", self.root.path().join("runtime"))
            .env("A13N_ENVD_STATE_DIR", self.root.path().join("installation"))
            .env("A13N_ENVD_FULL_CONTROL", "1")
            .env("A13N_ENVD_HTTP_BIND", self.address.to_string())
            .env("A13N_ENVD_HTTP_PLAINTEXT_SCOPE", "loopback")
            .env(
                "A13N_ENVD_HTTP_CREDENTIAL_FILE",
                self.root.path().join("credential"),
            )
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(fs::File::create(self.root.path().join("stderr")).unwrap());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            use windows_sys::Win32::System::Threading::CREATE_NEW_PROCESS_GROUP;
            command.creation_flags(CREATE_NEW_PROCESS_GROUP);
        }
        command
    }

    fn start(&self, mode: &str) -> Running {
        Running(self.command(mode).spawn().unwrap())
    }

    fn log(&self) -> String {
        fs::read_to_string(self.root.path().join("stderr")).unwrap_or_default()
    }

    fn daemon_pids(&self) -> Vec<u32> {
        self.log()
            .lines()
            .filter_map(|line| {
                line.strip_prefix("a13n-envd supervise: started daemon pid=")?
                    .parse()
                    .ok()
            })
            .collect()
    }

    fn request(&self, session: Option<&str>, method: &str, params: Value) -> Option<Value> {
        let mut request = json!({"jsonrpc":"2.0", "id":1, "method":method, "params":params});
        if let Some(session) = session {
            request["eip_session"] = json!(session);
            request["eip_owner_epoch"] = json!(1);
        }
        let response = self
            .client
            .post(format!("http://{}/eip/control", self.address))
            .bearer_auth("native-supervision-test-credential")
            .header("Content-Type", "application/json")
            .body(request.to_string())
            .send()
            .ok()?;
        assert!(response.status().is_success(), "HTTP {}", response.status());
        serde_json::from_slice(&response.bytes().unwrap()).ok()
    }

    fn descriptor(&self, previous: Option<&Value>) -> Value {
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            if let Some(response) = self.request(
                None,
                "initialize",
                json!({"client":{"name":"supervision-test","version":"1"}}),
            ) {
                let descriptor = &response["result"]["descriptor"];
                if !descriptor.is_null() && previous != Some(&descriptor["generation"]) {
                    return descriptor.clone();
                }
            }
            assert!(
                Instant::now() < deadline,
                "daemon not ready: {}",
                self.log()
            );
            sleep(Duration::from_millis(20));
        }
    }

    fn open(&self, descriptor: &Value) -> String {
        let response = self
            .request(
                None,
                "session.open",
                json!({
                    "expected_device_id":descriptor["device_id"],
                    "expected_generation":descriptor["generation"],
                    "configuration":{}, "required_methods":[]
                }),
            )
            .unwrap();
        assert!(response.get("error").is_none(), "{response}");
        let session = response["result"]["descriptor"]["session_id"]
            .as_str()
            .unwrap()
            .to_owned();
        let ready = self
            .request(
                Some(&session),
                "environment.readiness",
                json!({
                    "context":{"operation_id":"ready", "timeout_ms":5000}
                }),
            )
            .unwrap();
        assert!(ready.get("error").is_none(), "{ready}");
        session
    }

    fn start_payload(&self, session: &str) -> u32 {
        #[cfg(unix)]
        let script = "echo $$ > payload; printf once >> effects; exec sleep 300";
        #[cfg(windows)]
        let script =
            "$PID | Set-Content payload; Add-Content effects once; Start-Sleep -Seconds 300";
        let response = self
            .request(
                Some(session),
                "process.start",
                json!({
                    "context":{"operation_id":"payload", "timeout_ms":5000},
                    "request":{"command":{"kind":"shell", "profile_id":"default", "script":script}}
                }),
            )
            .unwrap();
        assert!(response.get("error").is_none(), "{response}");
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(pid) = fs::read_to_string(self.root.path().join("payload"))
                .ok()
                .and_then(|value| value.trim().parse().ok())
            {
                return pid;
            }
            assert!(
                Instant::now() < deadline,
                "payload did not start: {response}"
            );
            sleep(Duration::from_millis(20));
        }
    }

    fn wait_for_log(&self, text: &str) {
        let deadline = Instant::now() + Duration::from_secs(15);
        while !self.log().contains(text) {
            assert!(Instant::now() < deadline, "missing {text}: {}", self.log());
            sleep(Duration::from_millis(10));
        }
    }
}

struct Running(Child);

impl Running {
    fn wait(&mut self, limit: Duration) -> ExitStatus {
        let deadline = Instant::now() + limit;
        loop {
            if let Some(status) = self.0.try_wait().unwrap() {
                return status;
            }
            assert!(Instant::now() < deadline, "process did not exit");
            sleep(Duration::from_millis(10));
        }
    }
}

impl Drop for Running {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[cfg(unix)]
fn kill(pid: u32) {
    assert_eq!(unsafe { libc::kill(pid as i32, libc::SIGKILL) }, 0);
}

#[cfg(windows)]
fn kill(pid: u32) {
    use windows_sys::Win32::{
        Foundation::CloseHandle,
        System::Threading::{OpenProcess, PROCESS_TERMINATE, TerminateProcess},
    };
    unsafe {
        let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
        assert!(!handle.is_null());
        assert_ne!(TerminateProcess(handle, 1), 0);
        CloseHandle(handle);
    }
}

#[cfg(unix)]
fn stop(pid: u32) {
    assert_eq!(unsafe { libc::kill(pid as i32, libc::SIGTERM) }, 0);
}

#[cfg(windows)]
fn stop(pid: u32) {
    use windows_sys::Win32::System::Console::{CTRL_BREAK_EVENT, GenerateConsoleCtrlEvent};
    assert_ne!(
        unsafe { GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid) },
        0
    );
}

#[cfg(windows)]
fn ensure_console() {
    use windows_sys::Win32::System::Console::{AllocConsole, GetConsoleProcessList};
    static INIT: std::sync::Once = std::sync::Once::new();
    INIT.call_once(|| unsafe {
        let mut pid = 0;
        if GetConsoleProcessList(&mut pid, 1) == 0 {
            assert_ne!(AllocConsole(), 0);
        }
    });
}

fn assert_exited(pid: u32) {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        #[cfg(unix)]
        let alive = unsafe { libc::kill(pid as i32, 0) } == 0;
        #[cfg(windows)]
        let alive = unsafe {
            use windows_sys::Win32::{
                Foundation::{CloseHandle, WAIT_TIMEOUT},
                System::Threading::{OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject},
            };
            let handle = OpenProcess(PROCESS_SYNCHRONIZE, 0, pid);
            let alive = !handle.is_null() && WaitForSingleObject(handle, 0) == WAIT_TIMEOUT;
            if !handle.is_null() {
                CloseHandle(handle);
            }
            alive
        };
        if !alive {
            return;
        }
        assert!(Instant::now() < deadline, "process {pid} survived");
        sleep(Duration::from_millis(20));
    }
}

#[test]
fn foreground_daemon_ignores_stdin_eof_and_does_not_restart() {
    let fixture = Fixture::new();
    let mut daemon = fixture.start("daemon");
    fixture.descriptor(None);
    kill(daemon.0.id());
    assert!(!daemon.wait(Duration::from_secs(5)).success());
    assert!(fixture.daemon_pids().is_empty());
    assert!(std::net::TcpStream::connect(fixture.address).is_err());
}

#[test]
fn crashed_daemon_replacement_changes_generation_and_preserves_identity_and_credentials() {
    let fixture = Fixture::new();
    let mut supervisor = fixture.start("supervise");
    let first = fixture.descriptor(None);
    let session = fixture.open(&first);
    let payload = fixture.start_payload(&session);
    let credential = fs::read(fixture.root.path().join("credential")).unwrap();
    let pid = fixture.daemon_pids()[0];
    assert_ne!(supervisor.0.id(), pid);
    kill(pid);
    let second = fixture.descriptor(Some(&first["generation"]));
    assert_eq!(first["device_id"], second["device_id"]);
    assert_eq!(
        credential,
        fs::read(fixture.root.path().join("credential")).unwrap()
    );
    assert_eq!(fixture.daemon_pids().len(), 2);
    assert_exited(payload);
    let stale = fixture
        .request(Some(&session), "session.describe", json!({}))
        .unwrap();
    assert!(stale.get("error").is_some(), "{stale}");
    let effects = fs::read_to_string(fixture.root.path().join("effects")).unwrap();
    assert_eq!(effects.matches("once").count(), 1);
    stop(supervisor.0.id());
    assert!(
        supervisor.wait(Duration::from_secs(5)).success(),
        "{}",
        fixture.log()
    );
    assert_exited(fixture.daemon_pids()[1]);
}

#[test]
fn startup_failures_exhaust_three_lifetime_replacements_with_backoff() {
    let fixture = Fixture::new();
    fs::remove_file(fixture.root.path().join("credential")).unwrap();
    let started = Instant::now();
    let mut supervisor = fixture.start("supervise");
    assert!(!supervisor.wait(Duration::from_secs(15)).success());
    assert!(started.elapsed() >= Duration::from_secs(7));
    assert_eq!(fixture.daemon_pids().len(), 4, "{}", fixture.log());
    assert!(fixture.log().contains("replacement budget exhausted"));
    assert!(!fixture.log().contains("native-supervision-test-credential"));
}

#[test]
fn operator_stop_during_backoff_does_not_restart() {
    let fixture = Fixture::new();
    fs::remove_file(fixture.root.path().join("credential")).unwrap();
    let mut supervisor = fixture.start("supervise");
    fixture.wait_for_log("replacement 1/3");
    stop(supervisor.0.id());
    assert!(
        supervisor.wait(Duration::from_secs(2)).success(),
        "{}",
        fixture.log()
    );
    assert_eq!(fixture.daemon_pids().len(), 1);
}

#[test]
fn operator_stop_drains_daemon_and_running_command_without_restart() {
    let fixture = Fixture::new();
    let mut supervisor = fixture.start("supervise");
    let descriptor = fixture.descriptor(None);
    let session = fixture.open(&descriptor);
    let payload = fixture.start_payload(&session);
    stop(supervisor.0.id());
    assert!(
        supervisor.wait(Duration::from_secs(5)).success(),
        "{}",
        fixture.log()
    );
    assert_exited(payload);
    assert_eq!(fixture.daemon_pids().len(), 1);
    assert_exited(fixture.daemon_pids()[0]);
    assert!(
        !fs::read_dir(fixture.root.path().join("runtime"))
            .unwrap()
            .any(|entry| {
                entry
                    .unwrap()
                    .file_name()
                    .to_string_lossy()
                    .starts_with("generation-")
            })
    );
}

#[test]
fn abrupt_supervisor_exit_does_not_leave_a_daemon_or_command() {
    let fixture = Fixture::new();
    let mut supervisor = fixture.start("supervise");
    let descriptor = fixture.descriptor(None);
    let session = fixture.open(&descriptor);
    let payload = fixture.start_payload(&session);
    kill(supervisor.0.id());
    assert!(!supervisor.wait(Duration::from_secs(5)).success());
    assert_exited(fixture.daemon_pids()[0]);
    assert_exited(payload);
}

#[cfg(unix)]
#[test]
fn operator_stop_escalates_within_the_desktop_launchers_deadline() {
    let fixture = Fixture::new();
    let mut supervisor = fixture.start("supervise");
    fixture.descriptor(None);
    let pid = fixture.daemon_pids()[0];
    assert_eq!(unsafe { libc::kill(pid as i32, libc::SIGSTOP) }, 0);
    let started = Instant::now();
    stop(supervisor.0.id());
    assert!(
        supervisor.wait(Duration::from_secs(5)).success(),
        "{}",
        fixture.log()
    );
    assert!(started.elapsed() >= Duration::from_secs(3));
    assert_exited(pid);
    assert_eq!(fixture.daemon_pids().len(), 1);
}

#[test]
fn supervise_rejects_stdio_before_launching_a_peer() {
    let fixture = Fixture::new();
    let status = fixture
        .command("supervise")
        .env("A13N_ENVD_TRANSPORT", "stdio")
        .status()
        .unwrap();
    assert!(!status.success());
    assert!(fixture.daemon_pids().is_empty());
    assert!(fixture.log().contains("stdio is caller-owned"));
}

#[cfg(unix)]
#[test]
fn child_exit_zero_is_replaced_until_the_same_budget_is_exhausted() {
    let fixture = Fixture::new();
    let mut supervisor = fixture.start("supervise");
    let mut generation = fixture.descriptor(None)["generation"].clone();
    for index in 0..4 {
        stop(fixture.daemon_pids()[index]);
        if index < 3 {
            generation = fixture.descriptor(Some(&generation))["generation"].clone();
        }
    }
    assert!(!supervisor.wait(Duration::from_secs(5)).success());
    assert_eq!(fixture.daemon_pids().len(), 4);
    assert_eq!(
        fixture
            .log()
            .matches("daemon exited: exit status: 0")
            .count(),
        4
    );
}
