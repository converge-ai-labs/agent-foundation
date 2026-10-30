use crate::eip::{FilesystemMetrics, OperatingSystem, SystemInfo, SystemMetrics};
use std::{
    ffi::CStr,
    mem::{size_of, zeroed},
    path::Path,
    ptr,
};

unsafe extern "C" {
    fn host_page_size(host: libc::host_t, size: *mut libc::vm_size_t) -> libc::kern_return_t;
    fn mach_port_deallocate(
        task: libc::mach_port_t,
        name: libc::mach_port_t,
    ) -> libc::kern_return_t;
}

fn number<T: Copy>(name: &CStr) -> Option<T> {
    let mut value = std::mem::MaybeUninit::<T>::uninit();
    let mut length = size_of::<T>();
    // SAFETY: read-only sysctl receives writable, correctly sized scalar storage.
    let status = unsafe {
        libc::sysctlbyname(
            name.as_ptr(),
            value.as_mut_ptr().cast(),
            &mut length,
            ptr::null_mut(),
            0,
        )
    };
    (status == 0 && length == size_of::<T>()).then(|| unsafe { value.assume_init() })
}

fn text(name: &CStr) -> Option<String> {
    let mut bytes = [0_u8; 1024];
    let mut length = bytes.len();
    // SAFETY: read-only sysctl receives writable buffer and its byte capacity.
    if unsafe {
        libc::sysctlbyname(
            name.as_ptr(),
            bytes.as_mut_ptr().cast(),
            &mut length,
            ptr::null_mut(),
            0,
        )
    } != 0
    {
        return None;
    }
    Some(
        String::from_utf8_lossy(bytes.get(..length)?.split(|b| *b == 0).next()?)
            .trim()
            .chars()
            .take(256)
            .collect(),
    )
}

pub(super) fn system_info() -> SystemInfo {
    let architecture = if number::<i32>(c"hw.optional.arm64") == Some(1) {
        Some("aarch64".to_owned())
    } else {
        text(c"hw.machine")
    };
    SystemInfo {
        os: OperatingSystem::Macos,
        os_version: text(c"kern.osproductversion"),
        kernel_version: text(c"kern.osrelease"),
        architecture,
        cpu_model: text(c"machdep.cpu.brand_string"),
    }
}

pub(super) struct SystemCollector {
    previous_cpu: Option<([u32; 4], Option<u32>)>,
}
impl SystemCollector {
    pub(super) fn new() -> Self {
        Self { previous_cpu: None }
    }

    #[allow(deprecated)] // libc exposes the Mach host/task port primitives as deprecated bindings.
    pub(super) fn collect(&mut self) -> SystemMetrics {
        let logical_cpu_count = number::<u32>(c"hw.logicalcpu").filter(|n| *n > 0);
        // SAFETY: Mach calls use correctly sized, zero-initialized native structures.
        let (current, memory_available_bytes, memory_wired_bytes, memory_compressed_bytes) = unsafe {
            let host = libc::mach_host_self();
            let mut cpu: libc::host_cpu_load_info = zeroed();
            let mut count = libc::HOST_CPU_LOAD_INFO_COUNT;
            let status = libc::host_statistics(
                host,
                libc::HOST_CPU_LOAD_INFO,
                (&mut cpu as *mut libc::host_cpu_load_info).cast(),
                &mut count,
            );
            let current = (status == libc::KERN_SUCCESS && count == libc::HOST_CPU_LOAD_INFO_COUNT)
                .then_some(cpu.cpu_ticks);
            let mut vm: libc::vm_statistics64 = zeroed();
            let mut count = libc::HOST_VM_INFO64_COUNT;
            let mut page_size = 0;
            let status = libc::host_statistics64(
                host,
                libc::HOST_VM_INFO64,
                (&mut vm as *mut libc::vm_statistics64).cast(),
                &mut count,
            );
            // free_count and inactive_count are in the initial four natural_t fields.
            let available = if status == libc::KERN_SUCCESS
                && count >= 4
                && host_page_size(host, &mut page_size) == libc::KERN_SUCCESS
            {
                (u64::from(vm.free_count) + u64::from(vm.inactive_count))
                    .checked_mul(page_size as u64)
            } else {
                None
            };
            let wired =
                available.and_then(|_| u64::from(vm.wire_count).checked_mul(page_size as u64));
            let compressed = (status == libc::KERN_SUCCESS
                && count as usize * size_of::<libc::integer_t>()
                    >= std::mem::offset_of!(libc::vm_statistics64, compressor_page_count)
                        + size_of::<libc::natural_t>()
                && page_size > 0)
                .then(|| u64::from(vm.compressor_page_count).checked_mul(page_size as u64))
                .flatten();
            mach_port_deallocate(libc::mach_task_self_, host);
            (current, available, wired, compressed)
        };
        let cpu_busy_percent =
            current
                .zip(self.previous_cpu)
                .and_then(|(now, (previous, previous_count))| {
                    (logical_cpu_count == previous_count)
                        .then(|| cpu_percent(now, previous))
                        .flatten()
                });
        self.previous_cpu = current.map(|cpu| (cpu, logical_cpu_count));
        let swap = number::<libc::xsw_usage>(c"vm.swapusage");
        SystemMetrics {
            cpu_busy_percent,
            cpu_time_seconds: None,
            logical_cpu_count,
            memory_total_bytes: number::<u64>(c"hw.memsize"),
            memory_available_bytes,
            pressure: None,
            memory_wired_bytes,
            memory_compressed_bytes,
            swap_used_bytes: swap.as_ref().map(|s| s.xsu_used),
            swap_total_bytes: swap.as_ref().map(|s| s.xsu_total),
            commit_total_bytes: None,
            commit_limit_bytes: None,
            memory_cache_bytes: None,
            process_count: None,
            thread_count: None,
            handle_count: None,
        }
    }
}

