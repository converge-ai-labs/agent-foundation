use std::{
    collections::BTreeMap,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    time::Instant,
};

use serde::Serialize;

#[derive(Serialize)]
pub(super) struct Snapshot {
    sequence: u64,
    sampled_at: String,
    collection_duration_ms: u128,
    system: System,
    cgroup_v2: Option<Cgroup>,
    filesystems: BTreeMap<String, Option<Filesystem>>,
}

#[derive(Serialize)]
struct System {
    cpu_busy_percent: Option<f64>,
    memory_total_bytes: Option<u64>,
    memory_available_bytes: Option<u64>,
}

#[derive(Serialize)]
struct Cgroup {
    cpu_usage_usec: Option<u64>,
    cpu_max: Option<CpuLimit>,
    memory_current_bytes: Option<u64>,
    memory_max: Option<MemoryLimit>,
}

#[derive(Serialize, Debug, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum CpuLimit {
    Unlimited,
    Limited { quota_usec: u64, period_usec: u64 },
}

#[derive(Serialize, Debug, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum MemoryLimit {
    Unlimited,
    Limited { bytes: u64 },
}

#[derive(Serialize)]
struct Filesystem {
    total_bytes: u64,
    available_bytes: u64,
    free_bytes: u64,
}

pub(super) struct Collector {
    filesystems: BTreeMap<String, PathBuf>,
    cgroup: Option<PathBuf>,
    sequence: u64,
    previous_cpu: Option<Cpu>,
}

impl Collector {
    pub(super) fn new(filesystems: BTreeMap<String, PathBuf>, cgroup: Option<PathBuf>) -> Self {
        Self {
            filesystems,
            cgroup,
            sequence: 0,
            previous_cpu: None,
        }
    }

    pub(super) fn collect(&mut self) -> Snapshot {
        let started = Instant::now();
        let sampled_at = chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true);
        let current = read(Path::new("/proc/stat")).and_then(|s| cpu(&s));
        let cpu_busy_percent = current
            .as_ref()
            .zip(self.previous_cpu.as_ref())
            .and_then(|(now, previous)| now.busy_percent(previous));
        self.previous_cpu = current;
        let memory = read(Path::new("/proc/meminfo"));
        let memory_total_bytes = memory.as_deref().and_then(|s| memory_bytes(s, "MemTotal:"));
        let memory_available_bytes = memory
            .as_deref()
            .and_then(|s| memory_bytes(s, "MemAvailable:"));
        let cgroup_v2 = self.cgroup.as_ref().map(|path| Cgroup {
            cpu_usage_usec: read(&path.join("cpu.stat")).and_then(|s| keyed_u64(&s, "usage_usec")),
            cpu_max: read(&path.join("cpu.max")).and_then(|s| cpu_limit(&s)),
            memory_current_bytes: read(&path.join("memory.current"))
                .and_then(|s| s.trim().parse().ok()),
            memory_max: read(&path.join("memory.max")).and_then(|s| memory_limit(&s)),
        });
        let filesystems = self
            .filesystems
            .iter()
            .map(|(name, path)| {
                let stats = fs2::statvfs(path).ok().map(|stats| Filesystem {
                    total_bytes: stats.total_space(),
                    available_bytes: stats.available_space(),
                    free_bytes: stats.free_space(),
                });
                (name.clone(), stats)
            })
            .collect();
        self.sequence += 1;
        Snapshot {
            sequence: self.sequence,
            sampled_at,
            collection_duration_ms: started.elapsed().as_millis(),
            system: System {
                cpu_busy_percent,
                memory_total_bytes,
                memory_available_bytes,
            },
            cgroup_v2,
            filesystems,
        }
    }
}

// Kernel pseudo-files have small records. Limit allocation even if a configured
// cgroup path is accidentally pointed at an ordinary large file tree.
fn read(path: &Path) -> Option<String> {
    let mut text = String::new();
    File::open(path)
        .ok()?
        .take(64 * 1024)
        .read_to_string(&mut text)
        .ok()?;
    Some(text)
}

#[derive(Debug)]
struct Cpu {
    ticks: [u64; 8],
}

impl Cpu {
    fn busy_percent(&self, previous: &Self) -> Option<f64> {
        let mut delta = [0; 8];
        for (i, value) in delta.iter_mut().enumerate() {
            *value = self.ticks[i].checked_sub(previous.ticks[i])?;
        }
        let total = delta
            .iter()
            .try_fold(0_u64, |sum, value| sum.checked_add(*value))?;
        let idle = delta[3].checked_add(delta[4])?;
        (total > 0).then(|| (total - idle) as f64 / total as f64 * 100.0)
    }
}

