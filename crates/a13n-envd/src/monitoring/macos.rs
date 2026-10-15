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

fn number<T: Copy + Default>(name: &CStr) -> Option<T> {
    let mut value = T::default();
    let mut length = size_of::<T>();
    // SAFETY: read-only sysctl receives writable, correctly sized scalar storage.
    let status = unsafe {
        libc::sysctlbyname(
            name.as_ptr(),
            (&mut value as *mut T).cast(),
            &mut length,
            ptr::null_mut(),
            0,
        )
    };
    (status == 0 && length == size_of::<T>()).then_some(value)
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
        let (current, memory_available_bytes) = unsafe {
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
            mach_port_deallocate(libc::mach_task_self_, host);
            (current, available)
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
        SystemMetrics {
            cpu_busy_percent,
            cpu_time_seconds: None,
            logical_cpu_count,
            memory_total_bytes: number::<u64>(c"hw.memsize"),
            memory_available_bytes,
            pressure: None,
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
