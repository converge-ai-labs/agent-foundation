//! Prometheus text 0.0.4 projection of the shared latest observation.
use std::fmt::{Display, Write};

use crate::eip::{CpuLimit, DeviceMetricsResult, MemoryLimit, Pressures};

pub(crate) fn encode(report: &DeviceMetricsResult) -> String {
    let mut out = Exposition {
        text: String::new(),
        identity: format!(
            "device_id=\"{}\",generation=\"{}\"",
            escape(&report.device_id),
            report.generation
        ),
    };
    out.gauge(
        "sample_interval_seconds",
        "Configured collection interval in seconds.",
        seconds(report.sample_interval_ms),
        &[],
    );
    out.gauge(
        "sample_available",
        "Whether a completed sample is cached.",
        u8::from(report.sample.is_some()),
        &[],
    );
    let fresh = report
        .sample
        .as_ref()
        .is_some_and(|s| s.sample_age_ms <= 2 * report.sample_interval_ms);
    out.gauge(
        "sample_fresh",
        "Whether the cached sample age is at most two collection intervals.",
        u8::from(fresh),
        &[],
    );
    let Some(sample) = &report.sample else {
        return out.text;
    };
    out.gauge(
        "sample_sequence",
        "Generation-local completed collection sequence.",
        sample.sequence,
        &[],
    );
    out.gauge(
        "sample_timestamp_seconds",
        "Unix time at collection start in seconds.",
        sample.sampled_at.timestamp_millis() as f64 / 1000.0,
        &[],
    );
    out.gauge(
        "sample_age_seconds",
        "Monotonic time since collection start in seconds.",
        seconds(sample.sample_age_ms),
        &[],
    );
    out.gauge(
        "collection_duration_seconds",
        "Duration of the cached collection in seconds.",
        seconds(sample.collection_duration_ms),
        &[],
    );
    // Do not continually timestamp a stalled collector's resource values as current.
    if !fresh {
        return out.text;
    }

    let system = &sample.system;
    if let Some(value) = system.cpu_busy_percent {
        out.gauge(
            "system_cpu_busy_ratio",
            "System CPU busy fraction over successive native observations.",
            value / 100.0,
            &[],
        );
    }
    if let Some(value) = system.logical_cpu_count {
        out.gauge(
            "system_logical_cpus",
            "Observed system logical CPUs, not a quota or affinity allowance.",
            value,
            &[],
        );
    }
    for (name, help, value) in [
        (
            "system_memory_total_bytes",
            "System physical memory in bytes.",
            system.memory_total_bytes,
        ),
        (
            "system_memory_available_bytes",
            "System available memory in bytes, with platform-specific reclaimability semantics.",
            system.memory_available_bytes,
        ),
    ] {
        if let Some(value) = value {
            out.gauge(name, help, value, &[]);
        }
    }
    if let Some(cpu) = &system.cpu_time_seconds {
        out.family(
            "system_cpu_time_seconds_total",
            "Cumulative system CPU time by native mode in seconds.",
            "counter",
        );
        for (mode, value) in [
            ("user", cpu.user),
            ("nice", cpu.nice),
            ("system", cpu.system),
            ("idle", cpu.idle),
            ("iowait", cpu.iowait),
            ("irq", cpu.irq),
            ("softirq", cpu.softirq),
            ("steal", cpu.steal),
        ] {
            if let Some(value) = value {
                out.sample("system_cpu_time_seconds_total", value, &[("mode", mode)]);
            }
        }
    }
    if let Some(pressure) = &system.pressure {
        out.pressure("system", pressure);
    }

    if let Some(group) = &sample.cgroup_v2 {
        for (name, help, value) in [
            (
                "cgroup_cpu_usage_seconds_total",
                "Cumulative selected cgroup CPU usage including descendants in seconds.",
                group.cpu_usage_usec,
            ),
            (
                "cgroup_cpu_user_seconds_total",
                "Cumulative selected cgroup user CPU time including descendants in seconds.",
                group.cpu_user_usec,
            ),
            (
                "cgroup_cpu_system_seconds_total",
                "Cumulative selected cgroup system CPU time including descendants in seconds.",
                group.cpu_system_usec,
            ),
            (
                "cgroup_cpu_throttled_seconds_total",
                "Cumulative selected cgroup bandwidth throttling time in seconds.",
                group.cpu_throttled_usec,
            ),
        ] {
            if let Some(value) = value {
                out.counter(name, help, value as f64 / 1_000_000.0, &[]);
            }
        }
        for (name, help, value) in [
            (
                "cgroup_cpu_periods_total",
                "Selected cgroup bandwidth periods.",
                group.cpu_periods_total,
            ),
            (
                "cgroup_cpu_throttled_periods_total",
                "Selected cgroup throttled bandwidth periods.",
                group.cpu_throttled_periods_total,
            ),
        ] {
            if let Some(value) = value {
                out.counter(name, help, value, &[]);
            }
        }
        if let Some(limit) = &group.cpu_max {
            out.gauge(
                "cgroup_cpu_limit_unlimited",
                "Whether the local cgroup CPU limit is unlimited; ancestor limits may still apply.",
                u8::from(matches!(limit, CpuLimit::Unlimited(_))),
                &[],
            );
            if let CpuLimit::Limited(limit) = limit {
                out.gauge(
                    "cgroup_cpu_quota_seconds",
                    "Local cgroup CPU bandwidth quota in seconds.",
                    limit.quota_usec as f64 / 1_000_000.0,
                    &[],
                );
                out.gauge(
                    "cgroup_cpu_period_seconds",
                    "Local cgroup CPU bandwidth period in seconds.",
                    limit.period_usec as f64 / 1_000_000.0,
                    &[],
                );
            }
        }
        if let Some(value) = group.memory_current_bytes {
            out.gauge(
                "cgroup_memory_current_bytes",
                "Selected cgroup memory usage including descendants in bytes.",
                value,
                &[],
            );
        }
        if let Some(limit) = &group.memory_max {
            out.gauge("cgroup_memory_limit_unlimited", "Whether the local cgroup memory limit is unlimited; ancestor limits may still apply.", u8::from(matches!(limit, MemoryLimit::Unlimited(_))), &[]);
            if let MemoryLimit::Limited(limit) = limit {
                out.gauge(
                    "cgroup_memory_limit_bytes",
                    "Local cgroup memory limit in bytes.",
                    limit.bytes,
                    &[],
                );
            }
        }
        let events = &group.memory_events;
        out.family(
            "cgroup_memory_events_total",
            "Selected cgroup memory events including descendants.",
            "counter",
        );
        for (event, value) in [
            ("low", events.low),
            ("high", events.high),
            ("max", events.max),
            ("oom", events.oom),
            ("oom_kill", events.oom_kill),
            ("oom_group_kill", events.oom_group_kill),
        ] {
            if let Some(value) = value {
                out.sample("cgroup_memory_events_total", value, &[("event", event)]);
            }
        }
        out.pressure("cgroup", &group.pressure);
    }

    // Keep each metric family contiguous even when multiple targets are configured.
    out.family(
        "filesystem_available",
        "Whether the configured filesystem target was readable in this sample.",
        "gauge",
    );
    for (name, observation) in &sample.filesystems {
        out.sample(
            "filesystem_available",
            u8::from(observation.metrics.is_some()),
            &[("filesystem", name)],
        );
    }
    for (name, help) in [
        (
            "filesystem_total_bytes",
            "Containing filesystem total bytes, not workspace quota.",
        ),
        (
            "filesystem_free_bytes",
            "Containing filesystem free bytes including reserved space.",
        ),
        (
            "filesystem_available_bytes",
            "Containing filesystem bytes available to the daemon account.",
        ),
        (
            "filesystem_total_inodes",
            "Containing filesystem total inodes.",
        ),
        (
            "filesystem_free_inodes",
            "Containing filesystem free inodes.",
        ),
        (
            "filesystem_available_inodes",
            "Containing filesystem inodes available to the daemon account.",
        ),
    ] {
        out.family(name, help, "gauge");
        for (target, observation) in &sample.filesystems {
            let Some(fs) = &observation.metrics else {
                continue;
            };
            let value = match name {
                "filesystem_total_bytes" => Some(fs.total_bytes),
                "filesystem_free_bytes" => Some(fs.free_bytes),
                "filesystem_available_bytes" => Some(fs.available_bytes),
                "filesystem_total_inodes" => fs.total_inodes,
                "filesystem_free_inodes" => fs.free_inodes,
                "filesystem_available_inodes" => fs.available_inodes,
                _ => unreachable!(),
            };
            if let Some(value) = value {
                out.sample(name, value, &[("filesystem", target)]);
            }
        }
    }
    out.text
}

