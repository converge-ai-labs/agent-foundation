use serde::{Deserialize, Serialize};
use std::{
    ffi::CString,
    fs, io,
    net::{TcpListener, UdpSocket},
    os::fd::{AsRawFd, RawFd},
    os::unix::ffi::OsStrExt,
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
    pub ca_pem: String,
}

pub(crate) struct Listeners {
    pub tcp: TcpListener,
    pub udp: std::os::fd::OwnedFd,
    pub dns: UdpSocket,
}

/// Called only in the fresh, single-threaded unshare child, never from pre_exec.
pub(crate) fn prepare(plan: &Mounts) -> io::Result<Listeners> {
    check_kernel()?;
    if std::process::id() != 1 {
        return Err(io::Error::other("egress worker must own namespace PID 1"));
    }
    let hide = plan
        .hide
        .iter()
        .filter(|path| path.exists())
        .map(fs::canonicalize)
        .collect::<io::Result<Vec<_>>>()?;
    if !plan.workspace.is_absolute()
        || plan.workspace == Path::new("/")
        || ["/run", "/proc", "/sys", "/dev"]
            .iter()
            .any(|p| plan.workspace.starts_with(p))
        || hide
            .iter()
            .any(|p| plan.workspace.starts_with(p) || p.starts_with(&plan.workspace))
    {
        return Err(io::Error::other(
            "workspace overlaps protected runtime paths",
        ));
    }
    for hidden in &hide {
        use std::os::unix::fs::MetadataExt;
        if let Ok(metadata) = fs::symlink_metadata(hidden)
            && (metadata.file_type().is_symlink() || (metadata.is_file() && metadata.nlink() != 1))
        {
            return Err(io::Error::other("protected bootstrap file has aliases"));
        }
    }
    protect_process()?;
    mount(
        None,
        Path::new("/"),
        None,
        libc::MS_REC | libc::MS_PRIVATE,
        None,
    )?;
    // Pin workspace and executable before masking /tmp and /run.
    let workspace = fs::File::open(&plan.workspace)?;
    let mut executable = fs::File::open("/proc/self/exe")?;
    let resolv = fs::canonicalize("/etc/resolv.conf")?;
    let devices: Vec<_> = ["null", "zero", "random", "urandom"]
        .into_iter()
        .map(|name| fs::File::open(format!("/dev/{name}")).map(|file| (name, file)))
        .collect::<io::Result<_>>()?;
    let system_ca = [
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ]
    .into_iter()
    .find(|path| Path::new(path).is_file())
    .ok_or_else(|| io::Error::other("system CA bundle unavailable"))?;
    let mut bundle = fs::read(system_ca)?;
    bundle.push(b'\n');
    bundle.extend_from_slice(plan.ca_pem.as_bytes());
    for private in ["/tmp", "/run"] {
        mount(
            Some("tmpfs"),
            Path::new(private),
            Some("tmpfs"),
            libc::MS_NOSUID | libc::MS_NODEV,
            Some("mode=755"),
        )?;
    }
    mount(
        Some("tmpfs"),
        Path::new("/dev"),
        Some("tmpfs"),
        libc::MS_NOSUID,
        Some("mode=755"),
    )?;
    for (name, file) in &devices {
        let target = PathBuf::from(format!("/dev/{name}"));
        fs::write(&target, [])?;
        bind_fd(file.as_raw_fd(), &target)?;
    }
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
    )?;
    fs::create_dir_all(RUNTIME)?;
    fs::write(format!("{RUNTIME}/ca.pem"), bundle)?;
    // A read-only bind of the original inode would still permit writes through
    // a writable workspace alias. Copy into private tmpfs before sealing it.
    let executable_path = format!("{RUNTIME}/envd");
    io::copy(&mut executable, &mut fs::File::create(&executable_path)?)?;
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&executable_path, fs::Permissions::from_mode(0o555))?;
    let source = format!("{RUNTIME}/ca.pem");
    mount(
        Some(&source),
        Path::new(system_ca),
        None,
        libc::MS_BIND,
        None,
    )?;
    fs::write(
        format!("{RUNTIME}/resolv.conf"),
        "nameserver 127.0.0.1\noptions timeout:1 attempts:2\n",
    )?;
    // A systemd resolver target may have disappeared with the private /run mount.
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
    for hidden in &hide {
        if hidden.exists() && !plan.workspace.starts_with(hidden) {
            if hidden.is_dir() {
                mount(
                    Some("tmpfs"),
                    hidden,
                    Some("tmpfs"),
                    libc::MS_RDONLY | libc::MS_NOSUID | libc::MS_NODEV,
                    Some("mode=000"),
                )?;
            } else {
                mount(Some("/dev/null"), hidden, None, libc::MS_BIND, None)?;
            }
        }
    }
    // The private procfs belongs to the worker's PID namespace, installed by unshare.
    run("/usr/sbin/ip", &["link", "set", "lo", "up"])?;
    // Route all numeric destinations locally so DNAT observes original addresses.
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
        .stderr(std::process::Stdio::null())
        .spawn()?;
    use io::Write;
    nft.stdin
        .take()
        .ok_or_else(|| io::Error::other("nft input unavailable"))?
        .write_all(rules.as_bytes())?;
    if !nft.wait()?.success() {
        return Err(io::Error::other("DNAT unavailable"));
    }
    // Make the image immutable, then grant only the workspace and private runtime writes.
    readonly_tree(Path::new("/"))?;
    writable_mount(Path::new("/tmp"))?;
    writable_mount(Path::new("/run"))?;
    writable_mount(Path::new("/dev/shm"))?;
    writable_bind(workspace.as_raw_fd(), &plan.workspace)?;
    // Seal the directory too: a writable parent must not permit replacing it.
    readonly_bind(Path::new(RUNTIME))?;
    drop_capabilities()?;
    super::seccomp::install()?;
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
    // 6.1.2 contains 3ff8bff704f4: SEQPACKET pairs cannot transition back to
    // a reconnectable state when peer closure races with sendmsg. Rust spawn
    // needs these pairs. Reject older kernels even if some vendors backported it.
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

