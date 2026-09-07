//! Native command-tree ownership, not filesystem or network isolation.
//!
//! The supervisor owns the only Job handle. A suspended payload is assigned
//! before its primary thread is resumed; descendants cannot break away. This
//! remains useful in explicit outer-host mode but never enables required mode.

use std::{
    io,
    mem::size_of,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    ptr::{null, null_mut},
    time::Duration,
};

use tokio::process::{Child, Command};
use windows_sys::Win32::{
    Foundation::{ERROR_NO_MORE_FILES, HANDLE, INVALID_HANDLE_VALUE},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next,
        },
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JobObjectBasicAccountingInformation, JobObjectExtendedLimitInformation,
            QueryInformationJobObject, SetInformationJobObject, TerminateJobObject,
        },
        Threading::{CREATE_SUSPENDED, OpenThread, ResumeThread, THREAD_SUSPEND_RESUME},
    },
};

pub(crate) struct WindowsJob {
    handle: OwnedHandle,
}

impl WindowsJob {
    pub(crate) fn create() -> io::Result<Self> {
        // No name and no inheritable security attributes: payloads never receive
        // the handle which controls their own or another command's lifetime.
        let handle = owned_handle(unsafe { CreateJobObjectW(null(), null()) })?;
        let job = Self { handle };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let result = unsafe {
            SetInformationJobObject(
                job.raw(),
                JobObjectExtendedLimitInformation,
                (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        };
        if result == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(job)
    }

    fn raw(&self) -> HANDLE {
        self.handle.as_raw_handle()
    }

    pub(crate) async fn spawn(command: &mut Command) -> io::Result<(Child, Self)> {
        let job = Self::create()?;
        command.creation_flags(CREATE_SUSPENDED);
        let mut child = command.spawn()?;
        let result = (|| {
            let process = child
                .raw_handle()
                .ok_or_else(|| io::Error::other("payload handle unavailable"))?;
            if unsafe { AssignProcessToJobObject(job.raw(), process) } == 0 {
                return Err(io::Error::last_os_error());
            }
            resume_primary_thread(
                child
                    .id()
                    .ok_or_else(|| io::Error::other("payload ID unavailable"))?,
            )
        })();
        if let Err(error) = result {
            // A failed assignment never releases the payload. A failed resume
            // retains both owners until the strongest available cleanup runs.
            let _ = child.start_kill();
            let _ = job.terminate_and_wait(Duration::from_secs(2)).await;
            let _ = tokio::time::timeout(Duration::from_secs(2), child.wait()).await;
            return Err(error);
        }
        Ok((child, job))
    }

    pub(crate) fn active_processes(&self) -> io::Result<u32> {
        let mut accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        if unsafe {
            QueryInformationJobObject(
                self.raw(),
                JobObjectBasicAccountingInformation,
                (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
                size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                null_mut(),
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(accounting.ActiveProcesses)
    }

    pub(crate) async fn terminate_and_wait(&self, grace: Duration) -> io::Result<()> {
        if unsafe { TerminateJobObject(self.raw(), 1) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let deadline = tokio::time::Instant::now() + grace;
        loop {
            if self.active_processes()? == 0 {
                return Ok(());
            }
            if tokio::time::Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "command job did not become empty",
                ));
            }
            // Query the kernel-owned Job, never a potentially stale PID tree.
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }
}

fn owned_handle(handle: HANDLE) -> io::Result<OwnedHandle> {
    if handle.is_null() || handle == INVALID_HANDLE_VALUE {
        Err(io::Error::last_os_error())
    } else {
        // SAFETY: successful APIs return a unique, owned kernel handle.
        Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
    }
}

fn resume_primary_thread(process_id: u32) -> io::Result<()> {
    // Tokio intentionally hides the initial thread handle. Before first resume
    // the suspended process has exactly one thread; use documented Win32 APIs
    // to obtain it while the held process handle prevents PID reuse.
    let snapshot = owned_handle(unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) })?;
    let mut entry = THREADENTRY32 {
        dwSize: size_of::<THREADENTRY32>() as u32,
        ..Default::default()
    };
    let mut present = unsafe { Thread32First(snapshot.as_raw_handle(), &mut entry) } != 0;
    let mut primary = None;
    while present {
        if entry.th32OwnerProcessID == process_id {
            if primary.is_some() {
                return Err(io::Error::other(
                    "suspended payload has multiple initial threads",
                ));
            }
            primary = Some(owned_handle(unsafe {
                OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID)
            })?);
        }
        entry.dwSize = size_of::<THREADENTRY32>() as u32;
        present = unsafe { Thread32Next(snapshot.as_raw_handle(), &mut entry) } != 0;
    }
    let error = io::Error::last_os_error();
    if error.raw_os_error() != Some(ERROR_NO_MORE_FILES as i32) {
        return Err(error);
    }
    let primary =
        primary.ok_or_else(|| io::Error::other("suspended payload primary thread not found"))?;
    if unsafe { ResumeThread(primary.as_raw_handle()) } != 1 {
        return Err(io::Error::other(
            "payload primary thread did not leave its initial suspension",
        ));
    }
    Ok(())
}
