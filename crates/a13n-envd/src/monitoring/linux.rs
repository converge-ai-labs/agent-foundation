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
            swap_total_bytes: memory
                .as_deref()
                .and_then(|s| memory_bytes(s, "SwapTotal:")),
            swap_used_bytes: memory.as_deref().and_then(|s| {
                memory_bytes(s, "SwapTotal:")?.checked_sub(memory_bytes(s, "SwapFree:")?)
            }),
            memory_wired_bytes: None,
            memory_compressed_bytes: None,
            commit_total_bytes: None,
            commit_limit_bytes: None,
            memory_cache_bytes: None,
            process_count: None,
            thread_count: None,
            handle_count: None,
        }
    }
}

pub(super) fn cgroup(path: &Path) -> CgroupMetrics {
    // Read each source once so related counters share one observation.
    let cpu = read(&path.join("cpu.stat")).unwrap_or_default();
    let events = read(&path.join("memory.events")).unwrap_or_default();
    let pids_max = read(&path.join("pids.max"));
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
        pids_current: read(&path.join("pids.current")).and_then(|s| s.trim().parse().ok()),
        pids_limit: pids_max.as_deref().and_then(|s| s.trim().parse().ok()),
        pids_unlimited: pids_max.as_deref().and_then(|s| {
            if s.trim() == "max" {
                Some(true)
            } else {
                s.trim().parse::<u64>().ok().map(|_| false)
            }
        }),
        io: read(&path.join("io.stat")).map(|s| cgroup_io(&s)),
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
    read_bounded(path, 64 * 1024)
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

use crate::eip::{
    ConnectionMetrics, DiskIoMetrics, DiskMetrics, FilesystemObservation, NetworkInterfaceMetrics,
    NetworkMetrics,
};
use std::collections::{BTreeMap, BTreeSet};

pub(super) fn network() -> Option<NetworkMetrics> {
    let text = read(Path::new("/proc/net/dev"))?;
    let mut interfaces = BTreeMap::new();
    let mut truncated = false;
    for line in text.lines().skip(2) {
        let Some((name, values)) = line.split_once(':') else {
            continue;
        };
        let name = name.trim();
        let Ok(cname) = std::ffi::CString::new(name) else {
            continue;
        };
        // SAFETY: cname is a NUL-terminated interface name.
        let index = unsafe { libc::if_nametoindex(cname.as_ptr()) };
        if index == 0 {
            continue;
        }
        let fields: Vec<_> = values.split_whitespace().collect();
        if fields.len() < 16 {
            continue;
        }
        if interfaces.len() == 64 {
            truncated = true;
            break;
        }
        let value = |i: usize| fields[i].parse().ok();
        interfaces.insert(
            name.to_owned(),
            NetworkInterfaceMetrics {
                identity: index.to_string(),
                received_bytes: value(0),
                transmitted_bytes: value(8),
                received_packets: value(1),
                transmitted_packets: value(9),
                receive_errors: value(2),
                transmit_errors: value(10),
                receive_drops: value(3),
                transmit_drops: value(11),
            },
        );
    }
    Some(NetworkMetrics {
        interfaces,
        truncated,
    })
}

fn disk_line(line: &str) -> Option<(String, DiskIoMetrics)> {
    let f: Vec<_> = line.split_whitespace().collect();
    if f.len() < 14 {
        return None;
    }
    let value = |i: usize| f[i].parse::<u64>().ok();
    Some((
        f[2].to_owned(),
        DiskIoMetrics {
            identity: format!("{}:{}", value(0)?, value(1)?),
            read_bytes: value(5)?.checked_mul(512),
            written_bytes: value(9)?.checked_mul(512),
            read_operations: value(3),
            write_operations: value(7),
            read_time_ms: value(6),
            write_time_ms: value(10),
            busy_time_ms: value(12),
            weighted_time_ms: value(13),
            in_progress: value(11),
        },
    ))
}

pub(super) fn disks() -> Option<DiskMetrics> {
    let text = read(Path::new("/proc/diskstats"))?;
    let mut devices = BTreeMap::new();
    let mut truncated = false;
    for (name, mut metrics) in text.lines().filter_map(disk_line) {
        let sys = Path::new("/sys/class/block").join(&name);
        // Whole devices only. Stacked devices remain separate, never summed.
        if name.starts_with("loop") || name.starts_with("ram") || sys.join("partition").exists() {
            continue;
        }
        if devices.len() == 64 {
            truncated = true;
            break;
        }
        if let Some(sequence) =
            read(&sys.join("diskseq")).and_then(|s| s.trim().parse::<u64>().ok())
        {
            metrics.identity = format!("{}:{sequence}", metrics.identity);
        }
        devices.insert(name, metrics);
    }
    Some(DiskMetrics { devices, truncated })
}

fn cgroup_io(text: &str) -> DiskMetrics {
    let mut devices = BTreeMap::new();
    let mut truncated = false;
    for line in text.lines() {
        let mut fields = line.split_whitespace();
        let Some(device) = fields.next() else {
            continue;
        };
        let Some((major, minor)) = device.split_once(':') else {
            continue;
        };
        if major.parse::<u32>().is_err() || minor.parse::<u32>().is_err() {
            continue;
        }
        if devices.len() == 64 {
            truncated = true;
            break;
        }
        let fields: BTreeMap<_, _> = fields.filter_map(|s| s.split_once('=')).collect();
        let value = |key| fields.get(key).and_then(|s| s.parse().ok());
        devices.insert(
            device.to_owned(),
            DiskIoMetrics {
                identity: device.to_owned(),
                read_bytes: value("rbytes"),
                written_bytes: value("wbytes"),
                read_operations: value("rios"),
                write_operations: value("wios"),
                read_time_ms: None,
                write_time_ms: None,
                busy_time_ms: None,
                weighted_time_ms: None,
                in_progress: None,
            },
        );
    }
    DiskMetrics { devices, truncated }
}

fn socket_counts(text: &str) -> Option<BTreeMap<String, u64>> {
    let mut counts = BTreeMap::new();
    for line in text.lines().skip(1) {
        let state = u8::from_str_radix(line.split_whitespace().nth(3)?, 16).ok()?;
        let state = match state {
            1 => "established",
            2 => "syn_sent",
            3 => "syn_received",
            4 => "fin_wait_1",
            5 => "fin_wait_2",
            6 => "time_wait",
            7 => "closed",
            8 => "close_wait",
            9 => "last_ack",
            10 => "listen",
            11 => "closing",
            12 => "new_syn_received",
            _ => "unknown",
        };
        *counts.entry(state.to_owned()).or_insert(0) += 1;
    }
    Some(counts)
}

fn protocol_counts(protocol: &str) -> Option<BTreeMap<String, u64>> {
    let mut counts = BTreeMap::new();
    for suffix in ["", "6"] {
        let path = format!("/proc/net/{protocol}{suffix}");
        // IPv6 tables are absent when IPv6 is disabled, unlike unreadable tables.
        if suffix == "6" && !Path::new(&path).exists() {
            continue;
        }
        let text = read_bounded(Path::new(&path), 4 * 1024 * 1024)?;
        for (state, count) in socket_counts(&text)? {
            *counts.entry(state).or_insert(0) += count;
        }
    }
    Some(counts)
}

pub(super) fn connections() -> Option<ConnectionMetrics> {
    let tcp = protocol_counts("tcp");
    let udp = protocol_counts("udp");
    if tcp.is_none() && udp.is_none() {
        return None;
    }
    Some(ConnectionMetrics {
        tcp_total: tcp.as_ref().map(|c| c.values().sum()),
        tcp_states: tcp.unwrap_or_default(),
        udp_sockets: udp.map(|c| c.values().sum()),
    })
}

fn mount_paths(text: &str) -> BTreeMap<String, std::path::PathBuf> {
    let mut mounts = BTreeMap::new();
    let mut seen = BTreeSet::new();
    for line in text.lines() {
        let Some((left, right)) = line.split_once(" - ") else {
            continue;
        };
        let fields: Vec<_> = left.split_whitespace().collect();
        let kind = right.split_whitespace().next().unwrap_or_default();
        if fields.len() < 5
            || !matches!(
                kind,
                "ext2"
                    | "ext3"
                    | "ext4"
                    | "xfs"
                    | "btrfs"
                    | "zfs"
                    | "vfat"
                    | "exfat"
                    | "ntfs"
                    | "ntfs3"
                    | "f2fs"
                    | "overlay"
            )
        {
            continue;
        }
        let device = fields[2];
        if !seen.insert(device.to_owned()) {
            continue;
        }
        let path = fields[4]
            .replace("\\040", " ")
            .replace("\\011", "\t")
            .replace("\\012", "\n")
            .replace("\\134", "\\");
        mounts.insert(device.to_owned(), path.into());
        if mounts.len() == 17 {
            break;
        }
    }
    mounts
}

pub(super) fn local_filesystems() -> Option<crate::eip::FilesystemInventory> {
    let text = read_bounded(Path::new("/proc/self/mountinfo"), 1024 * 1024)?;
    let paths = mount_paths(&text);
    Some(crate::eip::FilesystemInventory {
        truncated: paths.len() > 16,
        filesystems: paths
            .into_iter()
            .take(16)
            .map(|(name, path)| {
                (
                    name,
                    FilesystemObservation {
                        metrics: filesystem(&path),
                    },
                )
            })
            .collect(),
    })
}

fn read_bounded(path: &Path, limit: u64) -> Option<String> {
    let mut text = String::new();
    File::open(path)
        .ok()?
        .take(limit + 1)
        .read_to_string(&mut text)
        .ok()?;
    (text.len() as u64 <= limit).then_some(text)
}

#[cfg(test)]
mod resource_tests {
    use super::*;
    #[test]
    fn disk_sectors_are_512_bytes_and_operations_are_not_merged_counts() {
        let (_, d) = disk_line("8 0 sda 10 2 20 4 30 6 40 8 9 10 11").unwrap();
        assert_eq!(d.read_bytes, Some(10240));
        assert_eq!(d.written_bytes, Some(20480));
        assert_eq!(d.read_operations, Some(10));
        assert_eq!(d.in_progress, Some(9));
        assert!(disk_line("broken").is_none());
    }
    #[test]
    fn cgroup_io_preserves_missing_and_zero() {
        let d = cgroup_io("8:0 wbytes=0 rios=4\n8:1 rbytes=12");
        assert_eq!(d.devices["8:0"].written_bytes, Some(0));
        assert_eq!(d.devices["8:0"].read_bytes, None);
        assert_eq!(d.devices["8:1"].read_bytes, Some(12));
    }
    #[test]
    fn sockets_retain_only_state_counts() {
        let c =
            socket_counts("header\n0: 0000:1234 0000:0 01 rest\n1: hidden hidden 0A rest").unwrap();
        assert_eq!(c["established"], 1);
        assert_eq!(c["listen"], 1);
        assert!(socket_counts("header\nbroken").is_none());
    }
    #[test]
    fn mounts_filter_remote_and_deduplicate_bind_mounts() {
        let m = mount_paths(
            "1 0 8:0 / / rw - ext4 /dev/sda rw\n2 0 8:0 /home /home rw - ext4 /dev/sda rw\n3 0 0:1 / /remote rw - nfs server:/ rw\n4 0 8:1 / /with\\040space rw - xfs /dev/sdb rw",
        );
        assert_eq!(m.len(), 2);
        assert_eq!(m["8:0"], Path::new("/"));
        assert_eq!(m["8:1"], Path::new("/with space"));
    }
}
