//! Native MXC/PSEC only: no AppContainer fallback, accounts, ACL edits or elevation.
use super::boundary::{Access, Egress, GrantSource, Sandbox};
use mxc_sdk::mxc_common::{
    cmdline::{CommandLineContext, cmdline_from_argv_for_context},
    logger::{Logger, Mode},
    models::{
        BaseProcessUiConfig, ContainerPolicy, ExecutionRequest, FallbackPolicy, NetworkPolicy,
        UiPolicy,
    },
    sandbox_process::{SandboxBackend, StdioMode},
};
use mxc_sdk::process_container_common::{
    PROCESS_SECURITY_ENVIRONMENT_FLAG_NONE, ProcessSecurityEnvironmentApi,
    SecurityEnvironmentStartupInfo, base_container_runner::BaseContainerRunner,
};
use serde::{Deserialize, Serialize};
use std::{
    fs::{File, OpenOptions},
    io,
    os::windows::{
        fs::OpenOptionsExt,
        io::{AsRawHandle, BorrowedHandle},
    },
    path::{Path, PathBuf},
    process::Stdio,
};
use tokio::io::AsyncWriteExt;
use windows_sys::Win32::{
    Storage::FileSystem::{
        BY_HANDLE_FILE_INFORMATION, FILE_FLAG_BACKUP_SEMANTICS, FILE_LIST_DIRECTORY,
        FILE_READ_ATTRIBUTES, FILE_SHARE_READ, FILE_SHARE_WRITE, GetFileInformationByHandle,
    },
    System::Console::{GetStdHandle, STD_INPUT_HANDLE},
};

// tracelogging 1.2.3 uses these documented exports on MSVC and GNU.
#[link(name = "advapi32")]
unsafe extern "system" {}

/// Use PSEC 1.0's capability/peer network policy, also used by Codex's pinned
/// MXC runner. SDK 1.0's directional ingress API requires PSEC 1.1 and strips
/// these explicit capabilities. Keep both directional fields unset.
fn base_policy(egress: Egress) -> io::Result<ContainerPolicy> {
    let inherit = match egress {
        Egress::Inherit {} => true,
        Egress::Deny {} => false,
        Egress::Controlled {} => {
            return Err(io::Error::other("MXC controlled egress is unavailable"));
        }
    };
    let mut capabilities = vec!["registryRead".into()];
    if inherit {
        capabilities.extend(
            [
                "internetClient",
                "privateNetworkClientServer",
                "networkLoopback",
            ]
            .map(str::to_owned),
        );
    }
    Ok(ContainerPolicy {
        capabilities,
        fallback: FallbackPolicy {
            allow_dacl_mutation: false,
        },
        default_network_policy: if inherit {
            NetworkPolicy::Allow
        } else {
            NetworkPolicy::Block
        },
        allow_local_network: inherit,
        allowed_proxy_peer: inherit.then(|| "MXC-Loopback".into()),
        network_specified: true,
        network_mode_specified: true,
        // PowerShell DLL initialization needs Win32k and desktop handles.
        // Keep clipboard, input-injection and system-control restrictions.
        ui: UiPolicy {
            disable: false,
            ..Default::default()
        },
        base_process_ui: BaseProcessUiConfig {
            isolation: "desktop".into(),
            ..Default::default()
        },
        ..Default::default()
    })
}

pub(crate) fn probe(egress: Egress) -> io::Result<()> {
    use mxc_sdk::process_security_environment_spec::process_security_environment_layout::{
        EndpointPolicyT, FilterAction, NetworkPolicyT, ProcessSecurityEnvironmentT, SchemaVersionT,
        finish_process_security_environment_buffer,
    };
    let policy = base_policy(egress)?;
    let mut builder = flatbuffers::FlatBufferBuilder::new();
    let mut endpoint = EndpointPolicyT::default();
    endpoint.default_action = if policy.default_network_policy == NetworkPolicy::Allow {
        FilterAction::allow
    } else {
        FilterAction::deny
    };
    let mut network = NetworkPolicyT::default();
    network.egress = Some(Box::new(endpoint));
    network.allowed_appcontainer_peer = policy.allowed_proxy_peer;
    let mut spec = ProcessSecurityEnvironmentT::default();
    spec.version = SchemaVersionT { major: 1, minor: 0 };
    spec.capabilities = Some(policy.capabilities.join(","));
    spec.network_policy = Some(Box::new(network));
    let spec = spec.pack(&mut builder);
    finish_process_security_environment_buffer(&mut builder, spec);
    let environment = ProcessSecurityEnvironmentApi::load()
        .and_then(|api| {
            api.create(
                builder.finished_data(),
                PROCESS_SECURITY_ENVIRONMENT_FLAG_NONE,
            )
        })
        .map_err(io::Error::other)?;
    let startup = SecurityEnvironmentStartupInfo::new(Default::default(), environment.raw(), &[])
        .map_err(io::Error::other)?;
    drop(startup);
    environment.close();
    Ok(())
}

