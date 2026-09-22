use super::Identity;
use crate::config::CommandConfig;
use std::{
    ffi::{CStr, CString},
    io,
};

pub(crate) fn needs_worker(identity: Identity) -> bool {
    unsafe { libc::geteuid() != identity.uid || libc::getegid() != identity.gid }
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
    let (name, home, shell) = account(identity.uid)?;
    if let Some(command) = command.as_mut() {
        for (key, value) in [
            ("HOME", &home),
            ("USER", &name),
            ("LOGNAME", &name),
            ("SHELL", &shell),
        ] {
            command.base_environment.insert(key.into(), value.clone());
        }
    }
    switch(identity, &name, command)
}

fn account(uid: u32) -> io::Result<(String, String, String)> {
    let mut size = 16 * 1024;
    loop {
        let mut buffer = vec![0_u8; size];
        let mut entry: libc::passwd = unsafe { std::mem::zeroed() };
        let mut result = std::ptr::null_mut();
        let code = unsafe {
            libc::getpwuid_r(
                uid,
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
        return Ok((
            owned(entry.pw_name)?,
            owned(entry.pw_dir)?,
            owned(entry.pw_shell)?,
        ));
    }
}

fn switch(identity: Identity, name: &str, command: &mut Option<CommandConfig>) -> io::Result<()> {
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
    if let Some(command) = command {
        command.drop_supervisor_capabilities = true;
    }
    Ok(())
}

/// Resolve supplementary groups only in the trusted launcher view. The restricted
/// bootstrap applies these numeric IDs before native NSS or payload code runs.
pub(crate) fn restricted_groups(identity: Identity) -> io::Result<Option<Vec<u32>>> {
    if !needs_worker(identity) {
        return Ok(None);
    }
    let (name, _, _) = account(identity.uid)?;
    let name = CString::new(name).map_err(io::Error::other)?;
    let mut groups = vec![0; 16];
    loop {
        let mut count = groups.len() as libc::c_int;
        if unsafe {
            libc::getgrouplist(name.as_ptr(), identity.gid, groups.as_mut_ptr(), &mut count)
        } >= 0
        {
            groups.truncate(count as usize);
            return Ok(Some(groups));
        }
        if count <= 0 || count as usize <= groups.len() {
            return Err(io::Error::other("cannot resolve execution groups"));
        }
        groups.resize(count as usize, 0);
    }
}

pub(crate) fn enter_restricted(identity: Identity, groups: Option<&[u32]>) -> io::Result<()> {
    // Rootful bubblewrap clears active capabilities, not the bounding set.
    // SETPCAP exists only until this bootstrap removes all future authority.
    for capability in 0..64 {
        let present = unsafe { libc::prctl(libc::PR_CAPBSET_READ, capability, 0, 0, 0) };
        if present < 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(libc::EINVAL) {
                break;
            }
            return Err(error);
        }
        if present == 1 && unsafe { libc::prctl(libc::PR_CAPBSET_DROP, capability, 0, 0, 0) } != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    if let Some(groups) = groups
        && unsafe {
            libc::setgroups(groups.len(), groups.as_ptr()) != 0
                || libc::setresgid(identity.gid, identity.gid, identity.gid) != 0
                || libc::setresuid(identity.uid, identity.uid, identity.uid) != 0
        }
    {
        return Err(io::Error::last_os_error());
    }
    if needs_worker(identity) {
        return Err(io::Error::other("Sandbox identity was not applied"));
    }
    clear_payload_capabilities()?;
    disable_privilege_gain()?;
    crate::execution::namespace::seccomp::install()
}

/// Irreversible and inherited across fork/exec, including native setuid binaries
/// and file capabilities. Only trusted startup configuration reaches this call.
pub(crate) fn disable_privilege_gain() -> io::Result<()> {
    if unsafe { libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

/// Drop only the supervisor authority envd retained during an identity switch.
/// Never called for an unchanged launcher identity. Async-signal-safe for pre_exec.
pub(crate) fn clear_payload_capabilities() -> io::Result<()> {
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
