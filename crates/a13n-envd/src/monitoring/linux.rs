use crate::eip::{
    CgroupMetrics, CpuLimit, CpuTimeSeconds, FilesystemMetrics, LimitedCpuLimit,
    LimitedMemoryLimit, MemoryEvents, MemoryLimit, OperatingSystem, Pressure, PressureLevel,
    Pressures, SystemInfo, SystemMetrics, UnlimitedCpuLimit, UnlimitedMemoryLimit,
};
use std::{fs::File, io::Read, path::Path};
impl MemoryEvents {
    fn parse(text: &str) -> Self {
        Self {
            low: keyed_u64(text, "low"),
            high: keyed_u64(text, "high"),
            max: keyed_u64(text, "max"),
            oom: keyed_u64(text, "oom"),
            oom_kill: keyed_u64(text, "oom_kill"),
            oom_group_kill: keyed_u64(text, "oom_group_kill"),
        }
    }
}

impl Pressure {
    fn read(path: &Path) -> Option<Self> {
        let text = read(path)?;
        Some(Self {
            some: pressure_level(&text, "some"),
            full: pressure_level(&text, "full"),
        })
    }

    fn system_cpu(mut self) -> Self {
        // CPU full is undefined at system scope; newer kernels emit a
        // compatibility zero that must not be mistaken for an observation.
        self.full = None;
        self
    }
}

fn pressure_level(text: &str, kind: &str) -> Option<PressureLevel> {
    let line = text
        .lines()
        .find(|line| line.split_whitespace().next() == Some(kind))?;
    let value = |key: &str| {
        line.split_whitespace().skip(1).find_map(|field| {
            let (name, value) = field.split_once('=')?;
            (name == key).then_some(value)
        })
    };
    let percent = |key| {
        let value = value(key)?.parse::<f64>().ok()?;
        (value.is_finite() && (0.0..=100.0).contains(&value)).then_some(value)
    };
    Some(PressureLevel {
        avg10_percent: percent("avg10")?,
        avg60_percent: percent("avg60")?,
        avg300_percent: percent("avg300")?,
        total_usec: value("total")?.parse().ok()?,
    })
}

#[cfg(target_os = "linux")]
#[allow(clippy::unnecessary_cast)] // libc field widths differ across Linux architectures.
pub(super) fn filesystem(path: &Path) -> Option<FilesystemMetrics> {
    use std::{ffi::CString, mem::MaybeUninit, os::unix::ffi::OsStrExt};

    let path = CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut stats = MaybeUninit::<libc::statvfs>::uninit();
    // SAFETY: path is NUL-terminated and stats points to writable statvfs storage.
    if unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) } != 0 {
        return None;
    }
    // SAFETY: statvfs initialized the structure on success.
    let stats = unsafe { stats.assume_init() };
    let block_size = stats.f_frsize as u64;
    Some(FilesystemMetrics {
        total_bytes: (stats.f_blocks as u64).checked_mul(block_size)?,
        free_bytes: (stats.f_bfree as u64).checked_mul(block_size)?,
        available_bytes: (stats.f_bavail as u64).checked_mul(block_size)?,
        total_inodes: Some(stats.f_files as u64),
        free_inodes: Some(stats.f_ffree as u64),
        available_inodes: Some(stats.f_favail as u64),
    })
}

pub(super) struct SystemCollector {
    previous_cpu: Option<Cpu>,
    clock_ticks_per_second: Option<f64>,
}
impl SystemCollector {
    pub(super) fn new() -> Self {
        // SAFETY: sysconf takes a constant selector and accesses no Rust memory.
        let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
        Self {
            previous_cpu: None,
            clock_ticks_per_second: (ticks > 0).then_some(ticks as f64),
        }
    }
    pub(super) fn collect(&mut self) -> SystemMetrics {
        let current = read(Path::new("/proc/stat")).and_then(|s| cpu(&s));
        let cpu_busy_percent = current
            .as_ref()
            .zip(self.previous_cpu.as_ref())
            .and_then(|(now, previous)| now.busy_percent(previous));
        let cpu_time_seconds = current
            .as_ref()
            .and_then(|cpu| cpu.seconds(self.clock_ticks_per_second));
        self.previous_cpu = current;
        let memory = read(Path::new("/proc/meminfo"));
        let memory_total_bytes = memory.as_deref().and_then(|s| memory_bytes(s, "MemTotal:"));
        let memory_available_bytes = memory
            .as_deref()
            .and_then(|s| memory_bytes(s, "MemAvailable:"));
        let pressure = Pressures {
            cpu: Pressure::read(Path::new("/proc/pressure/cpu")).map(Pressure::system_cpu),
            memory: Pressure::read(Path::new("/proc/pressure/memory")),
            io: Pressure::read(Path::new("/proc/pressure/io")),
        };
        let count = unsafe { libc::sysconf(libc::_SC_NPROCESSORS_ONLN) };
        SystemMetrics {
            cpu_busy_percent,
            cpu_time_seconds,
            logical_cpu_count: u32::try_from(count).ok().filter(|n| *n > 0),
            memory_total_bytes,
            memory_available_bytes,
            pressure: Some(pressure),
        }
    }
}

