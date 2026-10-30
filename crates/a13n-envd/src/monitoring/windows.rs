use crate::eip::{CpuTimeSeconds, FilesystemMetrics, OperatingSystem, SystemInfo, SystemMetrics};
use std::{
    mem::{size_of, size_of_val},
    os::windows::ffi::OsStrExt,
    path::Path,
    ptr,
};
use windows_sys::{
    Wdk::System::SystemServices::RtlGetVersion,
    Win32::{
        Foundation::{FILETIME, HANDLE},
        Storage::FileSystem::{DISK_SPACE_INFORMATION, GetDiskFreeSpaceExW, GetVolumePathNameW},
        System::{
            LibraryLoader::{GetModuleHandleW, GetProcAddress},
            Registry::{HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ, RegGetValueW},
            SystemInformation::{
                GROUP_AFFINITY, GetNativeSystemInfo, GlobalMemoryStatusEx,
                IMAGE_FILE_MACHINE_AMD64, IMAGE_FILE_MACHINE_ARM64, IMAGE_FILE_MACHINE_I386,
                MEMORYSTATUSEX, OSVERSIONINFOW, PROCESSOR_ARCHITECTURE_AMD64,
                PROCESSOR_ARCHITECTURE_ARM64, PROCESSOR_ARCHITECTURE_INTEL, SYSTEM_INFO,
            },
            Threading::{
                ALL_PROCESSOR_GROUPS, GetActiveProcessorCount, GetActiveProcessorGroupCount,
                GetCurrentProcess, GetCurrentThread, GetSystemTimes, SetThreadGroupAffinity,
            },
        },
    },
};

fn wide(value: &std::ffi::OsStr) -> Option<Vec<u16>> {
    let mut value: Vec<u16> = value.encode_wide().collect();
    if value.contains(&0) {
        return None;
    }
    value.push(0);
    Some(value)
}

fn machine_architecture(machine: u16) -> Option<String> {
    match machine {
        IMAGE_FILE_MACHINE_AMD64 => Some("x86_64".into()),
        IMAGE_FILE_MACHINE_ARM64 => Some("aarch64".into()),
        IMAGE_FILE_MACHINE_I386 => Some("x86".into()),
        _ => None,
    }
}

fn native_architecture() -> Option<String> {
    let module = unsafe { GetModuleHandleW(wide(std::ffi::OsStr::new("kernel32.dll"))?.as_ptr()) };
    if module.is_null() {
        return None;
    }
    // GetNativeSystemInfo reports the emulated architecture on ARM64 Windows.
    // IsWow64Process2 distinguishes the host from this executable's machine.
    if let Some(function) = unsafe { GetProcAddress(module, c"IsWow64Process2".as_ptr().cast()) } {
        type Query = unsafe extern "system" fn(HANDLE, *mut u16, *mut u16) -> i32;
        // SAFETY: the named OS export has the documented Query ABI.
        let query: Query = unsafe { std::mem::transmute(function) };
        let (mut process, mut native) = (0, 0);
        return (unsafe { query(GetCurrentProcess(), &mut process, &mut native) } != 0)
            .then(|| machine_architecture(native))
            .flatten();
    }
    // Older Windows lacks this export and ARM64 emulation support.
    let mut system = SYSTEM_INFO::default();
    unsafe {
        GetNativeSystemInfo(&mut system);
    }
    match unsafe { system.Anonymous.Anonymous.wProcessorArchitecture } {
        PROCESSOR_ARCHITECTURE_AMD64 => Some("x86_64".into()),
        PROCESSOR_ARCHITECTURE_ARM64 => Some("aarch64".into()),
        PROCESSOR_ARCHITECTURE_INTEL => Some("x86".into()),
        _ => None,
    }
}