pub(crate) fn open_directory(path: &Path) -> io::Result<File> {
    // Attribute-only handles do not participate in Windows sharing checks.
    // Request directory read access so withholding delete sharing pins the grant.
    OpenOptions::new()
        .access_mode(FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)
}

pub(crate) fn directory_identity(file: &File) -> io::Result<GrantSource> {
    if !file.metadata()?.is_dir() {
        return Err(io::Error::other("Sandbox grant is not a directory"));
    }
    let mut info = BY_HANDLE_FILE_INFORMATION::default();
    if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(GrantSource {
        device: u64::from(info.dwVolumeSerialNumber),
        inode: (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
    })
}

/// Read-only runtime roots, resolved by Windows rather than the command environment.
pub(crate) fn platform_roots() -> io::Result<Vec<PathBuf>> {
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::{
        Foundation::RPC_E_CHANGED_MODE,
        System::{
            Com::{COINIT_MULTITHREADED, CoInitializeEx, CoTaskMemFree, CoUninitialize},
            SystemInformation::GetWindowsDirectoryW,
        },
        UI::Shell::{FOLDERID_ProgramFiles, FOLDERID_ProgramFilesX86, SHGetKnownFolderPath},
    };
    let mut buffer = vec![0_u16; 32768];
    let count = unsafe { GetWindowsDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
    if count == 0 || count >= buffer.len() {
        return Err(io::Error::other("cannot locate Windows runtime directory"));
    }
    let mut roots = vec![PathBuf::from(std::ffi::OsString::from_wide(
        &buffer[..count],
    ))];
    let initialized = unsafe { CoInitializeEx(std::ptr::null(), COINIT_MULTITHREADED as u32) };
    if initialized < 0 && initialized != RPC_E_CHANGED_MODE {
        return Err(io::Error::other(
            "cannot initialize Windows known-folder lookup",
        ));
    }
    struct ComGuard(bool);
    impl Drop for ComGuard {
        fn drop(&mut self) {
            if self.0 {
                unsafe { CoUninitialize() };
            }
        }
    }
    let _guard = ComGuard(initialized >= 0);
    for folder in [FOLDERID_ProgramFiles, FOLDERID_ProgramFilesX86] {
        let mut raw = std::ptr::null_mut();
        let status = unsafe { SHGetKnownFolderPath(&folder, 0, std::ptr::null_mut(), &mut raw) };
        if status < 0 || raw.is_null() {
            if !raw.is_null() {
                unsafe { CoTaskMemFree(raw.cast()) };
            }
            return Err(io::Error::other("cannot locate Windows program directory"));
        }
        let mut length = 0;
        unsafe {
            while *raw.add(length) != 0 {
                length += 1;
            }
        }
        let path = PathBuf::from(std::ffi::OsString::from_wide(unsafe {
            std::slice::from_raw_parts(raw, length)
        }));
        unsafe { CoTaskMemFree(raw.cast()) };
        roots.push(path);
    }
    roots.into_iter().map(std::fs::canonicalize).collect()
}

/// Materialize Codex-style broad read as available volumes and immediate children.
/// A volume-root grant alone does not cover descendants on every PSEC version.
pub(crate) fn full_disk_read_roots() -> io::Result<Vec<PathBuf>> {
    use mxc_sdk::mxc_common::filesystem_object::{
        ExistingObjectComparison, compare_existing_filesystem_objects,
    };
    use windows_sys::Win32::{
        Storage::FileSystem::{GetDriveTypeW, GetLogicalDrives},
        System::WindowsProgramming::DRIVE_REMOTE,
    };
    let drives = unsafe { GetLogicalDrives() };
    if drives == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut roots = Vec::new();
    for index in 0..26 {
        if drives & (1 << index) == 0 {
            continue;
        }
        let letter = char::from(b'A' + index as u8);
        let root = PathBuf::from(format!(r"{letter}:\"));
        let wide: Vec<u16> = format!("{letter}:\\\0").encode_utf16().collect();
        // A mapped SMB drive can block directory enumeration for minutes while
        // disconnected. Broad disk reads do not implicitly grant network shares.
        if unsafe { GetDriveTypeW(wide.as_ptr()) } == DRIVE_REMOTE {
            continue;
        }
        // Collect before adding grants so a disconnect during iteration follows
        // the same whole-volume skip policy as a failure to open the directory.
        let entries =
            match std::fs::read_dir(&root).and_then(Iterator::collect::<io::Result<Vec<_>>>) {
                Ok(entries) => entries,
                Err(error) if inaccessible_volume(&error) => continue,
                Err(error) => return Err(error),
            };
        roots.push(root);
        for entry in entries {
            let path = entry.path();
            if path.to_str().is_some()
                && compare_existing_filesystem_objects(&path, &path)
                    == ExistingObjectComparison::Same
            {
                roots.push(path);
            }
        }
    }
    Ok(roots)
}

fn inaccessible_volume(error: &io::Error) -> bool {
    use windows_sys::Win32::Foundation::{
        ERROR_BAD_NETPATH, ERROR_CONNECTION_UNAVAIL, ERROR_DEVICE_NOT_CONNECTED, ERROR_NOT_READY,
    };
    matches!(
        error.kind(),
        io::ErrorKind::NotFound
            | io::ErrorKind::PermissionDenied
            | io::ErrorKind::NetworkUnreachable
            | io::ErrorKind::HostUnreachable
    ) || matches!(
        error.raw_os_error().map(|code| code as u32),
        Some(
            ERROR_NOT_READY
                | ERROR_BAD_NETPATH
                | ERROR_CONNECTION_UNAVAIL
                | ERROR_DEVICE_NOT_CONNECTED
        )
    )
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Launch {
    sandbox: Sandbox,
    sources: Vec<GrantSource>,
    protected: Vec<PathBuf>,
    egress: Egress,
    state: PathBuf,
    target: Target,
}

#[derive(Clone, Copy, Deserialize, Serialize)]
pub(crate) enum Target {
    Session,
    Directory,
}

/// Own state until the kernel confirms that the complete Job is empty.
/// Cancellation drops the kill-on-close Job but retains state: closing a handle
/// requests termination; it does not establish a completed cleanup fence.
pub(crate) struct Worker {
    pub child: tokio::process::Child,
    job: crate::windows_job::WindowsJob,
    state: Option<super::StateDirectory>,
    _reservation: Option<crate::daemon::execution::Reservation>,
}

impl Worker {
    pub async fn shutdown(&mut self) -> io::Result<()> {
        let grace = std::time::Duration::from_secs(4);
        self.job.terminate_and_wait(grace).await?;
        tokio::time::timeout(grace, self.child.wait())
            .await
            .map_err(io::Error::other)??;
        self.state.take();
        Ok(())
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        if let Some(state) = self.state.take() {
            std::mem::forget(state);
        }
    }
}

/// The broker owns the wrapper and all descendants before any code is released.
/// Stdin carries one bounded launcher header, then the existing worker protocol.
pub(crate) async fn spawn(
    sandbox: &Sandbox,
    sources: &[GrantSource],
    protected: &[PathBuf],
    egress: Egress,
    state: super::StateDirectory,
    target: Target,
    mut reservation: Option<crate::daemon::execution::Reservation>,
) -> io::Result<Worker> {
    probe(egress)?;
    let launch = Launch {
        sandbox: sandbox.clone(),
        sources: sources.to_vec(),
        protected: protected.to_vec(),
        egress,
        state: state.0.clone(),
        target,
    };
    let bytes = serde_json::to_vec(&launch).map_err(io::Error::other)?;
    if bytes.len() > 16 * 1024 * 1024 {
        return Err(io::Error::other("MXC launcher request exceeds limit"));
    }
    std::fs::create_dir_all(state.0.join("home"))?;
    std::fs::create_dir_all(state.0.join("tmp"))?;
    let mut command = tokio::process::Command::new(std::env::current_exe()?);
    command
        .arg("--internal-mxc-launcher")
        .current_dir(&state.0)
        .env_clear()
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let (child, job) = crate::windows_job::WindowsJob::spawn(&mut command).await?;
    if let Some(reservation) = &mut reservation {
        reservation.started = true;
    }
    let mut worker = Worker {
        child,
        job,
        state: Some(state),
        _reservation: reservation,
    };
    let input = worker
        .child
        .stdin
        .as_mut()
        .ok_or_else(|| io::Error::other("missing MXC input"))?;
    tokio::time::timeout(std::time::Duration::from_secs(15), async {
        input.write_all(&(bytes.len() as u32).to_be_bytes()).await?;
        input.write_all(&bytes).await
    })
    .await
    .map_err(io::Error::other)??;
    Ok(worker)
}

/// Called before the Tokio runtime. Read exactly the header: buffered stdin
/// would steal bytes that belong to the child inheriting the same pipe.
pub(crate) fn run_launcher() -> io::Result<i32> {
    let handle = unsafe { GetStdHandle(STD_INPUT_HANDLE) };
    if handle.is_null() || handle == windows_sys::Win32::Foundation::INVALID_HANDLE_VALUE {
        return Err(io::Error::other("missing MXC launcher pipe"));
    }
    let handle = unsafe { BorrowedHandle::borrow_raw(handle) }.try_clone_to_owned()?;
    let launch: Launch = super::worker::read_bootstrap(&mut File::from(handle))?;
    probe(launch.egress)?;
    let Sandbox::Restricted {
        grants,
        full_disk_read,
    } = &launch.sandbox
    else {
        return Err(io::Error::other("MXC requires a restricted Sandbox"));
    };
    if grants.len() != launch.sources.len() || launch.egress.controlled() {
        return Err(io::Error::other("invalid MXC boundary"));
    }
    let mut retained = Vec::new();
    let mut read = platform_roots()?;
    let system_root = unicode(&read[0])?;
    if *full_disk_read {
        read.extend(full_disk_read_roots()?);
    }
    let mut write = vec![std::fs::canonicalize(&launch.state)?];
    for (grant, source) in grants.iter().zip(&launch.sources) {
        let file = open_directory(&grant.path)?;
        if directory_identity(&file)? != *source {
            return Err(io::Error::other("Sandbox grant directory was replaced"));
        }
        retained.push(file);
        match grant.access {
            Access::ReadOnly => read.push(grant.path.clone()),
            Access::ReadWrite => write.push(grant.path.clone()),
        }
    }
    let executable = std::env::current_exe()?;
    read.push(executable.clone());
    let target = match launch.target {
        Target::Session => "--internal-session-worker",
        Target::Directory => "--internal-directory-worker",
    };
    let request = ExecutionRequest {
        script_code: cmdline_from_argv_for_context(
            &[unicode(&executable)?, target.to_owned()],
            CommandLineContext::WindowsCreateProcess,
        )
        .map_err(io::Error::other)?,
        working_directory: unicode(&launch.state)?,
        // Explicit private environment; never synthesize or inherit Host defaults.
        inherit_default_env: false,
        env: Some(vec![
            format!("SystemRoot={system_root}"),
            format!("WINDIR={system_root}"),
            format!("HOME={}", unicode(&launch.state.join("home"))?),
            format!("USERPROFILE={}", unicode(&launch.state.join("home"))?),
            format!("LOCALAPPDATA={}", unicode(&launch.state.join("home"))?),
            format!("TEMP={}", unicode(&launch.state.join("tmp"))?),
            format!("TMP={}", unicode(&launch.state.join("tmp"))?),
        ]),
        policy: ContainerPolicy {
            denied_paths: launch
                .protected
                .iter()
                .map(|p| unicode(p))
                .collect::<io::Result<_>>()?,
            readonly_paths: read.iter().map(|p| unicode(p)).collect::<io::Result<_>>()?,
            readwrite_paths: write
                .iter()
                .map(|p| unicode(p))
                .collect::<io::Result<_>>()?,
            ..base_policy(launch.egress)?
        },
        ..Default::default()
    };
    let mut logger = Logger::new(Mode::Buffer);
    let mut child = BaseContainerRunner::new()
        .spawn(&request, &mut logger, StdioMode::Inherit)
        .map_err(|error| io::Error::other(error.error_message))?;
    // Retain native policy and directory handles for the complete worker lifetime.
    // Never emit the SDK log buffer: it may include command/environment contents.
    let result = child.wait().map_err(io::Error::other);
    drop(retained);
    result
}

fn unicode(path: &Path) -> io::Result<String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| io::Error::other("MXC requires Unicode native paths"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inaccessible_volume_errors_match_codex_without_hiding_unknown_failures() {
        for kind in [
            io::ErrorKind::NotFound,
            io::ErrorKind::PermissionDenied,
            io::ErrorKind::NetworkUnreachable,
            io::ErrorKind::HostUnreachable,
        ] {
            assert!(inaccessible_volume(&io::Error::from(kind)));
        }
        for code in [21, 53, 1201, 1167] {
            assert!(inaccessible_volume(&io::Error::from_raw_os_error(code)));
        }
        assert!(!inaccessible_volume(&io::Error::other(
            "unexpected failure"
        )));
        assert!(!inaccessible_volume(&io::Error::from_raw_os_error(87)));
    }

    #[test]
    #[ignore = "requires native MXC/PSEC support on the Windows host"]
    fn native_mxc_probe() {
        probe(Egress::Deny {}).expect("native MXC create/startup-info/close probe must succeed");
    }

    #[test]
    #[ignore = "requires native MXC/PSEC loopback capability and peer support"]
    fn native_mxc_probe_inherit() {
        probe(Egress::Inherit {}).expect("native MXC inherit probe must succeed");
    }

    #[test]
    fn network_policy_uses_native_v1_capabilities_without_directional_overrides() {
        for egress in [Egress::Inherit {}, Egress::Deny {}] {
            let policy = base_policy(egress).unwrap();
            let inherit = egress == (Egress::Inherit {});
            assert!(policy.network_egress.is_none());
            assert!(policy.network_ingress.is_none());
            assert_eq!(
                policy.default_network_policy == NetworkPolicy::Allow,
                inherit
            );
            assert_eq!(
                policy.allowed_proxy_peer.as_deref(),
                inherit.then_some("MXC-Loopback")
            );
            for capability in [
                "internetClient",
                "privateNetworkClientServer",
                "networkLoopback",
            ] {
                assert_eq!(
                    policy.capabilities.iter().any(|value| value == capability),
                    inherit
                );
            }
            assert!(!policy.fallback.allow_dacl_mutation);
        }
        assert!(base_policy(Egress::Controlled {}).is_err());
    }

    // Subprocess fixture for the native policy test. The ordinary unit suite
    // does not enter MXC or perform networking through this entry point.
    #[test]
    fn mxc_network_child() {
        let Ok(payload) = std::env::var("A13N_ENVD_MXC_NETWORK_TEST") else {
            return;
        };
        let (inherit, addresses, outside, broad_read): (
            bool,
            Vec<std::net::SocketAddr>,
            PathBuf,
            bool,
        ) = serde_json::from_str(&payload).unwrap();
        std::fs::write("allowed.txt", b"allowed").unwrap();
        assert_eq!(std::fs::read("allowed.txt").unwrap(), b"allowed");
        if broad_read {
            assert_eq!(std::fs::read(&outside).unwrap(), b"host-only sentinel");
            let cwd = std::env::current_dir().unwrap();
            let resolved = crate::windows_path::canonicalize(&cwd).unwrap();
            let directory = crate::windows_directory::Dir::open_ambient_dir(
                &resolved,
                cap_std::ambient_authority(),
            )
            .unwrap();
            directory.create_dir("native-dir").unwrap();
            std::fs::write("native-source", b"rename").unwrap();
            directory
                .rename("native-source", &directory, "native-target")
                .unwrap();
            assert_eq!(directory.read("native-target").unwrap(), b"rename");
            let child = crate::windows_directory::Dir::open_ambient_dir(
                resolved.join("native-dir"),
                cap_std::ambient_authority(),
            )
            .unwrap();
            directory.rename("native-target", &child, "moved").unwrap();
            assert_eq!(child.read("moved").unwrap(), b"rename");
            child.remove_file("moved").unwrap();
            drop(child);
            directory.remove_dir("native-dir").unwrap();
            std::fs::create_dir_all("recursive/child").unwrap();
            std::fs::write("recursive/child/file", b"cleanup").unwrap();
            directory.remove_dir_all("recursive").unwrap();
            assert_eq!(
                std::fs::read(resolved.join("allowed.txt")).unwrap(),
                b"allowed"
            );
            assert_eq!(
                std::fs::write(&outside, b"forbidden").unwrap_err().kind(),
                io::ErrorKind::PermissionDenied
            );
        } else {
            assert_eq!(
                std::fs::read(&outside).unwrap_err().kind(),
                io::ErrorKind::PermissionDenied
            );
        }
        for address in addresses {
            let result =
                std::net::TcpStream::connect_timeout(&address, std::time::Duration::from_secs(2));
            assert_eq!(result.is_ok(), inherit, "loopback {address}: {result:?}");
        }
    }

    #[test]
    #[ignore = "requires native MXC/PSEC execution on the Windows host"]
    fn native_mxc_probe_network() {
        use std::net::{TcpListener, TcpStream};
        let mut listeners = vec![TcpListener::bind("127.0.0.1:0").unwrap()];
        if let Ok(listener) = TcpListener::bind("[::1]:0") {
            listeners.push(listener);
        }
        let addresses: Vec<_> = listeners
            .iter()
            .map(|listener| listener.local_addr().unwrap())
            .collect();
        for address in &addresses {
            TcpStream::connect_timeout(address, std::time::Duration::from_secs(2)).unwrap();
            listeners
                .iter()
                .find(|listener| listener.local_addr().unwrap() == *address)
                .unwrap()
                .accept()
                .unwrap();
        }
        let state = super::super::StateDirectory::new(None).unwrap();
        let work = state.0.join("work");
        std::fs::create_dir(&work).unwrap();
        let outside = state.0.join("outside.txt");
        std::fs::write(&outside, b"host-only sentinel").unwrap();
        let executable = std::env::current_exe().unwrap();
        let mut read = platform_roots().unwrap();
        let system_root = unicode(&read[0]).unwrap();
        read.push(executable.clone());
        for (egress, broad_read) in [
            (Egress::Inherit {}, false),
            (Egress::Deny {}, false),
            (Egress::Inherit {}, true),
        ] {
            probe(egress).unwrap();
            let mut read = read.clone();
            if broad_read {
                read.extend(full_disk_read_roots().unwrap());
            }
            let request = ExecutionRequest {
                script_code: cmdline_from_argv_for_context(
                    &[
                        unicode(&executable).unwrap(),
                        "--exact".into(),
                        "execution::windows::tests::mxc_network_child".into(),
                        "--nocapture".into(),
                    ],
                    CommandLineContext::WindowsCreateProcess,
                )
                .unwrap(),
                working_directory: unicode(&work).unwrap(),
                inherit_default_env: false,
                env: Some(vec![
                    format!("SystemRoot={system_root}"),
                    format!("LOCALAPPDATA={}", unicode(&work).unwrap()),
                    format!("TEMP={}", unicode(&work).unwrap()),
                    format!("TMP={}", unicode(&work).unwrap()),
                    format!(
                        "A13N_ENVD_MXC_NETWORK_TEST={}",
                        serde_json::to_string(&(
                            egress == (Egress::Inherit {}),
                            &addresses,
                            &outside,
                            broad_read
                        ))
                        .unwrap()
                    ),
                ]),
                policy: ContainerPolicy {
                    readonly_paths: read.iter().map(|path| unicode(path).unwrap()).collect(),
                    readwrite_paths: vec![unicode(&work).unwrap()],
                    ..base_policy(egress).unwrap()
                },
                ..Default::default()
            };
            let mut logger = Logger::new(Mode::Buffer);
            let mut child = BaseContainerRunner::new()
                .spawn(&request, &mut logger, StdioMode::Inherit)
                .unwrap_or_else(|error| panic!("native MXC launch: {}", error.error_message));
            assert_eq!(child.wait().unwrap(), 0, "native network policy {egress:?}");
            // A mistyped test filter must not turn a zero-test child into a pass.
            assert_eq!(std::fs::read(work.join("allowed.txt")).unwrap(), b"allowed");
            std::fs::remove_file(work.join("allowed.txt")).unwrap();
        }
    }

    #[test]
    fn directory_handles_prevent_replacement_and_detect_new_objects() {
        let state = super::super::StateDirectory::new(None).unwrap();
        let granted = state.0.join("granted");
        std::fs::create_dir(&granted).unwrap();
        let file = open_directory(&granted).unwrap();
        let original = directory_identity(&file).unwrap();
        let moved = state.0.join("moved");
        assert!(std::fs::rename(&granted, &moved).is_err());
        drop(file);
        std::fs::rename(&granted, &moved).unwrap();
        std::fs::create_dir(&granted).unwrap();
        assert_ne!(
            original,
            directory_identity(&open_directory(&granted).unwrap()).unwrap()
        );
    }
}
