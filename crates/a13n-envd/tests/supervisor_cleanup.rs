#![cfg(unix)]

use serde_json::json;
use std::{
    io::{BufRead, BufReader, Write},
    process::{Command, Stdio},
    time::{Duration, Instant},
};

#[test]
fn broken_event_pipe_still_cleans_the_command_group() {
    for output in [false, true] {
        let root = std::env::temp_dir().join(format!(
            "a13n-supervisor-pipe-{}-{output}",
            std::process::id()
        ));
        std::fs::create_dir(&root).unwrap();
        let mut supervisor = Command::new(env!("CARGO_BIN_EXE_a13n-envd"))
            .arg("--internal-supervisor")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let mut input = supervisor.stdin.take().unwrap();
        let mut events = BufReader::new(supervisor.stdout.take().unwrap());
        let mut line = String::new();
        events.read_line(&mut line).unwrap();
        assert!(line.contains("booted"));
        let script = format!(
            "sleep 60 & echo $! > descendant; while [ ! -e proceed ]; do sleep 0.01; done; {}",
            if output {
                "printf event; sleep 60"
            } else {
                "exit 0"
            }
        );
        let prepare = json!({"type":"prepare","version":1,"plan":{
            "executable":"/bin/sh", "arguments":["-c",script], "cwd":root,
            "environment":{"PATH":"/usr/bin:/bin"}, "initial_stdin":null,
            "keep_stdin_open":false, "wall_time_ms":10000
        }});
        writeln!(input, "{prepare}").unwrap();
        input.flush().unwrap();
        line.clear();
        events.read_line(&mut line).unwrap();
        assert!(line.contains("prepared"));
        writeln!(input, "{}", json!({"type":"start"})).unwrap();
        input.flush().unwrap();
        let deadline = Instant::now() + Duration::from_secs(15);
        while !root.join("descendant").exists() {
            assert!(Instant::now() < deadline, "payload did not start");
            std::thread::sleep(Duration::from_millis(10));
        }
        let pid: i32 = std::fs::read_to_string(root.join("descendant"))
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        // Keep stdin open: only the failed event write triggers cleanup here.
        drop(events);
        std::fs::write(root.join("proceed"), "").unwrap();
        while supervisor.try_wait().unwrap().is_none() {
            assert!(Instant::now() < deadline, "supervisor did not exit");
            std::thread::sleep(Duration::from_millis(10));
        }
        while unsafe { libc::kill(pid, 0) } == 0 {
            assert!(
                Instant::now() < deadline,
                "descendant survived the broken event pipe"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        assert_eq!(
            std::io::Error::last_os_error().raw_os_error(),
            Some(libc::ESRCH)
        );
        std::fs::remove_dir_all(root).unwrap();
    }
}