pub(super) fn system_info() -> SystemInfo {
    let mut version = OSVERSIONINFOW {
        dwOSVersionInfoSize: size_of::<OSVERSIONINFOW>() as u32,
        ..Default::default()
    };
    // SAFETY: each native call receives correctly sized writable storage.
    let version = (unsafe { RtlGetVersion(&mut version) } == 0).then(|| {
        format!(
            "{}.{}.{}",
            version.dwMajorVersion, version.dwMinorVersion, version.dwBuildNumber
        )
    });
    let architecture = native_architecture();
    let key = wide(std::ffi::OsStr::new(
        "HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0",
    ))
    .unwrap();
    let value = wide(std::ffi::OsStr::new("ProcessorNameString")).unwrap();
    let mut name = [0_u16; 512];
    let mut bytes = size_of_val(&name) as u32;
    let status = unsafe {
        RegGetValueW(
            HKEY_LOCAL_MACHINE,
            key.as_ptr(),
            value.as_ptr(),
            RRF_RT_REG_SZ,
            ptr::null_mut(),
            name.as_mut_ptr().cast(),
            &mut bytes,
        )
    };
    let cpu_model = (status == 0).then(|| {
        String::from_utf16_lossy(&name[..name.iter().position(|c| *c == 0).unwrap_or(name.len())])
            .trim()
            .chars()
            .take(256)
            .collect()
    });
    SystemInfo {
        os: OperatingSystem::Windows,
        os_version: version.clone(),
        kernel_version: version,
        architecture,
        cpu_model,
    }
}

#[derive(Clone, Copy)]
struct Cpu {
    user: u64,
    system: u64,
    idle: u64,
    count: u32,
}
impl Cpu {
    fn percent(self, previous: Self) -> Option<f64> {
        if self.count != previous.count {
            return None;
        }
        let user = self.user.checked_sub(previous.user)?;
        let system = self.system.checked_sub(previous.system)?;
        let idle = self.idle.checked_sub(previous.idle)?;
        let busy = user.checked_add(system)?;
        let total = busy.checked_add(idle)?;
        (total > 0).then(|| busy as f64 / total as f64 * 100.0)
    }
    fn seconds(self) -> CpuTimeSeconds {
        CpuTimeSeconds {
            user: Some(self.user as f64 / 10_000_000.0),
            system: Some(self.system as f64 / 10_000_000.0),
            idle: Some(self.idle as f64 / 10_000_000.0),
            nice: None,
            iowait: None,
            irq: None,
            softirq: None,
            steal: None,
        }
    }
}

fn times(count: u32) -> Option<Cpu> {
    let mut idle = FILETIME::default();
    let mut kernel = FILETIME::default();
    let mut user = FILETIME::default();
    // SAFETY: GetSystemTimes fills three FILETIME values.
    if unsafe { GetSystemTimes(&mut idle, &mut kernel, &mut user) } == 0 {
        return None;
    }
    let ticks = |v: FILETIME| (u64::from(v.dwHighDateTime) << 32) | u64::from(v.dwLowDateTime);
    Some(Cpu {
        user: ticks(user),
        system: ticks(kernel).checked_sub(ticks(idle))?,
        idle: ticks(idle),
        count,
    })
}

fn cpu() -> Option<Cpu> {
    // SAFETY: processor topology getters have no pointer arguments.
    let groups = unsafe { GetActiveProcessorGroupCount() };
    let count = unsafe { GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) };
    if groups == 0 || count == 0 {
        return None;
    }
    if groups == 1 {
        return times(count);
    }
    // GetSystemTimes observes the calling thread's primary processor group.
    // A short-lived helper contains affinity changes; never pin a Tokio worker.
    std::thread::spawn(move || {
        let mut total = Cpu {
            user: 0,
            system: 0,
            idle: 0,
            count,
        };
        for group in 0..groups {
            let affinity = GROUP_AFFINITY {
                Mask: 1,
                Group: group,
                Reserved: [0; 3],
            };
            if unsafe { SetThreadGroupAffinity(GetCurrentThread(), &affinity, ptr::null_mut()) }
                == 0
            {
                return None;
            }
            let sample = times(count)?;
            total.user = total.user.checked_add(sample.user)?;
            total.system = total.system.checked_add(sample.system)?;
            total.idle = total.idle.checked_add(sample.idle)?;
        }
        (unsafe { GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) } == count
            && unsafe { GetActiveProcessorGroupCount() } == groups)
            .then_some(total)
    })
    .join()
    .ok()
    .flatten()
}