fn cpu_percent(current: [u32; 4], previous: [u32; 4]) -> Option<f64> {
    let mut delta = [0_u64; 4];
    for i in 0..4 {
        delta[i] = u64::from(current[i].checked_sub(previous[i])?);
    }
    let total: u64 = delta.iter().sum();
    (total > 0)
        .then(|| (total - delta[libc::CPU_STATE_IDLE as usize]) as f64 / total as f64 * 100.0)
}

#[allow(clippy::unnecessary_cast)]
pub(super) fn filesystem(path: &Path) -> Option<FilesystemMetrics> {
    use std::{ffi::CString, os::unix::ffi::OsStrExt};
    let path = CString::new(path.as_os_str().as_bytes()).ok()?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    // SAFETY: path is NUL-terminated and stats points to writable storage.
    if unsafe { libc::statvfs(path.as_ptr(), stats.as_mut_ptr()) } != 0 {
        return None;
    }
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

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cpu_ticks_handle_idle_and_counter_wrap() {
        assert_eq!(cpu_percent([20, 20, 140, 20], [10, 10, 70, 10]), Some(30.0));
        assert_eq!(cpu_percent([0; 4], [u32::MAX; 4]), None);
        assert_eq!(cpu_percent([0; 4], [0; 4]), None);
    }
}

use crate::eip::{
    ConnectionMetrics, DiskMetrics, FilesystemInventory, FilesystemObservation,
    NetworkInterfaceMetrics, NetworkMetrics,
};
use std::collections::BTreeMap;