pub(super) fn cgroup(path: &Path) -> CgroupMetrics {
    // Read each source once so related counters share one observation.
    let cpu = read(&path.join("cpu.stat")).unwrap_or_default();
    let events = read(&path.join("memory.events")).unwrap_or_default();
    CgroupMetrics {
        cpu_usage_usec: keyed_u64(&cpu, "usage_usec"),
        cpu_user_usec: keyed_u64(&cpu, "user_usec"),
        cpu_system_usec: keyed_u64(&cpu, "system_usec"),
        cpu_periods_total: keyed_u64(&cpu, "nr_periods"),
        cpu_throttled_periods_total: keyed_u64(&cpu, "nr_throttled"),
        cpu_throttled_usec: keyed_u64(&cpu, "throttled_usec"),
        cpu_max: read(&path.join("cpu.max")).and_then(|s| cpu_limit(&s)),
        memory_current_bytes: read(&path.join("memory.current"))
            .and_then(|s| s.trim().parse().ok()),
        memory_max: read(&path.join("memory.max")).and_then(|s| memory_limit(&s)),
        memory_events: MemoryEvents::parse(&events),
        pressure: Pressures {
            cpu: Pressure::read(&path.join("cpu.pressure")),
            memory: Pressure::read(&path.join("memory.pressure")),
            io: Pressure::read(&path.join("io.pressure")),
        },
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
    fn seconds(&self, frequency: Option<f64>) -> Option<CpuTimeSeconds> {
        let frequency = frequency?;
        let seconds = self.ticks.map(|ticks| Some(ticks as f64 / frequency));
        Some(CpuTimeSeconds {
            user: seconds[0],
            nice: seconds[1],
            system: seconds[2],
            idle: seconds[3],
            iowait: seconds[4],
            irq: seconds[5],
            softirq: seconds[6],
            steal: seconds[7],
        })
    }

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
        Some(CpuLimit::Unlimited(UnlimitedCpuLimit {
            kind: "unlimited".to_owned(),
        }))
    } else {
        let quota_usec = quota.parse::<u64>().ok()?;
        (quota_usec > 0).then_some(CpuLimit::Limited(LimitedCpuLimit {
            kind: "limited".to_owned(),
            quota_usec,
            period_usec,
        }))
    }
}

fn memory_limit(text: &str) -> Option<MemoryLimit> {
    if text.trim() == "max" {
        Some(MemoryLimit::Unlimited(UnlimitedMemoryLimit {
            kind: "unlimited".to_owned(),
        }))
    } else {
        text.trim().parse().ok().map(|bytes| {
            MemoryLimit::Limited(LimitedMemoryLimit {
                kind: "limited".to_owned(),
                bytes,
            })
        })
    }
}

