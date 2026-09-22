use super::Identity;
use crate::config::CommandConfig;
use std::{
    ffi::{CStr, CString},
    fs, io,
    os::{fd::AsRawFd, unix::fs::DirBuilderExt},
    path::PathBuf,
};

pub(crate) fn needs_worker(identity: Identity) -> bool {
    unsafe { libc::geteuid() != identity.uid || libc::getegid() != identity.gid }
}

pub(crate) struct StateDirectory(pub PathBuf);

impl StateDirectory {
    pub fn new(identity: Identity) -> io::Result<Self> {
        let mut random = [0_u8; 16];
        getrandom::fill(&mut random).map_err(|error| io::Error::other(error.to_string()))?;
        let path =
            std::env::temp_dir().join(format!("a13n-session-{:032x}", u128::from_ne_bytes(random)));
        fs::DirBuilder::new().mode(0o700).create(&path)?;
        let directory = Self(path);
        let file = fs::File::open(&directory.0)?;
        if unsafe { libc::fchown(file.as_raw_fd(), identity.uid, identity.gid) } != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(directory)
    }
}

impl Drop for StateDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Run only in the single-threaded worker, before constructing its filesystem
/// engine. Native sudoers remains the sole source of sudo authorization.
pub(crate) fn enter(identity: Identity, command: &mut Option<CommandConfig>) -> io::Result<()> {
    identity.validate().map_err(io::Error::other)?;
    // A root launcher retaining its identity needs neither account discovery nor
    // a privilege transition. Preserve its groups and native administration caps.
    if identity.uid == 0 && !needs_worker(identity) {
        return Ok(());
    }
    let mut size = 16 * 1024;
    let (name, home, shell) = loop {
        let mut buffer = vec![0_u8; size];
        let mut entry: libc::passwd = unsafe { std::mem::zeroed() };
        let mut result = std::ptr::null_mut();
        let code = unsafe {
            libc::getpwuid_r(
                identity.uid,
                &mut entry,
                buffer.as_mut_ptr().cast(),
                size,
                &mut result,
            )
        };
        if code == libc::ERANGE && size < 1024 * 1024 {
            size *= 2;
            continue;
        }
        if code != 0 {
            return Err(io::Error::from_raw_os_error(code));
        }
        if result.is_null() {
            return Err(io::Error::other(
                "execution.uid must name a provisioned system account",
            ));
        }
        let owned = |pointer: *const libc::c_char| -> io::Result<String> {
            if pointer.is_null() {
                return Err(io::Error::other("invalid system account"));
            }
            unsafe { CStr::from_ptr(pointer) }
                .to_str()
                .map(str::to_owned)
                .map_err(io::Error::other)
        };
        break (
            owned(entry.pw_name)?,
            owned(entry.pw_dir)?,
            owned(entry.pw_shell)?,
        );
    };
    if let Some(command) = command {
        for (key, value) in [
            ("HOME", &home),
            ("USER", &name),
            ("LOGNAME", &name),
            ("SHELL", &shell),
        ] {
            command.base_environment.insert(key.into(), value.clone());
        }
    }
    if unsafe { libc::geteuid() } != 0 {
        if needs_worker(identity) {
            return Err(io::Error::other("execution identity is unavailable"));
        }
        return Ok(());
    }
    let name = CString::new(name).map_err(io::Error::other)?;
    unsafe {
        if libc::initgroups(name.as_ptr(), identity.gid) != 0
            || libc::setresgid(identity.gid, identity.gid, identity.gid) != 0
        {
            return Err(io::Error::last_os_error());
        }
        if identity.uid == 0 {
            return Ok(());
        }
        if libc::prctl(libc::PR_SET_KEEPCAPS, 1, 0, 0, 0) != 0
            || libc::setresuid(identity.uid, identity.uid, identity.uid) != 0
        {
            return Err(io::Error::last_os_error());
        }
    }
    // The internal supervisor must signal admitted process groups even after
    // sudo changes a descendant's UID. No payload inherits this capability.
    capabilities(1 << 5)?; // CAP_KILL
    unsafe {
        if libc::prctl(libc::PR_CAP_AMBIENT, libc::PR_CAP_AMBIENT_RAISE, 5, 0, 0) != 0
            || libc::prctl(libc::PR_SET_KEEPCAPS, 0, 0, 0, 0) != 0
        {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

/// Irreversible and inherited across fork/exec, including native setuid binaries
/// and file capabilities. Only trusted startup configuration reaches this call.
pub(crate) fn disable_privilege_gain() -> io::Result<()> {
    if unsafe { libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

/// Async-signal-safe: called in the supervisor's payload pre_exec closure.
pub(crate) fn clear_payload_capabilities() -> io::Result<()> {
    // Root execution deliberately retains native administration capabilities.
    // Managed workers have already removed capabilities that could break egress.
    if unsafe { libc::geteuid() } == 0 {
        return Ok(());
    }
    capabilities(0)?;
    if unsafe {
        libc::prctl(
            libc::PR_CAP_AMBIENT,
            libc::PR_CAP_AMBIENT_CLEAR_ALL,
            0,
            0,
            0,
        )
    } != 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn capabilities(bits: u32) -> io::Result<()> {
    #[repr(C)]
    struct Header {
        version: u32,
        pid: i32,
    }
    #[repr(C)]
    #[derive(Clone, Copy)]
    struct Data {
        effective: u32,
        permitted: u32,
        inheritable: u32,
    }
    let header = Header {
        version: 0x20080522,
        pid: 0,
    };
    let data = [
        Data {
            effective: bits,
            permitted: bits,
            inheritable: bits,
        },
        Data {
            effective: 0,
            permitted: 0,
            inheritable: 0,
        },
    ];
    if unsafe { libc::syscall(libc::SYS_capset, &header, data.as_ptr()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}
