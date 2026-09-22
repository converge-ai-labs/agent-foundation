//! Private management runtime for a daemon offering controlled Sessions.
//!
//! Only the broker and trusted bootstrap use this copied runtime. Payloads pivot
//! into a mount clone of the ORIGINAL tree; package installs are not overlays.
use crate::{
    config::{Config, TransportConfig},
    egress::namespace::{bind_fd, mount, protect_process},
    runtime::RuntimeState,
};
use std::{
    collections::BTreeMap,
    ffi::CString,
    fs, io,
    os::{
        fd::{AsRawFd, FromRawFd, OwnedFd},
        unix::{ffi::OsStrExt, fs::PermissionsExt, process::CommandExt},
    },
    path::{Path, PathBuf},
    process::Command,
};

pub(crate) const ORIGINAL: &str = "/original";
pub(crate) const EXECUTABLE: &str = "/bin/a13n-envd";
const BOOTSTRAP: &str = "/bootstrap.json";

/// Called with no running runtime, before admitting any Session. Startup artifacts
/// must be trusted by the launcher; a writable sandbox is not a trusted installer
/// for its next launch. Once entered, no management code loads from ORIGINAL.
pub(crate) fn enter(mut config: Config) -> io::Result<()> {
    if unsafe { libc::geteuid() } != 0 {
        return Err(io::Error::other(
            "controlled Sessions require a root launcher with native namespace permissions",
        ));
    }
    if fs::read_dir("/proc/self/task")?.count() != 1 {
        return Err(io::Error::other(
            "management bootstrap must be single-threaded",
        ));
    }
    let runtime_parent = config
        .runtime
        .as_ref()
        .and_then(|runtime| runtime.generation().parent())
        .ok_or_else(|| io::Error::other("missing runtime directory"))?
        .to_path_buf();
    config.bootstrap_files.push(runtime_parent.clone());
    if let Some(home) = std::env::var_os("HOME") {
        let home = PathBuf::from(home).join(".a13n");
        if home.exists() {
            config.bootstrap_files.push(fs::canonicalize(home)?);
        }
    }
    // Resolve every protected path while still in the original root. Workers
    // must not resolve these through payload-controlled symlinks after startup.
    for path in &mut config.bootstrap_files {
        *path = fs::canonicalize(&*path)?;
    }
    let mut image = Image::default();
    image.executable(&std::env::current_exe()?, Path::new(EXECUTABLE))?;
    for executable in ["/usr/bin/unshare", "/usr/sbin/ip", "/usr/sbin/nft"] {
        image.executable(Path::new(executable), Path::new(executable))?;
    }
    for path in [
        "/etc/resolv.conf",
        "/etc/hosts",
        "/etc/passwd",
        "/etc/group",
    ] {
        image.file(Path::new(path), Path::new(path))?;
    }
    // Deliberately do not copy ld.so.preload or extensible NSS configuration.
    // Management only resolves ordinary files/DNS; payload NSS stays native.
    image.files.insert(
        PathBuf::from("/etc/nsswitch.conf"),
        b"passwd: files\ngroup: files\nhosts: files dns\n".to_vec(),
    );
    let bundle = [
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ]
    .into_iter()
    .find(|path| Path::new(path).is_file())
    .ok_or_else(|| io::Error::other("system CA bundle unavailable"))?;
    image.file(
        Path::new(bundle),
        Path::new("/etc/ssl/certs/ca-certificates.crt"),
    )?;
    // Native TLS and reconnects only read these private copies, never mutable
    // original credential files. Preserve the original paths solely for masking.
    match &mut config.transport {
        TransportConfig::Stdio => {}
        TransportConfig::Http(http) => {
            image.credential(
                &mut http.credential_file,
                "credential",
                &mut config.bootstrap_files,
            )?;
            if let Some(path) = &mut http.tls_certificate_file {
                image.credential(path, "certificate", &mut config.bootstrap_files)?;
            }
            if let Some(path) = &mut http.tls_private_key_file {
                image.credential(path, "private-key", &mut config.bootstrap_files)?;
            }
        }
        TransportConfig::ReverseWebSocket(ws) => {
            image.credential(
                &mut ws.credential_file,
                "credential",
                &mut config.bootstrap_files,
            )?;
            if let Some(path) = &mut ws.tls_ca_file {
                image.credential(path, "extra-ca", &mut config.bootstrap_files)?;
            }
        }
    }
    for path in &config.bootstrap_files {
        use std::os::unix::fs::MetadataExt;
        let metadata = fs::metadata(path)?;
        if metadata.is_file() && metadata.nlink() != 1 {
            return Err(io::Error::other("protected bootstrap file has aliases"));
        }
    }
    // Finish and release the pre-bootstrap generation before re-exec. The same
    // locked runtime parent is rebound into management and prepared on restore.
    config.runtime.take();
    image.files.insert(
        PathBuf::from(BOOTSTRAP),
        serde_json::to_vec(&config).map_err(io::Error::other)?,
    );
    let hidden = config.bootstrap_files.clone();
    protect_process()?;
    if unsafe { libc::unshare(libc::CLONE_NEWNS) } != 0 {
        return Err(io::Error::last_os_error());
    }
    mount(
        None,
        Path::new("/"),
        None,
        libc::MS_REC | libc::MS_PRIVATE,
        None,
    )?;
    // FDs used as bind sources must refer to this mount namespace, not the
    // launcher's mounts retained by an FD opened before unshare.
    let state = fs::File::open(runtime_parent)?;
    let devices = ["null", "zero", "random", "urandom", "tty"]
        .into_iter()
        .map(|name| {
            use std::os::unix::fs::OpenOptionsExt;
            fs::OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_PATH)
                .open(format!("/dev/{name}"))
                .map(|fd| (name, fd))
        })
        .collect::<io::Result<Vec<_>>>()?;
    let original = clone_tree(Path::new("/"))?;
    // Reuse an existing mountpoint so no management directory is left on the
    // shared backing tree. The clone predates this private management mount.
    mount(
        Some("tmpfs"),
        Path::new("/run"),
        Some("tmpfs"),
        libc::MS_NOSUID | libc::MS_NODEV,
        Some("mode=755"),
    )?;
    for directory in ["original", "payload", "state", "tmp", "proc", "dev"] {
        fs::create_dir(Path::new("/run").join(directory))?;
    }
    attach_tree(original, Path::new("/run/original"))?;
    // Mask on the template mount, not by pathname on each new Session. The
    // protected mounts then follow renamed ancestors and all subsequent clones.
    for path in hidden {
        let target =
            Path::new("/run/original").join(path.strip_prefix("/").map_err(io::Error::other)?);
        if target.is_dir() {
            mount(
                Some("tmpfs"),
                &target,
                Some("tmpfs"),
                libc::MS_RDONLY | libc::MS_NOSUID | libc::MS_NODEV,
                Some("mode=000"),
            )?;
        } else {
            mount(Some("/dev/null"), &target, None, libc::MS_BIND, None)?;
        }
    }
    bind_fd(state.as_raw_fd(), Path::new("/run/state"))?;
    drop(state);
    mount(
        Some("/proc"),
        Path::new("/run/proc"),
        None,
        libc::MS_BIND,
        None,
    )?;
    mount(
        Some("tmpfs"),
        Path::new("/run/tmp"),
        Some("tmpfs"),
        libc::MS_NOSUID | libc::MS_NODEV,
        Some("mode=1777"),
    )?;
    for (name, file) in devices {
        let target = Path::new("/run/dev").join(name);
        fs::write(&target, [])?;
        bind_fd(file.as_raw_fd(), &target)?;
    }
    std::os::unix::fs::symlink("/proc/self/fd", "/run/dev/fd")?;
    for (name, fd) in [("stdin", 0), ("stdout", 1), ("stderr", 2)] {
        std::os::unix::fs::symlink(format!("/proc/self/fd/{fd}"), format!("/run/dev/{name}"))?;
    }
    for (path, contents) in image.files {
        let target = Path::new("/run").join(path.strip_prefix("/").map_err(io::Error::other)?);
        fs::create_dir_all(
            target
                .parent()
                .ok_or_else(|| io::Error::other("invalid image path"))?,
        )?;
        fs::write(&target, contents)?;
        fs::set_permissions(target, fs::Permissions::from_mode(0o555))?;
    }
    mount(
        None,
        Path::new("/run"),
        None,
        libc::MS_REMOUNT | libc::MS_RDONLY | libc::MS_NOSUID | libc::MS_NODEV,
        None,
    )?;
    if unsafe { libc::chroot(c"/run".as_ptr()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    std::env::set_current_dir("/")?;
    Err(Command::new(EXECUTABLE)
        .arg("--internal-managed-broker")
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LANG", "C.UTF-8")
        .exec())
}

pub(crate) fn restore() -> io::Result<Config> {
    protect_process()?;
    let mut config: Config =
        serde_json::from_slice(&fs::read(BOOTSTRAP)?).map_err(io::Error::other)?;
    if !config.egress.enabled {
        return Err(io::Error::other("invalid management configuration"));
    }
    config.managed = true;
    config.runtime = Some(RuntimeState::prepare(Path::new("/state")).map_err(io::Error::other)?);
    Ok(config)
}

/// A clone must originate from an attached mount in the caller's namespace.
/// Keeping ORIGINAL attached also avoids retaining an ancestor directory FD in
/// any worker. The moved clone shares inodes, not an overlay or copied rootfs.
pub(crate) fn clone_tree(path: &Path) -> io::Result<OwnedFd> {
    let path = CString::new(path.as_os_str().as_bytes()).map_err(io::Error::other)?;
    let fd = unsafe {
        libc::syscall(
            libc::SYS_open_tree,
            libc::AT_FDCWD,
            path.as_ptr(),
            1 | libc::O_CLOEXEC as u32 | libc::AT_RECURSIVE as u32,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { OwnedFd::from_raw_fd(fd as _) })
}

pub(crate) fn attach_tree(tree: OwnedFd, target: &Path) -> io::Result<()> {
    let path = CString::new(target.as_os_str().as_bytes()).map_err(io::Error::other)?;
    if unsafe {
        libc::syscall(
            libc::SYS_move_mount,
            tree.as_raw_fd(),
            c"".as_ptr(),
            libc::AT_FDCWD,
            path.as_ptr(),
            0x4,
        )
    } != 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub(crate) fn enter_original() -> io::Result<()> {
    let tree = clone_tree(Path::new(ORIGINAL))?;
    attach_tree(tree, Path::new("/payload"))?;
    std::env::set_current_dir("/payload")?;
    // Stack the old root on the new root, detach it, and leave no management
    // root or mount-namespace handles behind. A mere chroot would not suffice.
    if unsafe { libc::syscall(libc::SYS_pivot_root, c".".as_ptr(), c".".as_ptr()) } != 0
        || unsafe { libc::umount2(c".".as_ptr(), libc::MNT_DETACH) } != 0
    {
        return Err(io::Error::last_os_error());
    }
    std::env::set_current_dir("/")
}

#[derive(Default)]
struct Image {
    files: BTreeMap<PathBuf, Vec<u8>>,
}
impl Image {
    fn file(&mut self, source: &Path, target: &Path) -> io::Result<()> {
        if !self.files.contains_key(target) {
            self.files.insert(target.to_path_buf(), fs::read(source)?);
        }
        Ok(())
    }
    fn executable(&mut self, source: &Path, target: &Path) -> io::Result<()> {
        self.file(source, target)?;
        let output = Command::new("/usr/bin/ldd")
            .arg(source)
            .env_clear()
            .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
            .env("LC_ALL", "C")
            .output()?;
        let text = String::from_utf8(output.stdout).map_err(io::Error::other)?;
        if !output.status.success() {
            let error = String::from_utf8_lossy(&output.stderr);
            if text.contains("not a dynamic executable")
                || error.contains("not a dynamic executable")
            {
                return Ok(());
            }
            return Err(io::Error::other(
                "cannot resolve management executable dependencies",
            ));
        }
        for path in text.split_whitespace().filter(|part| part.starts_with('/')) {
            self.file(Path::new(path), Path::new(path))?;
        }
        Ok(())
    }
    fn credential(
        &mut self,
        path: &mut PathBuf,
        name: &str,
        hide: &mut Vec<PathBuf>,
    ) -> io::Result<()> {
        let original = fs::canonicalize(&*path)?;
        let target = PathBuf::from("/credentials").join(name);
        self.file(&original, &target)?;
        hide.push(original);
        *path = target;
        Ok(())
    }
}