pub(super) fn network() -> Option<NetworkMetrics> {
    let mut mib = [libc::CTL_NET, libc::PF_ROUTE, 0, 0, libc::NET_RT_IFLIST2, 0];
    let mut length = 0;
    if unsafe {
        libc::sysctl(
            mib.as_mut_ptr(),
            mib.len() as u32,
            ptr::null_mut(),
            &mut length,
            ptr::null_mut(),
            0,
        )
    } != 0
        || length > 4 * 1024 * 1024
    {
        return None;
    }
    let mut data = vec![0_u8; length];
    if unsafe {
        libc::sysctl(
            mib.as_mut_ptr(),
            mib.len() as u32,
            data.as_mut_ptr().cast(),
            &mut length,
            ptr::null_mut(),
            0,
        )
    } != 0
    {
        return None;
    }
    data.truncate(length);
    let mut offset = 0;
    let mut interfaces = BTreeMap::new();
    let mut truncated = false;
    while offset + 4 <= data.len() {
        let size = u16::from_ne_bytes([data[offset], data[offset + 1]]) as usize;
        if size < 4 || offset + size > data.len() {
            return None;
        }
        if data[offset + 3] == libc::RTM_IFINFO2 as u8 && size >= size_of::<libc::if_msghdr2>() {
            if interfaces.len() == 64 {
                truncated = true;
                break;
            }
            // Routing records are packed and need not be aligned in this byte buffer.
            let row = unsafe {
                ptr::read_unaligned(data.as_ptr().add(offset).cast::<libc::if_msghdr2>())
            };
            let mut name = [0_i8; libc::IFNAMSIZ];
            if !unsafe { libc::if_indextoname(u32::from(row.ifm_index), name.as_mut_ptr()) }
                .is_null()
            {
                let name = unsafe { CStr::from_ptr(name.as_ptr()) }
                    .to_string_lossy()
                    .into_owned();
                let stats = row.ifm_data;
                interfaces.insert(
                    name,
                    NetworkInterfaceMetrics {
                        identity: u32::from(row.ifm_index).to_string(),
                        received_bytes: Some(stats.ifi_ibytes),
                        transmitted_bytes: Some(stats.ifi_obytes),
                        received_packets: Some(stats.ifi_ipackets),
                        transmitted_packets: Some(stats.ifi_opackets),
                        receive_errors: Some(stats.ifi_ierrors),
                        transmit_errors: Some(stats.ifi_oerrors),
                        receive_drops: Some(stats.ifi_iqdrops),
                        transmit_drops: u64::try_from(row.ifm_snd_drops).ok(),
                    },
                );
            }
        }
        offset += size;
    }
    Some(NetworkMetrics {
        interfaces,
        truncated,
    })
}

pub(super) fn local_filesystems() -> Option<FilesystemInventory> {
    let count = unsafe { libc::getfsstat(ptr::null_mut(), 0, libc::MNT_NOWAIT) };
    if count < 0 {
        return None;
    }
    let mut mounts: Vec<libc::statfs> = (0..(count as usize + 8).min(256))
        .map(|_| unsafe { zeroed() })
        .collect();
    let count = unsafe {
        libc::getfsstat(
            mounts.as_mut_ptr(),
            (mounts.len() * size_of::<libc::statfs>()) as i32,
            libc::MNT_NOWAIT,
        )
    };
    if count < 0 {
        return None;
    }
    let mut filesystems = BTreeMap::new();
    let mut truncated = count as usize >= mounts.len();
    for mount in mounts.iter().take(count as usize) {
        let kind = unsafe { CStr::from_ptr(mount.f_fstypename.as_ptr()) }.to_bytes();
        if mount.f_flags & libc::MNT_LOCAL as u32 == 0
            || !matches!(kind, b"apfs" | b"hfs" | b"msdos" | b"exfat" | b"ufs")
        {
            continue;
        }
        let device = unsafe { CStr::from_ptr(mount.f_mntfromname.as_ptr()) }.to_string_lossy();
        let device = device.strip_prefix("/dev/").unwrap_or(&device).to_owned();
        if filesystems.contains_key(&device) {
            continue;
        }
        if filesystems.len() == 16 {
            truncated = true;
            break;
        }
        let path = unsafe { CStr::from_ptr(mount.f_mntonname.as_ptr()) }.to_string_lossy();
        filesystems.insert(
            device,
            FilesystemObservation {
                metrics: filesystem(Path::new(path.as_ref())),
            },
        );
    }
    Some(FilesystemInventory {
        filesystems,
        truncated,
    })
}

pub(super) fn connections() -> Option<ConnectionMetrics> {
    let tcp_total = number::<u32>(c"net.inet.tcp.pcbcount").map(u64::from);
    let udp_sockets = number::<u32>(c"net.inet.udp.pcbcount").map(u64::from);
    (tcp_total.is_some() || udp_sockets.is_some()).then_some(ConnectionMetrics {
        tcp_total,
        udp_sockets,
        // State breakdown requires private XNU PCB layouts; public active PCB counts cover both families.
        tcp_states: BTreeMap::new(),
    })
}

