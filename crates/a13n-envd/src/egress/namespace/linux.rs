use serde::{Deserialize, Serialize};
use std::{
    ffi::CString,
    fs, io,
    net::{TcpListener, UdpSocket},
    os::fd::{AsRawFd, RawFd},
    os::unix::{ffi::OsStrExt, fs::PermissionsExt},
    path::{Path, PathBuf},
    process::Command,
};

pub(crate) const TCP_PORT: u16 = 15001;
pub(crate) const DNS_PORT: u16 = 53;
pub(crate) const RUNTIME: &str = "/run/a13n-envd";

#[derive(Deserialize, Serialize)]
pub(crate) struct Mounts {
    pub workspace: PathBuf,
    pub hide: Vec<PathBuf>,
    pub network: bool,
    pub ca_pem: String,
}

pub(crate) struct Listeners {
    pub tcp: TcpListener,
    pub udp: std::os::fd::OwnedFd,
    pub dns: UdpSocket,
}

/// Called only in the fresh, single-threaded unshare child, never from pre_exec.
/// Helpers run from the frozen management runtime; payloads use the original
/// writable system tree. Install the boundary before native NSS can load code.
pub(crate) fn prepare(plan: &Mounts) -> io::Result<Option<Listeners>> {
    check_kernel()?;
    if std::process::id() != 1 {
        return Err(io::Error::other("Session worker must own namespace PID 1"));
    }
    if !plan.workspace.is_absolute()
        || plan.workspace == Path::new("/")
        || ["/run", "/proc", "/sys", "/dev"]
            .iter()
            .any(|p| plan.workspace.starts_with(p))
        || plan
            .hide
            .iter()
            .any(|p| plan.workspace.starts_with(p) || p.starts_with(&plan.workspace))
    {
        return Err(io::Error::other(
            "workspace overlaps protected runtime paths",
        ));
    }
    protect_process()?;
    let listeners = plan.network.then(network).transpose()?;
    let executable = fs::read("/proc/self/exe")?;
    let mut bundle = fs::read("/etc/ssl/certs/ca-certificates.crt")?;
    bundle.push(b'\n');
    bundle.extend_from_slice(plan.ca_pem.as_bytes());
    enter_original_view()?;
    // Pin workspace before masking /tmp. Do not impose nosuid on its native tree.
    let workspace = fs::File::open(&plan.workspace)?;
    let resolved_workspace = fs::canonicalize(&plan.workspace)?;
    if ["/run", "/proc", "/sys", "/dev"]
        .iter()
        .any(|p| resolved_workspace.starts_with(p))
        || plan
            .hide
            .iter()
            .any(|p| resolved_workspace.starts_with(p) || p.starts_with(&resolved_workspace))
    {
        return Err(io::Error::other(
            "workspace resolves into a protected runtime path",
        ));
    }
    let resolv = plan
        .network
        .then(|| fs::canonicalize("/etc/resolv.conf"))
        .transpose()?;
    for (private, mode) in [("/tmp", "mode=1777"), ("/run", "mode=755")] {
        mount(
            Some("tmpfs"),
            Path::new(private),
            Some("tmpfs"),
            libc::MS_NOSUID | libc::MS_NODEV,
            Some(mode),
        )?;
    }
    if resolved_workspace.starts_with("/tmp") {
        fs::create_dir_all(&resolved_workspace)?;
        bind_fd(workspace.as_raw_fd(), &resolved_workspace)?;
    }
    fs::create_dir_all(RUNTIME)?;
    fs::write(format!("{RUNTIME}/ca.pem"), bundle)?;
    let executable_path = format!("{RUNTIME}/envd");
    fs::write(&executable_path, executable)?;
    fs::set_permissions(&executable_path, fs::Permissions::from_mode(0o555))?;
    if let Some(resolv) = resolv {
        fs::write(
            format!("{RUNTIME}/resolv.conf"),
            "nameserver 127.0.0.1\noptions timeout:1 attempts:2\n",
        )?;
        // A systemd resolver target may disappear with the private /run mount.
        if !resolv.exists() {
            fs::create_dir_all(
                resolv
                    .parent()
                    .ok_or_else(|| io::Error::other("invalid resolver path"))?,
            )?;
            fs::write(&resolv, [])?;
        }
        mount(
            Some(&format!("{RUNTIME}/resolv.conf")),
            &resolv,
            None,
            libc::MS_BIND,
            None,
        )?;
    }
    // Do not bind over the system CA bundle: update-ca-certificates must retain
    // native rename/write semantics. Controlled commands receive explicit CA env.
    readonly_bind(Path::new(RUNTIME))?;
    // seccomp requires CAP_SYS_ADMIN when NNP is not set. Installing it first
    // preserves native setuid sudo while making these restrictions inheritable.
    super::seccomp::install()?;
    restrict_capabilities()?;
    Ok(listeners)
}

