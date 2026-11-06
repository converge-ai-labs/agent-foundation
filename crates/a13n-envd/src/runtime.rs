use std::{
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io,
    path::{Path, PathBuf},
    sync::Arc,
};

#[cfg(windows)]
use crate::windows_directory::Dir;
use cap_std::ambient_authority;
#[cfg(not(windows))]
use cap_std::fs::Dir;
use fs2::FileExt as _;

#[derive(Debug, Clone)]
pub(crate) struct RuntimeState {
    inner: Arc<RuntimeInner>,
}

#[derive(Debug)]
struct RuntimeInner {
    parent_dir: Dir,
    generation_name: OsString,
    _generation: PathBuf,
    spool: PathBuf,
    _lock: File,
}

impl RuntimeState {
    pub(crate) fn prepare(parent: &Path) -> Result<Self, String> {
        fs::create_dir_all(parent)
            .map_err(|error| format!("cannot create runtime parent: {error}"))?;
        let metadata = fs::symlink_metadata(parent)
            .map_err(|error| format!("cannot inspect runtime parent: {error}"))?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            return Err("runtime parent must be a directory, not a symlink".to_owned());
        }
        protect_directory(parent)?;
        let parent = crate::device_path::canonicalize(parent)
            .map_err(|error| format!("cannot canonicalize runtime parent: {error}"))?;
        let lock_path = parent.join(".a13n-envd.lock");
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&lock_path)
            .map_err(|error| format!("cannot open runtime-parent lock: {error}"))?;
        lock.try_lock_exclusive()
            .map_err(|error| format!("runtime parent is already owned: {error}"))?;

        let parent_dir = Dir::open_ambient_dir(&parent, ambient_authority())
            .map_err(|error| format!("cannot open runtime-parent capability: {error}"))?;
        for entry in parent_dir
            .entries()
            .map_err(|error| format!("cannot enumerate runtime parent: {error}"))?
        {
            let entry = entry.map_err(|error| format!("cannot inspect runtime entry: {error}"))?;
            let name = entry.file_name();
            if name == ".a13n-envd.lock" {
                continue;
            }
            let Some(display_name) = name.to_str() else {
                return Err("runtime parent contains a non-UTF-8 entry".to_owned());
            };
            if !display_name.starts_with("generation-") {
                return Err(format!(
                    "runtime parent contains unexpected entry: {display_name}"
                ));
            }
            let metadata = parent_dir
                .symlink_metadata(&name)
                .map_err(|error| format!("cannot inspect stale runtime generation: {error}"))?;
            if metadata.is_symlink() || !metadata.is_dir() {
                return Err(format!(
                    "stale runtime generation is not a real directory: {display_name}"
                ));
            }
            #[cfg(all(
                target_os = "linux",
                any(feature = "vm-boxlite", feature = "vm-microsandbox")
            ))]
            crate::execution::vm::reclaim_generation(&parent.join(&name))
                .map_err(|error| format!("cannot reclaim stale VM generation: {error}"))?;
            #[cfg(not(all(
                target_os = "linux",
                any(feature = "vm-boxlite", feature = "vm-microsandbox")
            )))]
            if parent.join(&name).join("vms").exists() {
                return Err(
                    "stale VM state requires a VM-enabled Envd build for reclamation".into(),
                );
            }
            parent_dir.remove_dir_all(&name).map_err(|error| {
                format!("cannot clean stale runtime generation {display_name}: {error}")
            })?;
        }

        let (generation_name, generation) = loop {
            let mut random = [0_u8; 16];
            getrandom::fill(&mut random)
                .map_err(|_| "secure runtime generation failed".to_owned())?;
            let name = OsString::from(format!("generation-{}", hex(&random)));
            let candidate = parent.join(&name);
            match parent_dir.create_dir(&name) {
                Ok(()) => break (name, candidate),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(format!("cannot create runtime generation: {error}")),
            }
        };
        protect_directory(&generation)?;
        let spool = create_private_child(&generation, "spool")?;
        Ok(Self {
            inner: Arc::new(RuntimeInner {
                parent_dir,
                generation_name,
                _generation: generation,
                spool,
                _lock: lock,
            }),
        })
    }

    #[cfg(any(target_os = "linux", test))]
    pub(crate) fn generation(&self) -> &Path {
        &self.inner._generation
    }

    pub(crate) fn spool(&self) -> &Path {
        &self.inner.spool
    }
}

impl Drop for RuntimeInner {
    fn drop(&mut self) {
        // VM journals outlive cancelled owner tasks and failed SDK cleanup.
        // The next owner reclaims them under the same runtime-parent lock.
        let vms = self._generation.join("vms");
        let vm_state_absent = match fs::read_dir(vms) {
            Ok(mut entries) => entries.next().is_none(),
            Err(error) => error.kind() == io::ErrorKind::NotFound,
        };
        if vm_state_absent {
            let _ = self.parent_dir.remove_dir_all(&self.generation_name);
        }
        let _ = self._lock.unlock();
    }
}