#[link(name = "IOKit", kind = "framework")]
unsafe extern "C" {
    fn IOServiceMatching(name: *const libc::c_char) -> *mut libc::c_void;
    fn IOServiceGetMatchingServices(
        port: u32,
        matching: *mut libc::c_void,
        iterator: *mut u32,
    ) -> i32;
    fn IOIteratorNext(iterator: u32) -> u32;
    fn IORegistryEntryCreateCFProperty(
        entry: u32,
        key: *const libc::c_void,
        allocator: *const libc::c_void,
        options: u32,
    ) -> *const libc::c_void;
    fn IORegistryEntryGetRegistryEntryID(entry: u32, id: *mut u64) -> i32;
    fn IOObjectRelease(object: u32) -> i32;
}
#[link(name = "CoreFoundation", kind = "framework")]
unsafe extern "C" {
    fn CFStringCreateWithCString(
        allocator: *const libc::c_void,
        text: *const libc::c_char,
        encoding: u32,
    ) -> *const libc::c_void;
    fn CFDictionaryGetValue(
        dictionary: *const libc::c_void,
        key: *const libc::c_void,
    ) -> *const libc::c_void;
    fn CFGetTypeID(value: *const libc::c_void) -> usize;
    fn CFDictionaryGetTypeID() -> usize;
    fn CFNumberGetTypeID() -> usize;
    fn CFNumberGetValue(number: *const libc::c_void, kind: isize, value: *mut libc::c_void) -> u8;
    fn CFRelease(value: *const libc::c_void);
}

pub(super) fn disks() -> Option<DiskMetrics> {
    // SAFETY: matching consumes its dictionary; Create properties/keys and all IO objects
    // are released locally. Values are type-checked before reading signed 64-bit numbers.
    unsafe {
        let matching = IOServiceMatching(c"IOBlockStorageDriver".as_ptr());
        if matching.is_null() {
            return None;
        }
        let mut iterator = 0;
        if IOServiceGetMatchingServices(0, matching, &mut iterator) != 0 {
            return None;
        }
        let key = CFStringCreateWithCString(ptr::null(), c"Statistics".as_ptr(), 0x08000100);
        if key.is_null() {
            IOObjectRelease(iterator);
            return None;
        }
        let mut devices = BTreeMap::new();
        let mut truncated = false;
        loop {
            let entry = IOIteratorNext(iterator);
            if entry == 0 {
                break;
            }
            if devices.len() == 64 {
                IOObjectRelease(entry);
                truncated = true;
                break;
            }
            let mut identity = 0;
            let identified = IORegistryEntryGetRegistryEntryID(entry, &mut identity) == 0;
            let stats = IORegistryEntryCreateCFProperty(entry, key, ptr::null(), 0);
            IOObjectRelease(entry);
            if stats.is_null() {
                continue;
            }
            if identified && CFGetTypeID(stats) == CFDictionaryGetTypeID() {
                let value = |name: &CStr| -> Option<u64> {
                    let key = CFStringCreateWithCString(ptr::null(), name.as_ptr(), 0x08000100);
                    if key.is_null() {
                        return None;
                    }
                    let number = CFDictionaryGetValue(stats, key);
                    CFRelease(key);
                    if number.is_null() || CFGetTypeID(number) != CFNumberGetTypeID() {
                        return None;
                    }
                    let mut n = 0_i64;
                    (CFNumberGetValue(number, 4, (&mut n as *mut i64).cast()) != 0)
                        .then(|| u64::try_from(n).ok())
                        .flatten()
                };
                devices.insert(
                    identity.to_string(),
                    crate::eip::DiskIoMetrics {
                        identity: identity.to_string(),
                        read_bytes: value(c"Bytes (Read)"),
                        written_bytes: value(c"Bytes (Write)"),
                        read_operations: value(c"Operations (Read)"),
                        write_operations: value(c"Operations (Write)"),
                        read_time_ms: value(c"Total Time (Read)").map(|v| v / 1_000_000),
                        write_time_ms: value(c"Total Time (Write)").map(|v| v / 1_000_000),
                        busy_time_ms: None,
                        weighted_time_ms: None,
                        in_progress: None,
                    },
                );
            }
            CFRelease(stats);
        }
        CFRelease(key);
        IOObjectRelease(iterator);
        Some(DiskMetrics { devices, truncated })
    }
}