pub(super) struct SystemCollector {
    previous_cpu: Option<Cpu>,
}
impl SystemCollector {
    pub(super) fn new() -> Self {
        Self { previous_cpu: None }
    }
    pub(super) fn collect(&mut self) -> SystemMetrics {
        let current = cpu();
        let cpu_busy_percent = current
            .zip(self.previous_cpu)
            .and_then(|(now, previous)| now.percent(previous));
        self.previous_cpu = current;
        let mut memory = MEMORYSTATUSEX {
            dwLength: size_of::<MEMORYSTATUSEX>() as u32,
            ..Default::default()
        };
        let memory = (unsafe { GlobalMemoryStatusEx(&mut memory) } != 0).then_some(memory);
        let count = unsafe { GetActiveProcessorCount(ALL_PROCESSOR_GROUPS) };
        use windows_sys::Win32::System::ProcessStatus::{
            GetPerformanceInfo, PERFORMANCE_INFORMATION,
        };
        let mut performance = PERFORMANCE_INFORMATION::default();
        let performance = (unsafe {
            GetPerformanceInfo(
                &mut performance,
                size_of::<PERFORMANCE_INFORMATION>() as u32,
            )
        } != 0)
            .then_some(performance);
        SystemMetrics {
            cpu_busy_percent,
            cpu_time_seconds: current.map(Cpu::seconds),
            logical_cpu_count: (count > 0).then_some(count),
            memory_total_bytes: memory.as_ref().map(|m| m.ullTotalPhys),
            memory_available_bytes: memory.as_ref().map(|m| m.ullAvailPhys),
            pressure: None,
            memory_wired_bytes: None,
            memory_compressed_bytes: None,
            swap_used_bytes: None,
            swap_total_bytes: None,
            commit_total_bytes: performance
                .as_ref()
                .and_then(|p| (p.CommitTotal as u64).checked_mul(p.PageSize as u64)),
            commit_limit_bytes: performance
                .as_ref()
                .and_then(|p| (p.CommitLimit as u64).checked_mul(p.PageSize as u64)),
            memory_cache_bytes: performance
                .as_ref()
                .and_then(|p| (p.SystemCache as u64).checked_mul(p.PageSize as u64)),
            process_count: performance.as_ref().map(|p| u64::from(p.ProcessCount)),
            thread_count: performance.as_ref().map(|p| u64::from(p.ThreadCount)),
            handle_count: performance.as_ref().map(|p| u64::from(p.HandleCount)),
        }
    }
}

pub(super) fn filesystem(path: &Path) -> Option<FilesystemMetrics> {
    let mut path = std::fs::canonicalize(path).ok()?;
    if !path.is_dir() {
        path = path.parent()?.to_path_buf();
    }
    let path = wide(path.as_os_str())?;
    let mut root = vec![0_u16; 32768];
    if unsafe { GetVolumePathNameW(path.as_ptr(), root.as_mut_ptr(), root.len() as u32) } == 0 {
        return None;
    }
    let mut available = 0;
    let mut free = 0;
    if unsafe { GetDiskFreeSpaceExW(path.as_ptr(), &mut available, ptr::null_mut(), &mut free) }
        == 0
    {
        return None;
    }
    // Unlike GetDiskFreeSpaceEx's total, this is the actual volume capacity,
    // not the caller's quota. Dynamic lookup keeps older Windows able to run
    // envd even when this observation API (Windows 10 1809+) is unavailable.
    let module = unsafe { GetModuleHandleW(wide(std::ffi::OsStr::new("kernel32.dll"))?.as_ptr()) };
    if module.is_null() {
        return None;
    }
    let function = unsafe { GetProcAddress(module, c"GetDiskSpaceInformationW".as_ptr().cast()) }?;
    type GetSpace = unsafe extern "system" fn(*const u16, *mut DISK_SPACE_INFORMATION) -> i32;
    // SAFETY: the named OS export has the documented GetSpace ABI.
    let get_space: GetSpace = unsafe { std::mem::transmute(function) };
    let mut info = DISK_SPACE_INFORMATION::default();
    if unsafe { get_space(root.as_ptr(), &mut info) } != 0 {
        return None;
    }
    let unit =
        u64::from(info.SectorsPerAllocationUnit).checked_mul(u64::from(info.BytesPerSector))?;
    Some(FilesystemMetrics {
        total_bytes: info.ActualTotalAllocationUnits.checked_mul(unit)?,
        free_bytes: free,
        available_bytes: available,
        total_inodes: None,
        free_inodes: None,
        available_inodes: None,
    })
}