/// Persistent installation state is separate from generation-private runtime data.
pub(crate) fn installation_identity(directory: &Path) -> Result<String, String> {
    use std::io::{Read, Write};

    if !directory.is_absolute() {
        return Err("installation state directory must be absolute".to_owned());
    }
    fs::create_dir_all(directory)
        .map_err(|error| format!("cannot create installation state: {error}"))?;
    protect_directory(directory)?;
    let path = directory.join("device-id");
    let existing = match fs::File::open(&path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let mut random = [0_u8; 16];
            getrandom::fill(&mut random)
                .map_err(|_| "cannot generate Device identity".to_owned())?;
            let identity = format!("device-{}", hex(&random));
            let mut temporary = tempfile::NamedTempFile::new_in(directory)
                .map_err(|error| format!("cannot create Device identity: {error}"))?;
            temporary
                .write_all(identity.as_bytes())
                .and_then(|()| temporary.as_file().sync_all())
                .map_err(|error| format!("cannot persist Device identity: {error}"))?;
            // Publish complete bytes once. Concurrent first starts read the winner.
            match temporary.persist_noclobber(&path) {
                Ok(_) => return Ok(identity),
                Err(error) if error.error.kind() == io::ErrorKind::AlreadyExists => {
                    fs::File::open(&path)
                        .map_err(|error| format!("cannot open Device identity: {error}"))?
                }
                Err(error) => return Err(format!("cannot publish Device identity: {error}")),
            }
        }
        Err(error) => return Err(format!("cannot open Device identity: {error}")),
    };
    let mut identity = String::new();
    existing
        .take(257)
        .read_to_string(&mut identity)
        .map_err(|error| format!("cannot read Device identity: {error}"))?;
    crate::config::validate_identity(&identity, "device_id").map_err(|error| error.to_string())?;
    Ok(identity)
}

fn create_private_child(parent: &Path, name: &str) -> Result<PathBuf, String> {
    let path = parent.join(name);
    fs::create_dir(&path).map_err(|error| format!("cannot create runtime {name}: {error}"))?;
    protect_directory(&path)?;
    Ok(path)
}

pub(crate) fn protect_directory(path: &Path) -> Result<(), String> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;
        fs::set_permissions(path, fs::Permissions::from_mode(0o700))
            .map_err(|error| format!("cannot protect runtime directory: {error}"))?;
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut value = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        value.push(DIGITS[(byte >> 4) as usize] as char);
        value.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    value
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::RuntimeState;

    #[test]
    fn installation_identity_survives_restarts_and_refuses_corrupt_state() {
        let state = tempfile::tempdir().unwrap();
        let path = state.path().join("device-id");
        let read = || super::installation_identity(state.path());
        let first = std::thread::scope(|scope| {
            let readers: Vec<_> = (0..8).map(|_| scope.spawn(read)).collect();
            let ids: Vec<_> = readers
                .into_iter()
                .map(|task| task.join().unwrap().unwrap())
                .collect();
            assert!(ids.iter().all(|id| id == &ids[0]));
            ids[0].clone()
        });
        assert_eq!(read().unwrap(), first);
        for invalid in [b"".as_slice(), b"truncated\n", &[0xff]] {
            fs::write(&path, invalid).unwrap();
            assert!(read().is_err());
            assert_eq!(fs::read(&path).unwrap(), invalid);
        }
        fs::remove_file(path).unwrap();
        assert_ne!(read().unwrap(), first);
    }

    #[test]
    fn runtime_drop_preserves_pending_vm_recovery_records() {
        let parent = tempfile::tempdir().unwrap();
        let runtime = RuntimeState::prepare(parent.path()).unwrap();
        let generation = runtime.generation().to_path_buf();
        let session = generation.join("vms/session-test");
        fs::create_dir_all(&session).unwrap();
        fs::write(session.join("backend.json"), b"incomplete").unwrap();
        drop(runtime);
        assert!(session.join("backend.json").exists());
        assert!(RuntimeState::prepare(parent.path()).is_err());
        assert!(session.join("backend.json").exists());
    }

    #[test]
    fn runtime_drop_removes_generation_after_vm_reclamation() {
        let parent = tempfile::tempdir().unwrap();
        let runtime = RuntimeState::prepare(parent.path()).unwrap();
        let generation = runtime.generation().to_path_buf();
        fs::create_dir(generation.join("vms")).unwrap();
        drop(runtime);
        assert!(!generation.exists());
    }

    #[test]
    fn startup_removes_stale_generation_and_creates_private_layout() {
        let parent =
            std::env::temp_dir().join(format!("a13n-envd-runtime-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&parent);
        fs::create_dir_all(parent.join("generation-stale/spool")).expect("stale tree");
        fs::write(parent.join("generation-stale/spool/output"), b"bytes").expect("stale bytes");
        let runtime = RuntimeState::prepare(&parent).expect("runtime");
        assert!(runtime.spool().is_dir());
        assert_eq!(fs::read_dir(runtime.generation()).unwrap().count(), 1);
        assert!(!parent.join("generation-stale").exists());
        drop(runtime);
        fs::remove_file(parent.join(".a13n-envd.lock")).expect("lock file");
        fs::remove_dir(parent).expect("parent");
    }
}
