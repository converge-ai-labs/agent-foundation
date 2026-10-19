//! One metric catalog and unit conversion shared by scrape and OTLP export.
use std::fmt::{self, Display};

use crate::eip::{CpuLimit, DeviceMetricsResult, MemoryLimit, Pressures};

#[derive(Clone, Copy, Debug)]
pub(crate) enum Number {
    Unsigned(u64),
    Float(f64),
}
impl From<u64> for Number {
    fn from(value: u64) -> Self {
        Self::Unsigned(value)
    }
}
impl From<u32> for Number {
    fn from(value: u32) -> Self {
        Self::Unsigned(value.into())
    }
}
impl From<u8> for Number {
    fn from(value: u8) -> Self {
        Self::Unsigned(value.into())
    }
}
impl From<f64> for Number {
    fn from(value: f64) -> Self {
        Self::Float(value)
    }
}
impl Display for Number {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unsigned(value) => value.fmt(out),
            Self::Float(value) => value.fmt(out),
        }
    }
}

pub(crate) fn visit(report: &DeviceMetricsResult, out: &mut impl Sink) {
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
        return;
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
        return;
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
}

fn seconds(ms: u64) -> f64 {
    ms as f64 / 1000.0
}

pub(crate) trait Sink {
    fn family(&mut self, name: &str, help: &str, kind: &str);
    fn sample(&mut self, name: &str, value: impl Into<Number>, labels: &[(&str, &str)]);

    fn gauge(&mut self, name: &str, help: &str, value: impl Into<Number>, labels: &[(&str, &str)]) {
        self.family(name, help, "gauge");
        self.sample(name, value, labels);
    }
    fn counter(
        &mut self,
        name: &str,
        help: &str,
        value: impl Into<Number>,
        labels: &[(&str, &str)],
    ) {
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