fn cpu(text: &str) -> Option<Cpu> {
    let mut fields = text.lines().next()?.split_whitespace();
    if fields.next()? != "cpu" {
        return None;
    }
    let mut ticks = [0; 8];
    for tick in &mut ticks {
        *tick = fields.next()?.parse().ok()?;
    }
    // guest/guest_nice already contribute to user/nice; don't count them twice.
    Some(Cpu { ticks })
}

fn keyed_u64(text: &str, key: &str) -> Option<u64> {
    let mut fields = text
        .lines()
        .find(|line| line.split_whitespace().next() == Some(key))?
        .split_whitespace();
    fields.next()?;
    fields.next()?.parse().ok()
}

fn memory_bytes(text: &str, key: &str) -> Option<u64> {
    let line = text
        .lines()
        .find(|line| line.split_whitespace().next() == Some(key))?;
    if line.split_whitespace().nth(2)? != "kB" {
        return None;
    }
    keyed_u64(line, key)?.checked_mul(1024)
}

fn cpu_limit(text: &str) -> Option<CpuLimit> {
    let mut fields = text.split_whitespace();
    let quota = fields.next()?;
    let period_usec = fields.next()?.parse::<u64>().ok()?;
    if period_usec == 0 || fields.next().is_some() {
        return None;
    }
    if quota == "max" {
        Some(CpuLimit::Unlimited)
    } else {
        let quota_usec = quota.parse::<u64>().ok()?;
        (quota_usec > 0).then_some(CpuLimit::Limited {
            quota_usec,
            period_usec,
        })
    }
}

fn memory_limit(text: &str) -> Option<MemoryLimit> {
    if text.trim() == "max" {
        Some(MemoryLimit::Unlimited)
    } else {
        text.trim()
            .parse()
            .ok()
            .map(|bytes| MemoryLimit::Limited { bytes })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpu_uses_deltas_excludes_guest_and_rejects_reset_or_missing_data() {
        let first = cpu("cpu 10 0 10 70 10 0 0 0 9 0").unwrap();
        let next = cpu("cpu 20 0 20 140 20 0 0 0 20 0").unwrap();
        assert_eq!(next.busy_percent(&first), Some(20.0));
        assert_eq!(first.busy_percent(&next), None);
        assert_eq!(first.busy_percent(&first), None);
        assert!(cpu("cpu0 1 2 3 4 5 6 7 8").is_none());
        assert!(cpu("cpu 1 2 nope").is_none());
    }

    #[test]
    fn memory_and_cgroup_units_and_unlimited_are_explicit() {
        assert_eq!(
            memory_bytes("MemTotal: 100 kB\nMemAvailable: 30 kB\n", "MemAvailable:"),
            Some(30 * 1024)
        );
        assert_eq!(memory_bytes("MemTotal: 100 bytes", "MemTotal:"), None);
        assert_eq!(
            memory_bytes("MemTotal: 18446744073709551615 kB", "MemTotal:"),
            None
        );
        assert_eq!(
            keyed_u64("usage_usec 42\nuser_usec 20", "usage_usec"),
            Some(42)
        );
        assert_eq!(
            cpu_limit("150000 100000"),
            Some(CpuLimit::Limited {
                quota_usec: 150000,
                period_usec: 100000
            })
        );
        assert_eq!(cpu_limit("max 100000"), Some(CpuLimit::Unlimited));
        assert_eq!(cpu_limit("1 0"), None);
        assert_eq!(memory_limit("max\n"), Some(MemoryLimit::Unlimited));
        assert_eq!(memory_limit("0"), Some(MemoryLimit::Limited { bytes: 0 }));
        assert_eq!(memory_limit("broken"), None);
    }

    #[test]
    fn unavailable_sources_are_null_and_paths_are_not_exported() {
        let missing = std::env::temp_dir()
            .join("a13n-monitoring-missing")
            .join("absent");
        let mut collector = Collector::new(
            BTreeMap::from([("workspace".into(), missing.clone())]),
            Some(missing),
        );
        let first = serde_json::to_value(collector.collect()).unwrap();
        assert_eq!(first["sequence"], 1);
        assert!(first["system"]["cpu_busy_percent"].is_null());
        assert!(first["cgroup_v2"]["cpu_usage_usec"].is_null());
        assert!(first["filesystems"]["workspace"].is_null());
        assert!(!first.to_string().contains("a13n-monitoring-missing"));
        assert_eq!(
            serde_json::to_value(collector.collect()).unwrap()["sequence"],
            2
        );
    }
}
