//! Local supervisor administration, separate from EIP and execution Sessions.

use std::{
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io,
    net::SocketAddr,
    path::{Path, PathBuf},
    process::Stdio,
    time::Duration,
};

use fs2::FileExt;
use serde::{Deserialize, Serialize};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    process::Command,
    time::timeout,
};

pub(crate) const RECORD: &str = ".a13n-envd-supervisor.json";
pub(crate) const LOCK: &str = ".a13n-envd-supervisor.lock";
const LIMIT: u64 = 4096;
const HELP: &str = "a13n-envd restart [--runtime-dir ABSOLUTE_PATH] [--version X.Y.Z[-rc.N]]

Ask the native supervisor to drain and replace its daemon using the installed binary.
--runtime-dir defaults to A13N_ENVD_RUNTIME_DIR. The same account must own the runtime.
--version restarts only when the running version differs, and verifies the installed
binary before stopping the old daemon. Retrying the same version is idempotent.
Success confirms process launch; the Host must still verify authenticated EIP readiness.
This command never downloads a release or restarts an external service manager.";

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    address: SocketAddr,
    token: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Request {
    token: String,
    pub(crate) version: Option<String>,
}

pub(crate) struct Control {
    listener: TcpListener,
    record: Record,
    path: PathBuf,
    _lock: File,
}

impl Control {
    pub(crate) async fn bind() -> io::Result<Self> {
        let root = runtime_directory(None)?;
        fs::create_dir_all(&root)?;
        let metadata = fs::symlink_metadata(&root)?;
        if metadata.is_symlink() || !metadata.is_dir() {
            return Err(io::Error::other(
                "supervisor runtime must be a real directory",
            ));
        }
        crate::runtime::protect_directory(&root).map_err(io::Error::other)?;
        #[cfg(windows)]
        protect_windows_path(&root)?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(root.join(LOCK))?;
        lock.try_lock_exclusive()?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let mut random = [0u8; 32];
        getrandom::fill(&mut random).map_err(|error| io::Error::other(error.to_string()))?;
        let record = Record {
            address: listener.local_addr()?,
            token: random.iter().map(|byte| format!("{byte:02x}")).collect(),
        };
        let path = root.join(RECORD);
        let mut stage = tempfile::NamedTempFile::new_in(&root)?;
        #[cfg(windows)]
        protect_windows_path(stage.path())?;
        serde_json::to_writer(stage.as_file_mut(), &record)?;
        stage.persist(&path).map_err(|error| error.error)?;
        Ok(Self {
            listener,
            record,
            path,
            _lock: lock,
        })
    }

    pub(crate) async fn receive(&self) -> io::Result<(TcpStream, Request)> {
        loop {
            let (mut stream, _) = self.listener.accept().await?;
            let mut bytes = Vec::new();
            let read = timeout(
                Duration::from_secs(2),
                (&mut stream).take(LIMIT + 1).read_to_end(&mut bytes),
            )
            .await;
            if !matches!(read, Ok(Ok(_))) || bytes.len() as u64 > LIMIT {
                continue;
            }
            if let Ok(request) = serde_json::from_slice::<Request>(&bytes)
                && request.token == self.record.token
            {
                return Ok((stream, request));
            }
        }
    }
}

impl Drop for Control {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

pub(crate) async fn reply(stream: &mut TcpStream, error: Option<&str>) -> io::Result<()> {
    stream.write_all(error.unwrap_or("ok").as_bytes()).await?;
    stream.shutdown().await
}

pub(crate) async fn binary_version(executable: &Path) -> io::Result<String> {
    let mut child = Command::new(executable)
        .arg("--version")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()?;
    let mut output = child
        .stdout
        .take()
        .ok_or_else(|| io::Error::other("version output unavailable"))?;
    let probe = async {
        let mut bytes = Vec::new();
        (&mut output).take(1025).read_to_end(&mut bytes).await?;
        if bytes.len() > 1024 || !child.wait().await?.success() {
            return Err(io::Error::other(
                "installed executable version probe failed",
            ));
        }
        let text = std::str::from_utf8(&bytes).map_err(io::Error::other)?;
        let version = text
            .strip_prefix("a13n-envd ")
            .ok_or_else(|| io::Error::other("invalid executable version"))?
            .trim();
        semver::Version::parse(version).map_err(io::Error::other)?;
        Ok(version.to_owned())
    };
    timeout(Duration::from_secs(5), probe)
        .await
        .map_err(|_| io::Error::other("version probe timed out"))?
}

#[cfg(windows)]
fn protect_windows_path(path: &Path) -> io::Result<()> {
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::{
            Authorization::{
                ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1,
            },
            DACL_SECURITY_INFORMATION, PROTECTED_DACL_SECURITY_INFORMATION, SetFileSecurityW,
        },
    };
    // OWNER RIGHTS applies to this object's owner. A protected DACL removes
    // inherited grants; child objects inherit owner-only access as well.
    let sddl: Vec<u16> = "D:P(A;OICI;FA;;;OW)\0".encode_utf16().collect();
    let path: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut descriptor = ptr::null_mut();
    unsafe {
        if ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            ptr::null_mut(),
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        let result = SetFileSecurityW(
            path.as_ptr(),
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            descriptor,
        );
        let error = io::Error::last_os_error();
        LocalFree(descriptor);
        if result == 0 {
            return Err(error);
        }
    }
    Ok(())
}