use crate::eip::{
    ConnectionMetrics, DiskIoMetrics, DiskMetrics, FilesystemInventory, FilesystemObservation,
    NetworkInterfaceMetrics, NetworkMetrics,
};
use std::collections::BTreeMap;
use windows_sys::Win32::NetworkManagement::IpHelper::*;
use windows_sys::Win32::Networking::WinSock::{AF_INET, AF_INET6};

pub(super) fn network() -> Option<NetworkMetrics> {
    let mut table = ptr::null_mut();
    // SAFETY: the API allocates its table; FreeMibTable releases it after copying bounded rows.
    if unsafe { GetIfTable2(&mut table) } != 0 {
        return None;
    }
    let count = unsafe { (*table).NumEntries as usize };
    let rows = unsafe { std::slice::from_raw_parts((*table).Table.as_ptr(), count.min(64)) };
    let mut interfaces = BTreeMap::new();
    for row in rows {
        let id = unsafe { row.InterfaceLuid.Value }.to_string();
        interfaces.insert(
            row.InterfaceIndex.to_string(),
            NetworkInterfaceMetrics {
                identity: id,
                received_bytes: Some(row.InOctets),
                transmitted_bytes: Some(row.OutOctets),
                received_packets: row.InUcastPkts.checked_add(row.InNUcastPkts),
                transmitted_packets: row.OutUcastPkts.checked_add(row.OutNUcastPkts),
                receive_errors: Some(row.InErrors),
                transmit_errors: Some(row.OutErrors),
                receive_drops: Some(row.InDiscards),
                transmit_drops: Some(row.OutDiscards),
            },
        );
    }
    unsafe {
        FreeMibTable(table.cast());
    }
    Some(NetworkMetrics {
        interfaces,
        truncated: count > 64,
    })
}

fn tcp_counts() -> Option<BTreeMap<String, u64>> {
    let mut states = BTreeMap::new();
    // Bound native table allocation, then immediately discard addresses and owners.
    macro_rules! collect {
        ($api:ident, $table:ty, $row:ty, $state:ident) => {{
            let mut size = 0;
            unsafe {
                $api(ptr::null_mut(), &mut size, 0);
            }
            if size == 0 || size > 4 * 1024 * 1024 {
                return None;
            }
            let mut bytes = vec![0_u64; (size as usize).div_ceil(8)];
            if unsafe { $api(bytes.as_mut_ptr().cast(), &mut size, 0) } != 0 {
                return None;
            }
            let table = bytes.as_ptr().cast::<$table>();
            let count = unsafe { (*table).dwNumEntries } as usize;
            let offset = std::mem::offset_of!($table, table);
            if offset + count * size_of::<$row>() > size as usize {
                return None;
            }
            let rows = unsafe { std::slice::from_raw_parts((*table).table.as_ptr(), count) };
            for row in rows {
                let name = match row.$state {
                    1 => "closed",
                    2 => "listen",
                    3 => "syn_sent",
                    4 => "syn_received",
                    5 => "established",
                    6 => "fin_wait_1",
                    7 => "fin_wait_2",
                    8 => "close_wait",
                    9 => "closing",
                    10 => "last_ack",
                    11 => "time_wait",
                    12 => "delete_tcb",
                    _ => "unknown",
                };
                *states.entry(name.to_owned()).or_insert(0) += 1;
            }
        }};
    }
    collect!(GetTcpTable2, MIB_TCPTABLE2, MIB_TCPROW2, dwState);
    collect!(GetTcp6Table2, MIB_TCP6TABLE2, MIB_TCP6ROW2, State);
    Some(states)
}

pub(super) fn connections() -> Option<ConnectionMetrics> {
    let tcp = tcp_counts();
    let udp = [AF_INET, AF_INET6]
        .into_iter()
        .try_fold(0_u64, |sum, family| {
            let mut stats = MIB_UDPSTATS::default();
            (unsafe { GetUdpStatisticsEx(&mut stats, u32::from(family)) } == 0)
                .then(|| sum + u64::from(stats.dwNumAddrs))
        });
    if tcp.is_none() && udp.is_none() {
        return None;
    }
    Some(ConnectionMetrics {
        tcp_total: tcp.as_ref().map(|s| s.values().sum()),
        tcp_states: tcp.unwrap_or_default(),
        udp_sockets: udp,
    })
}