/// Device discovery is a one-shot native-identity observation, not a Session.
/// It retains the original /tmp for browsing but cannot see management processes
/// or code. Native NSS runs only after the same boundary used by Sessions.
pub(crate) fn prepare_discovery() -> io::Result<()> {
    check_kernel()?;
    enter_original_view()?;
    mount(
        Some("tmpfs"),
        Path::new("/run"),
        Some("tmpfs"),
        libc::MS_NOSUID | libc::MS_NODEV,
        Some("mode=755"),
    )?;
    super::seccomp::install()?;
    restrict_capabilities()
}

fn enter_original_view() -> io::Result<()> {
    if std::process::id() != 1 {
        return Err(io::Error::other("worker must own namespace PID 1"));
    }
    let devices: Vec<_> = ["null", "zero", "random", "urandom", "tty"]
        .into_iter()
        .map(|name| {
            crate::execution::management::clone_tree(Path::new(&format!("/dev/{name}")))
                .map(|tree| (name, tree))
        })
        .collect::<io::Result<_>>()?;
    crate::execution::management::enter_original()?;
    // Replace the outer procfs before native NSS or payload code can run.
    mount(
        Some("proc"),
        Path::new("/proc"),
        Some("proc"),
        libc::MS_NOSUID | libc::MS_NODEV | libc::MS_NOEXEC,
        None,
    )?;
    // A fresh procfs must not expose writable global kernel controls hidden by
    // the outer sandbox's proc submounts. These are not native package state.
    for path in ["/proc/sys", "/proc/irq", "/proc/bus"] {
        if Path::new(path).exists() {
            readonly_bind(Path::new(path))?;
        }
    }
    for path in [
        "/proc/sysrq-trigger",
        "/proc/kcore",
        "/proc/keys",
        "/proc/timer_list",
        "/proc/latency_stats",
        "/proc/sched_debug",
    ] {
        if Path::new(path).exists() {
            mount(
                Some("/dev/null"),
                Path::new(path),
                None,
                libc::MS_BIND,
                None,
            )?;
        }
    }
    mount(
        Some("tmpfs"),
        Path::new("/dev"),
        Some("tmpfs"),
        libc::MS_NOSUID,
        Some("mode=755"),
    )?;
    for (name, tree) in devices {
        let target = PathBuf::from(format!("/dev/{name}"));
        fs::write(&target, [])?;
        crate::execution::management::attach_tree(tree, &target)?;
    }
    fs::create_dir("/dev/pts")?;
    mount(
        Some("devpts"),
        Path::new("/dev/pts"),
        Some("devpts"),
        libc::MS_NOSUID | libc::MS_NOEXEC,
        Some("newinstance,ptmxmode=0666,mode=0620"),
    )?;
    std::os::unix::fs::symlink("pts/ptmx", "/dev/ptmx")?;
    std::os::unix::fs::symlink("/proc/self/fd", "/dev/fd")?;
    for (name, fd) in [("stdin", 0), ("stdout", 1), ("stderr", 2)] {
        std::os::unix::fs::symlink(format!("/proc/self/fd/{fd}"), format!("/dev/{name}"))?;
    }
    fs::create_dir("/dev/shm")?;
    mount(
        Some("tmpfs"),
        Path::new("/dev/shm"),
        Some("tmpfs"),
        libc::MS_NOSUID | libc::MS_NODEV,
        Some("mode=1777,size=64m"),
    )?;
    mount(
        Some("tmpfs"),
        Path::new("/sys"),
        Some("tmpfs"),
        libc::MS_RDONLY | libc::MS_NOSUID | libc::MS_NODEV,
        Some("mode=555"),
    )
}

