//! Prometheus text 0.0.4 projection of the shared latest observation.
use std::fmt::Write;

use super::projection::{self, Number, Sink};
use crate::eip::DeviceMetricsResult;

pub(crate) fn encode(report: &DeviceMetricsResult) -> String {
    let mut out = Exposition {
        text: String::new(),
        identity: format!(
            "device_id=\"{}\",generation=\"{}\"",
            escape(&report.device_id),
            report.generation
        ),
    };
    projection::visit(report, &mut out);
    out.text
}

fn escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('\n', "\\n")
        .replace('"', "\\\"")
}

struct Exposition {
    text: String,
    identity: String,
}

impl Sink for Exposition {
    fn family(&mut self, name: &str, help: &str, kind: &str) {
        writeln!(
            self.text,
            "# HELP envd_{name} {help}\n# TYPE envd_{name} {kind}"
        )
        .unwrap();
    }

    fn sample(&mut self, name: &str, value: impl Into<Number>, labels: &[(&str, &str)]) {
        write!(self.text, "envd_{name}{{{}", self.identity).unwrap();
        for (key, value) in labels {
            write!(self.text, ",{key}=\"{}\"", escape(value)).unwrap();
        }
        writeln!(self.text, "}} {}", value.into()).unwrap();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::eip::{CpuLimit, MemoryLimit};
    use serde_json::json;

    fn report() -> DeviceMetricsResult {
        serde_json::from_value(json!({
            "device_id":"test-device", "generation":18446744073709551615_u64,
            "sample_interval_ms":1000,
            "sample":{
                "sequence":7, "sampled_at":"2026-01-01T00:00:00Z", "sample_age_ms":2000,
                "collection_duration_ms":25,
                "system":{
                    "cpu_busy_percent":25.0, "logical_cpu_count":8,
                    "cpu_time_seconds":{"user":1.25,"idle":0.0},
                    "memory_total_bytes":4096,"memory_available_bytes":0,
                    "pressure":{"cpu":{"some":{"avg10_percent":10.0,"avg60_percent":20.0,"avg300_percent":30.0,"total_usec":1234567}}}
                },
                "cgroup_v2":{
                    "cpu_usage_usec":1234567,"cpu_user_usec":1000000,"cpu_system_usec":234567,
                    "cpu_periods_total":12,"cpu_throttled_periods_total":0,"cpu_throttled_usec":250000,
                    "cpu_max":{"kind":"limited","quota_usec":150000,"period_usec":100000},
                    "memory_current_bytes":0,"memory_max":{"kind":"unlimited"},
                    "memory_events":{"low":0,"oom":2}, "pressure":{}
                },
                "filesystems":{
                    "workspace":{"metrics":{"total_bytes":100,"free_bytes":40,"available_bytes":30,"total_inodes":10,"free_inodes":0}},
                    "missing":{}
                }
            }
        })).unwrap()
    }

    fn value<'a>(text: &'a str, name: &str, labels: &str) -> Option<&'a str> {
        text.lines()
            .find(|line| line.starts_with(&format!("envd_{name}{{")) && line.contains(labels))
            .map(|line| line.rsplit_once(' ').unwrap().1)
    }

    #[test]
    fn converts_units_types_and_preserves_absence_zero_and_limits() {
        let text = encode(&report());
        for (name, labels, expected) in [
            ("sample_fresh", "", "1"),
            ("sample_age_seconds", "", "2"),
            ("collection_duration_seconds", "", "0.025"),
            ("system_cpu_busy_ratio", "", "0.25"),
            ("system_logical_cpus", "", "8"),
            ("system_cpu_time_seconds_total", "mode=\"idle\"", "0"),
            ("system_memory_available_bytes", "", "0"),
            ("system_pressure_ratio", "window=\"60\"", "0.2"),
            (
                "system_pressure_stall_seconds_total",
                "level=\"some\"",
                "1.234567",
            ),
            ("cgroup_cpu_usage_seconds_total", "", "1.234567"),
            ("cgroup_cpu_user_seconds_total", "", "1"),
            ("cgroup_cpu_system_seconds_total", "", "0.234567"),
            ("cgroup_cpu_throttled_seconds_total", "", "0.25"),
            ("cgroup_cpu_periods_total", "", "12"),
            ("cgroup_cpu_throttled_periods_total", "", "0"),
            ("cgroup_cpu_limit_unlimited", "", "0"),
            ("cgroup_cpu_quota_seconds", "", "0.15"),
            ("cgroup_cpu_period_seconds", "", "0.1"),
            ("cgroup_memory_limit_unlimited", "", "1"),
            ("cgroup_memory_events_total", "event=\"low\"", "0"),
            ("filesystem_available", "filesystem=\"missing\"", "0"),
            ("filesystem_free_inodes", "filesystem=\"workspace\"", "0"),
        ] {
            assert_eq!(value(&text, name, labels), Some(expected), "{name}");
        }
        for (name, labels) in [
            ("cgroup_memory_limit_bytes", ""),
            ("system_cpu_time_seconds_total", "mode=\"nice\""),
            ("system_pressure_ratio", "level=\"full\""),
            ("filesystem_total_bytes", "filesystem=\"missing\""),
            ("filesystem_available_inodes", ""),
        ] {
            assert_eq!(value(&text, name, labels), None, "{name}");
        }
        assert!(text.contains("# TYPE envd_cgroup_cpu_usage_seconds_total counter\n"));
        assert!(text.contains("# TYPE envd_system_cpu_busy_ratio gauge\n"));
        assert!(
            text.lines()
                .filter(|line| !line.starts_with('#'))
                .all(|line| line.contains("generation=\"18446744073709551615\""))
        );
        assert!(text.ends_with('\n'));
    }

    #[test]
    fn initial_and_stale_samples_expose_status_without_resource_values() {
        let mut report = report();
        report.sample.as_mut().unwrap().sample_age_ms = 2001;
        let text = encode(&report);
        assert_eq!(value(&text, "sample_available", ""), Some("1"));
        assert_eq!(value(&text, "sample_fresh", ""), Some("0"));
        assert_eq!(value(&text, "sample_sequence", ""), Some("7"));
        assert!(!text.contains("envd_system_"));
        assert!(!text.contains("envd_cgroup_"));
        assert!(!text.contains("envd_filesystem_"));
        report.sample = None;
        let text = encode(&report);
        assert_eq!(value(&text, "sample_available", ""), Some("0"));
        assert_eq!(value(&text, "sample_fresh", ""), Some("0"));
        assert_eq!(value(&text, "sample_sequence", ""), None);
    }

    #[test]
    fn escapes_labels_and_keeps_families_contiguous_without_sample_timestamps() {
        let mut report = report();
        report.device_id = "quote\"slash\\newline\n雪".into();
        let sample = report.sample.as_mut().unwrap();
        sample
            .filesystems
            .insert("second".into(), sample.filesystems["workspace"].clone());
        let text = encode(&report);
        assert!(text.contains("device_id=\"quote\\\"slash\\\\newline\\n雪\""));
        let mut names = std::collections::HashSet::new();
        let mut current = "";
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("# TYPE ") {
                current = rest.split_whitespace().next().unwrap();
                assert!(names.insert(current));
            } else if !line.starts_with('#') {
                let (name, _) = line.split_once('{').unwrap();
                assert_eq!(name, current);
                let (_, numeric) = line.rsplit_once("} ").unwrap();
                assert!(numeric.parse::<f64>().is_ok());
            }
        }
    }

    #[test]
    fn overview_projection_keeps_source_identity_units_and_incomplete_totals() {
        let mut report = report();
        let sample = report.sample.as_mut().unwrap();
        sample.network = Some(serde_json::from_value(json!({"interfaces":{"eth0":{"identity":"if-1", "received_bytes":0, "transmitted_bytes":500, "receive_errors":0}},"truncated":false})).unwrap());
        sample.disks = Some(serde_json::from_value(json!({"devices":{"disk0":{"identity":"disk-1", "read_bytes":1024,"read_time_ms":1500,"in_progress":0}},"truncated":true})).unwrap());
        sample.connections = Some(
            serde_json::from_value(
                json!({"tcp_total":3,"tcp_states":{"listen":1,"established":2},"udp_sockets":0}),
            )
            .unwrap(),
        );
        sample.daemon =
            Some(serde_json::from_value(json!({"sessions":0,"connections":1})).unwrap());
        sample.local_filesystems = Some(serde_json::from_value(json!({"filesystems":{"8:1":{"metrics":{"total_bytes":100,"free_bytes":50,"available_bytes":40}}},"truncated":false})).unwrap());
        let text = encode(&report);
        for (name, labels, expected) in [
            ("network_received_bytes_total", "identity=\"if-1\"", "0"),
            ("disk_read_time_seconds_total", "identity=\"disk-1\"", "1.5"),
            ("disk_in_progress", "", "0"),
            ("disk_truncated", "", "1"),
            ("tcp_state_connections", "state=\"established\"", "2"),
            ("udp_sockets", "", "0"),
            ("daemon_sessions", "", "0"),
            (
                "filesystem_available_bytes",
                "filesystem=\"auto:8:1\"",
                "40",
            ),
        ] {
            assert_eq!(value(&text, name, labels), Some(expected), "{name}");
        }
        assert_eq!(value(&text, "daemon_processes", ""), None);
        assert_eq!(value(&text, "network_receive_drops_total", ""), None);
        assert!(text.contains("# TYPE envd_network_received_bytes_total counter"));
        report.sample.as_mut().unwrap().sample_age_ms = 3000;
        let stale = encode(&report);
        for prefix in ["envd_network_", "envd_disk_", "envd_daemon_", "envd_tcp_"] {
            assert!(!stale.contains(prefix));
        }
    }

    #[test]
    fn counter_resets_and_limit_changes_are_not_hidden() {
        let mut report = report();
        let group = report.sample.as_mut().unwrap().cgroup_v2.as_mut().unwrap();
        group.cpu_usage_usec = Some(0);
        group.cpu_max = Some(CpuLimit::Unlimited(crate::eip::UnlimitedCpuLimit {
            kind: "unlimited".into(),
        }));
        group.memory_max = Some(MemoryLimit::Limited(crate::eip::LimitedMemoryLimit {
            kind: "limited".into(),
            bytes: 0,
        }));
        let text = encode(&report);
        assert_eq!(
            value(&text, "cgroup_cpu_usage_seconds_total", ""),
            Some("0")
        );
        assert_eq!(value(&text, "cgroup_cpu_limit_unlimited", ""), Some("1"));
        assert_eq!(value(&text, "cgroup_cpu_quota_seconds", ""), None);
        assert_eq!(value(&text, "cgroup_memory_limit_unlimited", ""), Some("0"));
        assert_eq!(value(&text, "cgroup_memory_limit_bytes", ""), Some("0"));
    }
}