fn seconds(ms: u64) -> f64 {
    ms as f64 / 1000.0
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

impl Exposition {
    fn family(&mut self, name: &str, help: &str, kind: &str) {
        writeln!(
            self.text,
            "# HELP envd_{name} {help}\n# TYPE envd_{name} {kind}"
        )
        .unwrap();
    }

    fn sample(&mut self, name: &str, value: impl Display, labels: &[(&str, &str)]) {
        write!(self.text, "envd_{name}{{{}", self.identity).unwrap();
        for (key, value) in labels {
            write!(self.text, ",{key}=\"{}\"", escape(value)).unwrap();
        }
        writeln!(self.text, "}} {value}").unwrap();
    }

    fn gauge(&mut self, name: &str, help: &str, value: impl Display, labels: &[(&str, &str)]) {
        self.family(name, help, "gauge");
        self.sample(name, value, labels);
    }

    fn counter(&mut self, name: &str, help: &str, value: impl Display, labels: &[(&str, &str)]) {
        self.family(name, help, "counter");
        self.sample(name, value, labels);
    }

    fn pressure(&mut self, scope: &str, pressures: &Pressures) {
        let ratio = format!("{scope}_pressure_ratio");
        let total = format!("{scope}_pressure_stall_seconds_total");
        self.family(
            &ratio,
            "Kernel pressure stall fraction over the labeled window in seconds.",
            "gauge",
        );
        for (resource, pressure) in [
            ("cpu", &pressures.cpu),
            ("memory", &pressures.memory),
            ("io", &pressures.io),
        ] {
            let Some(pressure) = pressure else {
                continue;
            };
            for (level, value) in [("some", &pressure.some), ("full", &pressure.full)] {
                let Some(value) = value else {
                    continue;
                };
                for (window, percent) in [
                    ("10", value.avg10_percent),
                    ("60", value.avg60_percent),
                    ("300", value.avg300_percent),
                ] {
                    self.sample(
                        &ratio,
                        percent / 100.0,
                        &[("resource", resource), ("level", level), ("window", window)],
                    );
                }
            }
        }
        self.family(
            &total,
            "Cumulative pressure stall time in seconds.",
            "counter",
        );
        for (resource, pressure) in [
            ("cpu", &pressures.cpu),
            ("memory", &pressures.memory),
            ("io", &pressures.io),
        ] {
            let Some(pressure) = pressure else {
                continue;
            };
            for (level, value) in [("some", &pressure.some), ("full", &pressure.full)] {
                if let Some(value) = value {
                    self.sample(
                        &total,
                        value.total_usec as f64 / 1_000_000.0,
                        &[("resource", resource), ("level", level)],
                    );
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
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