pub(crate) fn drop_capabilities() -> io::Result<()> {
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
    unsafe {
        if libc::prctl(
            libc::PR_CAP_AMBIENT,
            libc::PR_CAP_AMBIENT_CLEAR_ALL,
            0,
            0,
            0,
        ) != 0
        {
            return Err(io::Error::last_os_error());
        }
        // Drop the bounding set before clearing CAP_SETPCAP.
        for capability in 0..=40 {
            if libc::prctl(libc::PR_CAPBSET_DROP, capability, 0, 0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
        }
        let header = Header {
            version: 0x20080522,
            pid: 0,
        };
        let data = [Data {
            effective: 0,
            permitted: 0,
            inheritable: 0,
        }; 2];
        if libc::syscall(libc::SYS_capset, &header, data.as_ptr()) != 0 {
            return Err(io::Error::last_os_error());
        }
        if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
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

fn bind_fd(fd: RawFd, target: &Path) -> io::Result<()> {
    mount(
        Some(&format!("/proc/self/fd/{fd}")),
        target,
        None,
        libc::MS_BIND,
        None,
    )
}
fn writable_bind(fd: RawFd, target: &Path) -> io::Result<()> {
    fs::create_dir_all(target)?;
    bind_fd(fd, target)?;
    writable_mount(target)
}
fn writable_mount(target: &Path) -> io::Result<()> {
    mount(
        None,
        target,
        None,
        libc::MS_REMOUNT | libc::MS_BIND | libc::MS_NOSUID | libc::MS_NODEV,
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
fn readonly_tree(target: &Path) -> io::Result<()> {
    #[repr(C)]
    struct Attr {
        set: u64,
        clear: u64,
        propagation: u64,
        userns_fd: u64,
    }
    let path = cpath(target)?;
    let attrs = Attr {
        set: 1,
        clear: 0,
        propagation: 0,
        userns_fd: 0,
    };
    let rc = unsafe {
        libc::syscall(
            libc::SYS_mount_setattr,
            libc::AT_FDCWD,
            path.as_ptr(),
            0x8000u32,
            &attrs,
            std::mem::size_of::<Attr>(),
        )
    };
    if rc != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
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
fn cpath(path: &Path) -> io::Result<CString> {
    CString::new(path.as_os_str().as_bytes()).map_err(io::Error::other)
}
fn mount(
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
    let target = cpath(target)?;
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

    #[test]
    #[ignore = "requires a disposable Linux user/mount/network/PID namespace"]
    fn isolated_filesystem_and_payload() {
        assert_eq!(
            std::process::id(),
            1,
            "run this test under unshare --pid --fork --mount-proc"
        );
        let workspace = PathBuf::from("/workspace");
        fs::create_dir_all(&workspace).unwrap();
        let listeners = prepare(&Mounts {
            workspace: workspace.clone(),
            hide: vec![],
            ca_pem: String::new(),
        })
        .unwrap();
        assert_eq!(listeners.tcp.local_addr().unwrap().port(), TCP_PORT);
        assert!(listeners.udp.as_raw_fd() >= 0);
        assert_eq!(listeners.dns.local_addr().unwrap().port(), DNS_PORT);
        let output = Command::new("/usr/bin/python3")
            .args([
                "-c",
                r#"
import os,socket
open('/workspace/writable','w').write('yes')
open('/tmp/writable','w').write('yes')
try:
    open('/etc/egress-must-not-write','w')
    raise AssertionError('root filesystem writable')
except OSError:
    pass
for path in ['/run/a13n-envd/envd', '/run/a13n-envd/ca.pem']:
    try:
        open(path,'wb')
        raise AssertionError('protected runtime writable')
    except OSError:
        pass
try:
    os.rename('/run/a13n-envd','/run/replaced-runtime')
    raise AssertionError('protected runtime replaceable')
except OSError:
    pass
try:
    socket.socket(socket.AF_UNIX)
    raise AssertionError('Unix socket allowed')
except PermissionError:
    pass
try:
    socket.socket(socket.AF_VSOCK)
    raise AssertionError('VSOCK allowed')
except PermissionError:
    pass
assert 'CapEff:\t0000000000000000' in open('/proc/self/status').read()
assert 'NoNewPrivs:\t1' in open('/proc/self/status').read()
a,b=socket.socketpair()
a.send(b'ok')
assert b.recv(2)==b'ok'
print('isolated payload passed')
"#,
            ])
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(output.stdout, b"isolated payload passed\n");
    }
}
