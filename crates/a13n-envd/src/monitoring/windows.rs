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
        SystemMetrics {
            cpu_busy_percent,
            cpu_time_seconds: current.map(Cpu::seconds),
            logical_cpu_count: (count > 0).then_some(count),
            memory_total_bytes: memory.as_ref().map(|m| m.ullTotalPhys),
            memory_available_bytes: memory.as_ref().map(|m| m.ullAvailPhys),
            pressure: None,
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
