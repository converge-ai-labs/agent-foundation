//! Restricted filesystem/process views for the complete Session worker.
use super::boundary::{Access, Egress, Sandbox};
use std::{
    fs::{self, File},
    io::{self, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::process::CommandExt,
    },
    path::Path,
    process::Command,
};

pub(crate) const WORKER: &str = "/run/a13n-envd/envd";
pub(crate) const STATE: &str = "/run/a13n-session-state";

/// Pin bind sources before spawning. Bubblewrap consumes and closes these FDs;
/// neither the worker nor payload receives a handle to the outer filesystem.
pub(crate) fn command(
    sandbox: &Sandbox,
    grant_sources: &[super::boundary::GrantSource],
    egress: Egress,
    state: Option<&Path>,
    identity: super::Identity,
    managed: bool,
    ca: Option<&str>,
) -> io::Result<Command> {
    let Sandbox::Restricted { grants } = sandbox else {
        return Err(io::Error::other(
            "restricted launch requires Sandbox grants",
        ));
    };
    let mut command = Command::new("/usr/bin/bwrap");
    command.args([
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--new-session",
        "--die-with-parent",
        "--as-pid-1",
        "--cap-drop",
        "ALL",
        "--clearenv",
    ]);
    if unsafe { libc::geteuid() } != 0 {
        command.args([
            "--unshare-user",
            "--disable-userns",
            "--assert-userns-disabled",
        ]);
    } else {
        command.args(["--cap-add", "CAP_SETPCAP"]);
        if super::needs_worker(identity) {
            command.args(["--cap-add", "CAP_SETUID", "--cap-add", "CAP_SETGID"]);
        }
    }
    let source = |path: &Path| {
        if managed {
            Path::new(super::management::ORIGINAL)
                .join(path.strip_prefix("/").expect("absolute Sandbox path"))
        } else {
            path.to_path_buf()
        }
    };
    if egress == (Egress::Deny {}) {
        command.arg("--unshare-net");
    }
    let mut sources = Vec::new();
    // Bubblewrap's implicit parents are owner-only. The final worker may run
    // under a different identity, so shared view directories need traversal.
    for directory in ["/etc", "/run", "/run/a13n-envd"] {
        command.args(["--perms", "0755", "--dir", directory]);
    }
    for raw in ["/usr", "/bin", "/sbin", "/lib", "/lib64"] {
        let path = Path::new(raw);
        let origin = source(path);
        if origin.is_symlink() {
            command
                .arg("--symlink")
                .arg(fs::read_link(&origin)?)
                .arg(path);
        } else if origin.exists() {
            bind(&mut command, &mut sources, &origin, path, Access::ReadOnly)?;
        }
    }
    for raw in [
        "/etc/ld.so.cache",
        "/etc/localtime",
        "/etc/passwd",
        "/etc/group",
        "/etc/nsswitch.conf",
        "/etc/hosts",
        "/etc/resolv.conf",
        "/etc/ssl",
    ] {
        let path = Path::new(raw);
        let origin = source(path);
        if origin.exists() {
            bind(&mut command, &mut sources, &origin, path, Access::ReadOnly)?;
        }
    }
    command.args([
        "--proc", "/proc", "--dev", "/dev", "--perms", "1777", "--tmpfs", "/tmp",
    ]);
    if grants.len() != grant_sources.len() {
        return Err(io::Error::other("missing authorized Sandbox grant sources"));
    }
    for (grant, authorized) in grants.iter().zip(grant_sources) {
        // Synthetic ancestors belong to this view, not to the backing grant.
        // Set them explicitly so a root-to-user worker can traverse nested roots.
        let mut ancestors: Vec<_> = grant
            .path
            .ancestors()
            .skip(1)
            .filter(|path| *path != Path::new("/"))
            .collect();
        ancestors.reverse();
        for ancestor in ancestors {
            command.args(["--perms", "0755", "--dir"]).arg(ancestor);
        }
        let file = open_source(&source(&grant.path))?;
        authorized.check(&file)?;
        bind_file(&mut command, &mut sources, file, &grant.path, grant.access);
    }
    if let Some(state) = state {
        bind(
            &mut command,
            &mut sources,
            state,
            Path::new(STATE),
            Access::ReadWrite,
        )?;
    }
    bind(
        &mut command,
        &mut sources,
        &std::env::current_exe()?,
        Path::new(WORKER),
        Access::ReadOnly,
    )?;
    if let Some(ca) = ca {
        let mut bundle = fs::read("/etc/ssl/certs/ca-certificates.crt")?;
        bundle.extend_from_slice(b"\n");
        bundle.extend_from_slice(ca.as_bytes());
        data(&mut command, &mut sources, &bundle, "/run/a13n-envd/ca.pem")?;
        data(
            &mut command,
            &mut sources,
            b"nameserver 127.0.0.1\noptions timeout:1 attempts:2\n",
            "/etc/resolv.conf",
        )?;
    }
    let program = if managed {
        bind(
            &mut command,
            &mut sources,
            Path::new(super::management::WORKER_IMAGE),
            Path::new(super::management::WORKER_BOOTSTRAP),
            Access::ReadOnly,
        )?;
        super::management::worker_argv()?
    } else {
        vec![WORKER.into()]
    };
    command.args([
        "--setenv",
        "PATH",
        "/usr/bin:/bin:/usr/sbin:/sbin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--chdir",
        "/",
        "--",
    ]);
    command.args(program);
    unsafe {
        command.pre_exec(move || {
            for file in &sources {
                if libc::fcntl(file.as_raw_fd(), libc::F_SETFD, 0) < 0 {
                    return Err(io::Error::last_os_error());
                }
            }
            Ok(())
        });
    }
    Ok(command)
}

fn bind(
    command: &mut Command,
    sources: &mut Vec<File>,
    source: &Path,
    target: &Path,
    access: Access,
) -> io::Result<()> {
    bind_file(command, sources, open_source(source)?, target, access);
    Ok(())
}

fn open_source(source: &Path) -> io::Result<File> {
    if let Ok(path) = source.strip_prefix(super::management::ORIGINAL) {
        super::management::open_original(path)
    } else {
        File::open(source)
    }
}

fn bind_file(
    command: &mut Command,
    sources: &mut Vec<File>,
    file: File,
    target: &Path,
    access: Access,
) {
    command
        .arg(match access {
            Access::ReadOnly => "--ro-bind-fd",
            Access::ReadWrite => "--bind-fd",
        })
        .arg(file.as_raw_fd().to_string())
        .arg(target);
    sources.push(file);
}

fn data(
    command: &mut Command,
    sources: &mut Vec<File>,
    bytes: &[u8],
    target: &str,
) -> io::Result<()> {
    let fd = unsafe { libc::memfd_create(c"a13n-sandbox-data".as_ptr(), libc::MFD_CLOEXEC) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let mut file = unsafe { File::from_raw_fd(fd) };
    file.write_all(bytes)?;
    use std::io::{Seek, SeekFrom};
    file.seek(SeekFrom::Start(0))?;
    command
        .args(["--perms", "0444", "--ro-bind-data"])
        .arg(fd.to_string())
        .arg(target);
    sources.push(file);
    Ok(())
}