pub(super) fn system_info(root: &Path) -> SystemInfo {
    let mut name = std::mem::MaybeUninit::<libc::utsname>::uninit();
    // SAFETY: uname writes one utsname structure on success.
    let native = if unsafe { libc::uname(name.as_mut_ptr()) } == 0 {
        Some(unsafe { name.assume_init() })
    } else {
        None
    };
    let text = |value: &[libc::c_char]| -> String {
        let bytes: Vec<u8> = value
            .iter()
            .take_while(|c| **c != 0)
            .map(|c| *c as u8)
            .collect();
        String::from_utf8_lossy(&bytes).chars().take(256).collect()
    };
    let os_version = read(&root.join("etc/os-release"))
        .or_else(|| read(&root.join("usr/lib/os-release")))
        .and_then(|contents| {
            contents.lines().find_map(|line| {
                line.strip_prefix("PRETTY_NAME=")
                    .map(|v| v.trim_matches(['\"', '\'']).chars().take(256).collect())
            })
        });
    let cpu_model = read(Path::new("/proc/cpuinfo")).and_then(|contents| {
        contents.lines().find_map(|line| {
            let (key, value) = line.split_once(':')?;
            matches!(key.trim(), "model name" | "Hardware")
                .then(|| value.trim().chars().take(256).collect())
        })
    });
    SystemInfo {
        os: OperatingSystem::Linux,
        os_version,
        kernel_version: native.as_ref().map(|v| text(&v.release)),
        architecture: native.as_ref().map(|v| text(&v.machine)),
        cpu_model,
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
    fn cpu_seconds_keep_raw_modes_and_use_the_native_tick_frequency() {
        let first = cpu("cpu 250 5 100 600 30 3 8 4 200 5").unwrap();
        let seconds = first.seconds(Some(250.0)).unwrap();

        assert_eq!(seconds.user.unwrap(), 1.0);
        assert_eq!(seconds.system.unwrap(), 0.4);
        assert_eq!(seconds.steal.unwrap(), 0.016);

        assert!(first.seconds(None).is_none());

        // iowait can decrease without a reboot. Preserve the raw observation,
        // while the derived percentage needs a new valid counter pair.
        let next = cpu("cpu 300 5 100 610 29 3 8 4 200 5").unwrap();
        assert_eq!(next.busy_percent(&first), None);
        assert_eq!(next.seconds(Some(100.0)).unwrap().iowait.unwrap(), 0.29);
        let recovered = cpu("cpu 310 5 100 620 29 3 8 4 200 5").unwrap();
        assert_eq!(recovered.busy_percent(&next), Some(50.0));
    }

    #[test]
    fn pressure_preserves_kernel_windows_and_cumulative_time() {
        let text = "some total=123456 avg300=3.75 avg10=1.25 avg60=2.50 extra=9\nfull avg10=0.00 avg60=0.01 avg300=0.02 total=42\n";
        assert_eq!(
            pressure_level(text, "some"),
            Some(PressureLevel {
                avg10_percent: 1.25,
                avg60_percent: 2.5,
                avg300_percent: 3.75,
                total_usec: 123456,
            })
        );
        let pressure = Pressure {
            some: pressure_level(text, "some"),
            full: pressure_level(text, "full"),
        };
        assert_eq!(pressure.full.as_ref().unwrap().total_usec, 42);
        let system = pressure.system_cpu();
        assert!(system.some.is_some());
        assert!(system.full.is_none());
    }

    #[test]
    fn malformed_or_missing_pressure_records_stay_unavailable() {
        for value in ["NaN", "inf", "-1", "100.01", "broken"] {
            let text = format!(
                "some avg10={value} avg60=0 avg300=0 total=0\nfull avg10=0 avg60=0 avg300=0 total=0\n"
            );
            assert!(pressure_level(&text, "some").is_none());
            assert!(pressure_level(&text, "full").is_some());
        }
        for text in [
            "",
            "some avg10=0 avg60=0 total=0",
            "some avg10=0 avg60=0 avg300=0 total=-1",
        ] {
            assert!(pressure_level(text, "some").is_none());
            assert!(pressure_level(text, "full").is_none());
        }
    }

    #[test]
    fn memory_events_preserve_zero_and_partial_availability() {
        let events =
            MemoryEvents::parse("low 0\nhigh 12\nmax 20\noom 3\noom_kill broken\nfuture_key 17\n");
        assert_eq!(events.low, Some(0));
        assert_eq!(events.high, Some(12));
        assert_eq!(events.max, Some(20));
        assert_eq!(events.oom, Some(3));
        assert_eq!(events.oom_kill, None);
        assert_eq!(events.oom_group_kill, None);
        let missing = serde_json::to_value(MemoryEvents::parse("")).unwrap();
        assert!(
            missing
                .as_object()
                .unwrap()
                .values()
                .all(serde_json::Value::is_null)
        );
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
            Some(CpuLimit::Limited(LimitedCpuLimit {
                kind: "limited".to_owned(),
                quota_usec: 150000,
                period_usec: 100000
            }))
        );
        assert_eq!(
            cpu_limit("max 100000"),
            Some(CpuLimit::Unlimited(UnlimitedCpuLimit {
                kind: "unlimited".to_owned()
            }))
        );
        assert_eq!(cpu_limit("1 0"), None);
        assert_eq!(
            memory_limit("max\n"),
            Some(MemoryLimit::Unlimited(UnlimitedMemoryLimit {
                kind: "unlimited".to_owned()
            }))
        );
        assert_eq!(
            memory_limit("0"),
            Some(MemoryLimit::Limited(LimitedMemoryLimit {
                kind: "limited".to_owned(),
                bytes: 0
            }))
        );
        assert_eq!(memory_limit("broken"), None);
    }
}