pub(super) fn local_filesystems() -> Option<FilesystemInventory> {
    use windows_sys::Win32::Foundation::INVALID_HANDLE_VALUE;
    use windows_sys::Win32::Storage::FileSystem::{
        FindFirstVolumeW, FindNextVolumeW, FindVolumeClose, GetDriveTypeW,
        GetVolumePathNamesForVolumeNameW,
    };
    let mut buffer = [0_u16; 1024];
    let handle = unsafe { FindFirstVolumeW(buffer.as_mut_ptr(), buffer.len() as u32) };
    if handle == INVALID_HANDLE_VALUE {
        return None;
    }
    let mut filesystems = BTreeMap::new();
    let mut truncated = false;
    loop {
        // DRIVE_FIXED and DRIVE_REMOVABLE; exclude remote, optical and RAM drives.
        if matches!(unsafe { GetDriveTypeW(buffer.as_ptr()) }, 2 | 3) {
            if filesystems.len() == 16 {
                truncated = true;
                break;
            }
            let volume = String::from_utf16_lossy(
                &buffer[..buffer.iter().position(|v| *v == 0).unwrap_or(buffer.len())],
            );
            let mut paths = vec![0_u16; 32768];
            let mut required = 0;
            let metrics = if unsafe {
                GetVolumePathNamesForVolumeNameW(
                    buffer.as_ptr(),
                    paths.as_mut_ptr(),
                    paths.len() as u32,
                    &mut required,
                )
            } != 0
            {
                let length = paths.iter().position(|v| *v == 0).unwrap_or(paths.len());
                (length > 0)
                    .then(|| String::from_utf16_lossy(&paths[..length]))
                    .and_then(|path| filesystem(Path::new(&path)))
            } else {
                None
            };
            filesystems.insert(
                volume
                    .trim_start_matches("\\\\?\\")
                    .trim_end_matches('\\')
                    .to_owned(),
                FilesystemObservation { metrics },
            );
        }
        if unsafe { FindNextVolumeW(handle, buffer.as_mut_ptr(), buffer.len() as u32) } == 0 {
            break;
        }
    }
    unsafe {
        FindVolumeClose(handle);
    }
    Some(FilesystemInventory {
        filesystems,
        truncated,
    })
}

struct DiskQuery(windows_sys::Win32::System::Performance::PDH_HQUERY);
impl Drop for DiskQuery {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::System::Performance::PdhCloseQuery(self.0);
        }
    }
}