fn network() -> io::Result<Listeners> {
    run("/usr/sbin/ip", &["link", "set", "lo", "up"])?;
    run(
        "/usr/sbin/ip",
        &[
            "route",
            "add",
            "local",
            "0.0.0.0/0",
            "dev",
            "lo",
            "src",
            "127.0.0.1",
        ],
    )?;
    let tcp = TcpListener::bind(("127.0.0.1", TCP_PORT))?;
    let udp = super::udp::socket()?;
    let dns = UdpSocket::bind(("127.0.0.1", DNS_PORT))?;
    let rules = format!(
        "add table ip a13n\nadd chain ip a13n output {{ type nat hook output priority -100; policy accept; }}\n\
         add rule ip a13n output ip daddr 127.0.0.1 udp dport 53 return\n\
         add rule ip a13n output ip daddr 127.0.0.0/10 return\n\
         add rule ip a13n output meta l4proto tcp dnat to 127.0.0.1:{TCP_PORT}\n\
         add chain ip a13n filter {{ type filter hook output priority 0; policy accept; }}\n\
         add rule ip a13n filter ip protocol icmp drop\n"
    );
    let mut nft = Command::new("/usr/sbin/nft")
        .args(["-f", "-"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::inherit())
        .spawn()?;
    use io::Write;
    nft.stdin
        .take()
        .ok_or_else(|| io::Error::other("nft input unavailable"))?
        .write_all(rules.as_bytes())?;
    if !nft.wait()?.success() {
        return Err(io::Error::other("DNAT unavailable"));
    }
    Ok(Listeners { tcp, udp, dns })
}

fn check_kernel() -> io::Result<()> {
    let mut name: libc::utsname = unsafe { std::mem::zeroed() };
    if unsafe { libc::uname(&mut name) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let release = unsafe { std::ffi::CStr::from_ptr(name.release.as_ptr()) }
        .to_str()
        .map_err(io::Error::other)?;
    // 6.1.2 fixes reconnectable SEQPACKET pairs racing with peer closure.
    if !supported_kernel(release) {
        return Err(io::Error::other(
            "egress isolation requires Linux 6.1.2 or newer",
        ));
    }
    Ok(())
}

pub(crate) fn protect_process() -> io::Result<()> {
    unsafe {
        if libc::prctl(libc::PR_SET_DUMPABLE, 0, 0, 0, 0) != 0 {
            return Err(io::Error::last_os_error());
        }
        let limit = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        if libc::setrlimit(libc::RLIMIT_CORE, &limit) != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

/// Remove capabilities that cross the Session's process/device/network boundary,
/// not a small allowlist of commands or filesystem administration capabilities.
fn restrict_capabilities() -> io::Result<()> {
    // Raw device access; file-handle traversal; namespace/network reconfiguration;
    // other-process/kernel access. Keep CHOWN, DAC_OVERRIDE, FOWNER, SETUID/GID,
    // SETFCAP, KILL, NET_BIND_SERVICE and other native day-to-day capabilities.
    const REMOVED: &[u32] = &[
        2, 12, 13, 16, 17, 19, 21, 22, 25, 27, 30, 32, 33, 34, 36, 37, 38, 39, 40,
    ];
    #[repr(C)]
    struct Header {
        version: u32,
        pid: i32,
    }
    #[repr(C)]
    #[derive(Clone, Copy, Default)]
    struct Data {
        effective: u32,
        permitted: u32,
        inheritable: u32,
    }
    let header = Header {
        version: 0x20080522,
        pid: 0,
    };
    let mut data = [Data::default(); 2];
    unsafe {
        if libc::syscall(libc::SYS_capget, &header, data.as_mut_ptr()) != 0 {
            return Err(io::Error::last_os_error());
        }
        for &capability in REMOVED {
            if libc::prctl(libc::PR_CAPBSET_DROP, capability, 0, 0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            let mask = !(1 << (capability % 32));
            let entry = &mut data[(capability / 32) as usize];
            entry.effective &= mask;
            entry.permitted &= mask;
            entry.inheritable &= mask;
        }
        if libc::syscall(libc::SYS_capset, &header, data.as_ptr()) != 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

fn supported_kernel(release: &str) -> bool {
    let parts: Vec<_> = release
        .split(['.', '-', '+'])
        .take(3)
        .map(str::parse::<u32>)
        .collect();
    matches!(parts.as_slice(), [Ok(major), Ok(minor), Ok(patch)] if (*major, *minor, *patch) >= (6, 1, 2))
}

pub(crate) fn bind_fd(fd: RawFd, target: &Path) -> io::Result<()> {
    mount(
        Some(&format!("/proc/self/fd/{fd}")),
        target,
        None,
        libc::MS_BIND,
        None,
    )
}
fn readonly_bind(target: &Path) -> io::Result<()> {
    let source = target
        .to_str()
        .ok_or_else(|| io::Error::other("invalid mount path"))?;
    mount(Some(source), target, None, libc::MS_BIND, None)?;
    mount(
        None,
        target,
        None,
        libc::MS_REMOUNT | libc::MS_BIND | libc::MS_RDONLY | libc::MS_NOSUID | libc::MS_NODEV,
        None,
    )
}
fn run(executable: &str, args: &[&str]) -> io::Result<()> {
    if !Command::new(executable)
        .args(args)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()?
        .success()
    {
        return Err(io::Error::other("namespace network setup failed"));
    }
    Ok(())
}
pub(crate) fn mount(
    source: Option<&str>,
    target: &Path,
    filesystem: Option<&str>,
    flags: libc::c_ulong,
    data: Option<&str>,
) -> io::Result<()> {
    let source = source
        .map(CString::new)
        .transpose()
        .map_err(io::Error::other)?;
    let filesystem = filesystem
        .map(CString::new)
        .transpose()
        .map_err(io::Error::other)?;
    let data = data
        .map(CString::new)
        .transpose()
        .map_err(io::Error::other)?;
    let target = CString::new(target.as_os_str().as_bytes()).map_err(io::Error::other)?;
    let rc = unsafe {
        libc::mount(
            source.as_ref().map_or(std::ptr::null(), |s| s.as_ptr()),
            target.as_ptr(),
            filesystem.as_ref().map_or(std::ptr::null(), |s| s.as_ptr()),
            flags,
            data.as_ref()
                .map_or(std::ptr::null(), |s| s.as_ptr().cast()),
        )
    };
    if rc != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn supported_kernel_versions_include_vendor_suffixes() {
        for release in ["6.1.2", "6.1.158+", "6.8.0-136-generic", "6.18.49"] {
            assert!(supported_kernel(release));
        }
        for release in ["6.1.1", "6.0.12", "6.2-rc1", "broken"] {
            assert!(!supported_kernel(release));
        }
    }
    // Full namespace, native sudo and shared-system-tree tests are exercised by
    // tests/execution_linux.py in a disposable privileged Linux sandbox.
}