fn runtime_directory(argument: Option<OsString>) -> io::Result<PathBuf> {
    let path = PathBuf::from(
        argument
            .or_else(|| std::env::var_os("A13N_ENVD_RUNTIME_DIR"))
            .ok_or_else(|| {
                io::Error::other("--runtime-dir or A13N_ENVD_RUNTIME_DIR is required")
            })?,
    );
    if !path.is_absolute() {
        return Err(io::Error::other("runtime directory must be absolute"));
    }
    Ok(path)
}

pub(crate) fn restart(
    arguments: &[OsString],
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if arguments == [OsString::from("--help")] {
        println!("{HELP}");
        return Ok(());
    }
    let mut arguments = arguments.iter();
    let mut directory = None;
    let mut version = None;
    while let Some(argument) = arguments.next() {
        match argument.to_str() {
            Some("--runtime-dir") if directory.is_none() => {
                directory = Some(
                    arguments
                        .next()
                        .ok_or("--runtime-dir requires a path")?
                        .clone(),
                )
            }
            Some("--version") if version.is_none() => {
                let value = arguments
                    .next()
                    .and_then(|value| value.to_str())
                    .ok_or("--version requires a version")?;
                semver::Version::parse(value)?;
                version = Some(value.to_owned());
            }
            _ => return Err("invalid restart argument; use restart --help".into()),
        }
    }
    let path = runtime_directory(directory)?.join(RECORD);
    let bytes = fs::read(path)?;
    if bytes.len() as u64 > LIMIT {
        return Err("supervisor record exceeds limit".into());
    }
    let record: Record = serde_json::from_slice(&bytes)?;
    if !record.address.ip().is_loopback() {
        return Err("supervisor endpoint must be loopback".into());
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()?;
    runtime.block_on(async {
        timeout(Duration::from_secs(15), async {
            let mut stream = TcpStream::connect(record.address).await?;
            stream
                .write_all(&serde_json::to_vec(&Request {
                    token: record.token,
                    version,
                })?)
                .await?;
            stream.shutdown().await?;
            let mut bytes = Vec::new();
            (&mut stream)
                .take(LIMIT + 1)
                .read_to_end(&mut bytes)
                .await?;
            if bytes != b"ok" {
                return Err(io::Error::other("supervisor restart was not confirmed"));
            }
            Ok::<_, io::Error>(())
        })
        .await
        .map_err(|_| {
            io::Error::other("restart outcome unknown: supervisor did not confirm before deadline")
        })?
    })?;
    println!("Daemon launch confirmed; verify authenticated EIP readiness.");
    Ok(())
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    #[test]
    fn control_record_has_a_protected_owner_only_windows_dacl() {
        use std::{os::windows::ffi::OsStrExt, ptr};
        use windows_sys::Win32::{
            Foundation::LocalFree,
            Security::{
                Authorization::{
                    ConvertSecurityDescriptorToStringSecurityDescriptorW, SDDL_REVISION_1,
                },
                DACL_SECURITY_INFORMATION, GetFileSecurityW,
            },
        };
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("record");
        fs::write(&path, b"").unwrap();
        protect_windows_path(&path).unwrap();
        let path: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut size = 0;
        unsafe {
            GetFileSecurityW(
                path.as_ptr(),
                DACL_SECURITY_INFORMATION,
                ptr::null_mut(),
                0,
                &mut size,
            );
            let mut descriptor = vec![0u8; size as usize];
            assert_ne!(
                GetFileSecurityW(
                    path.as_ptr(),
                    DACL_SECURITY_INFORMATION,
                    descriptor.as_mut_ptr().cast(),
                    size,
                    &mut size
                ),
                0
            );
            let mut text = ptr::null_mut();
            let mut length = 0;
            assert_ne!(
                ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    descriptor.as_mut_ptr().cast(),
                    SDDL_REVISION_1,
                    DACL_SECURITY_INFORMATION,
                    &mut text,
                    &mut length
                ),
                0
            );
            let sddl =
                String::from_utf16_lossy(std::slice::from_raw_parts(text, length as usize - 1));
            LocalFree(text.cast());
            assert_eq!(sddl, "D:P(A;OICI;FA;;;OW)");
        }
    }
}