pub(super) fn disks() -> Option<DiskMetrics> {
    use windows_sys::Win32::System::Performance::*;
    let mut query = ptr::null_mut();
    if unsafe { PdhOpenQueryW(ptr::null(), 0, &mut query) } != 0 {
        return None;
    }
    let query = DiskQuery(query);
    let mut counters = Vec::new();
    for (field, name) in [
        "Disk Read Bytes/sec",
        "Disk Write Bytes/sec",
        "Disk Reads/sec",
        "Disk Writes/sec",
    ]
    .iter()
    .enumerate()
    {
        let path = wide(std::ffi::OsStr::new(&format!(r"\PhysicalDisk(*)\{name}")))?;
        let mut counter = ptr::null_mut();
        if unsafe { PdhAddEnglishCounterW(query.0, path.as_ptr(), 0, &mut counter) } == 0 {
            counters.push((field, counter));
        }
    }
    if counters.is_empty() || unsafe { PdhCollectQueryData(query.0) } != 0 {
        return None;
    }
    let mut devices = BTreeMap::new();
    let mut truncated = false;
    for (field, counter) in counters {
        let mut size = 0;
        unsafe {
            PdhGetCounterInfoW(counter, false, &mut size, ptr::null_mut());
        }
        if size < size_of::<PDH_COUNTER_INFO_W>() as u32 || size > 64 * 1024 {
            continue;
        }
        let mut info = vec![0_u64; (size as usize).div_ceil(8)];
        if unsafe { PdhGetCounterInfoW(counter, false, &mut size, info.as_mut_ptr().cast()) } != 0 {
            continue;
        }
        let kind = unsafe { (*info.as_ptr().cast::<PDH_COUNTER_INFO_W>()).dwType };
        // Winperf PERF_COUNTER_COUNTER (DWORD) and PERF_COUNTER_BULK_COUNT (LARGE).
        // These /sec counters' raw FirstValue is the cumulative event/byte count.
        if !matches!(kind, 0x10410400 | 0x10410500) {
            continue;
        }
        let (mut bytes, mut count) = (0, 0);
        unsafe {
            PdhGetRawCounterArrayW(counter, &mut bytes, &mut count, ptr::null_mut());
        }
        if bytes == 0 || bytes > 4 * 1024 * 1024 {
            continue;
        }
        let mut buffer = vec![0_u64; (bytes as usize).div_ceil(8)];
        if unsafe {
            PdhGetRawCounterArrayW(counter, &mut bytes, &mut count, buffer.as_mut_ptr().cast())
        } != 0
        {
            continue;
        }
        if count as usize * size_of::<PDH_RAW_COUNTER_ITEM_W>() > bytes as usize {
            continue;
        }
        let rows = unsafe {
            std::slice::from_raw_parts(
                buffer.as_ptr().cast::<PDH_RAW_COUNTER_ITEM_W>(),
                count as usize,
            )
        };
        for row in rows {
            if !matches!(row.RawValue.CStatus, 0 | 1) || row.szName.is_null() {
                continue;
            }
            // Native item names live inside the caller-owned buffer. Retain only the physical disk number.
            let start = row.szName as usize;
            let end = buffer.as_ptr() as usize + bytes as usize;
            if start < buffer.as_ptr() as usize || start >= end || !start.is_multiple_of(2) {
                continue;
            }
            let name = unsafe { std::slice::from_raw_parts(row.szName, (end - start) / 2) };
            let Some(len) = name.iter().position(|c| *c == 0) else {
                continue;
            };
            let name = String::from_utf16_lossy(&name[..len]);
            let Some(id) = name
                .split_whitespace()
                .next()
                .and_then(|s| s.parse::<u32>().ok())
            else {
                continue;
            };
            let id = id.to_string();
            if devices.len() == 64 && !devices.contains_key(&id) {
                truncated = true;
                continue;
            }
            let v = devices.entry(id.clone()).or_insert_with(|| DiskIoMetrics {
                identity: id,
                read_bytes: None,
                written_bytes: None,
                read_operations: None,
                write_operations: None,
                read_time_ms: None,
                write_time_ms: None,
                busy_time_ms: None,
                weighted_time_ms: None,
                in_progress: None,
            });
            let value = u64::try_from(row.RawValue.FirstValue).ok();
            match field {
                0 => v.read_bytes = value,
                1 => v.written_bytes = value,
                2 => v.read_operations = value,
                _ => v.write_operations = value,
            }
        }
    }
    Some(DiskMetrics { devices, truncated })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_machine_codes_distinguish_arm64_from_x64() {
        assert_eq!(
            machine_architecture(IMAGE_FILE_MACHINE_ARM64).as_deref(),
            Some("aarch64")
        );
        assert_eq!(
            machine_architecture(IMAGE_FILE_MACHINE_AMD64).as_deref(),
            Some("x86_64")
        );
        assert_eq!(
            machine_architecture(IMAGE_FILE_MACHINE_I386).as_deref(),
            Some("x86")
        );
        assert_eq!(machine_architecture(0), None);
        assert!(native_architecture().is_some());
    }

    #[test]
    fn utilization_excludes_idle_once_and_resets_after_topology_change() {
        let first = Cpu {
            user: 10,
            system: 10,
            idle: 80,
            count: 8,
        };
        let next = Cpu {
            user: 20,
            system: 20,
            idle: 160,
            count: 8,
        };
        assert_eq!(next.percent(first), Some(20.0));
        assert_eq!(first.percent(next), None);
        assert_eq!(Cpu { count: 16, ..next }.percent(first), None);
        assert_eq!(first.percent(first), None);
    }
}
