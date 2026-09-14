use std::process::{Command, Output};

fn run(argument: &str) -> Output {
    Command::new(env!("CARGO_BIN_EXE_a13n-service-cli"))
        .arg(argument)
        .output()
        .expect("a13n-service-cli should start")
}

fn normalized_stdout(output: &Output) -> String {
    String::from_utf8(output.stdout.clone())
        .expect("stdout should be UTF-8")
        .replace("\r\n", "\n")
}

#[test]
fn help_describes_the_cli() {
    let output = run("--help");

    assert!(output.status.success());
    let stdout = normalized_stdout(&output);
    assert!(stdout.contains("Command-line client for a13n Service"));
    assert!(stdout.contains("Usage: a13n-service-cli"));
}

#[test]
fn version_matches_the_package_version() {
    let output = run("--version");

    assert!(output.status.success());
    assert_eq!(
        normalized_stdout(&output),
        format!("a13n-service-cli {}\n", env!("CARGO_PKG_VERSION"))
    );
}

#[test]
fn replacement_requires_an_etag_before_network_access() {
    let output = Command::new(env!("CARGO_BIN_EXE_a13n-service-cli"))
        .args(["labels", "run", "run_test", "--set", "{}"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("--if-match"));
    assert!(!String::from_utf8_lossy(&output.stderr).contains("panicked"));
}

#[test]
fn every_label_resource_uses_the_sdk_http_contract() {
    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
        time::Duration,
    };
    for (kind, path) in [
        (
            "agent",
            "/api/v1/workspaces/ws_test/agents/resource_test/labels",
        ),
        ("session", "/api/v1/sessions/resource_test/labels"),
        ("thread", "/api/v1/threads/resource_test/labels"),
        ("run", "/api/v1/runs/resource_test/labels"),
        ("skill", "/api/v1/skills/resource_test/labels"),
        (
            "environment-template",
            "/api/v1/environment-templates/resource_test/labels",
        ),
        ("environment", "/api/v1/environments/resource_test/labels"),
    ] {
        for replace in [false, true] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let base = format!("http://{}", listener.local_addr().unwrap());
            let server = thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(10)))
                    .unwrap();
                let mut bytes = Vec::new();
                let mut buffer = [0; 4096];
                loop {
                    let count = stream.read(&mut buffer).unwrap();
                    assert!(count > 0);
                    bytes.extend_from_slice(&buffer[..count]);
                    if let Some(end) = bytes.windows(4).position(|s| s == b"\r\n\r\n") {
                        let headers = String::from_utf8_lossy(&bytes[..end]);
                        let length: usize = headers
                            .lines()
                            .find_map(|line| {
                                line.to_ascii_lowercase()
                                    .strip_prefix("content-length:")
                                    .map(|length| length.trim().parse().unwrap())
                            })
                            .unwrap_or(0);
                        if bytes.len() >= end + 4 + length {
                            break;
                        }
                    }
                }
                let request = String::from_utf8(bytes).unwrap();
                assert!(request.starts_with(&format!(
                    "{} {} HTTP/1.1",
                    if replace { "PUT" } else { "GET" },
                    path
                )));
                assert!(
                    request
                        .to_ascii_lowercase()
                        .contains("authorization: bearer test-token")
                );
                if replace {
                    assert!(
                        request
                            .to_ascii_lowercase()
                            .contains("if-match: \"original\"")
                    );
                    assert!(request.contains("\"labels\":{\"project\":\"support\"}"));
                }
                let body = r#"{"labels":{"project":"support"}}"#;
                write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nETag: \"result\"\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body).unwrap();
            });
            let mut command = Command::new(env!("CARGO_BIN_EXE_a13n-service-cli"));
            command.env("A13N_TOKEN", "test-token").args([
                "--base-url",
                &base,
                "labels",
                kind,
                "resource_test",
            ]);
            if kind == "agent" {
                command.args(["--workspace", "ws_test"]);
            }
            if replace {
                command.args([
                    "--set",
                    r#"{"project":"support"}"#,
                    "--if-match",
                    "\"original\"",
                ]);
            }
            let output = command.output().unwrap();
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
            assert!(normalized_stdout(&output).contains("support"));
            assert!(String::from_utf8_lossy(&output.stderr).contains("ETag: \"result\""));
            server.join().unwrap();
        }
    }
}
