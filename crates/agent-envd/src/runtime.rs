use std::{
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io,
    path::{Path, PathBuf},
    sync::Arc,
};

use cap_std::{ambient_authority, fs::Dir};
use fs2::FileExt as _;

#[derive(Debug, Clone)]
pub(crate) struct RuntimeState {
    inner: Arc<RuntimeInner>,
}

#[derive(Debug)]
struct RuntimeInner {
    parent: PathBuf,
    parent_dir: Dir,
    generation_name: OsString,
    _generation: PathBuf,
    spool: PathBuf,
    _control: PathBuf,
    _probe: PathBuf,
    home: PathBuf,
    temp: PathBuf,
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
        let parent = fs::canonicalize(parent)
            .map_err(|error| format!("cannot canonicalize runtime parent: {error}"))?;
        let lock_path = parent.join(".agent-envd.lock");
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
            if name == ".agent-envd.lock" {
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
        let control = create_private_child(&generation, "control")?;
        let probe = create_private_child(&generation, "probe")?;
        let home = create_private_child(&generation, "home")?;
        let temp = create_private_child(&generation, "temp")?;
        Ok(Self {
            inner: Arc::new(RuntimeInner {
                parent,
                parent_dir,
                generation_name,
                _generation: generation,
                spool,
                _control: control,
                _probe: probe,
                home,
                temp,
                _lock: lock,
            }),
        })
    }

    pub(crate) fn parent(&self) -> &Path {
        &self.inner.parent
    }

    #[cfg(test)]
    pub(crate) fn generation(&self) -> &Path {
        &self.inner._generation
    }

    pub(crate) fn spool(&self) -> &Path {
        &self.inner.spool
    }

    pub(crate) fn home(&self) -> &Path {
        &self.inner.home
    }

    pub(crate) fn temp(&self) -> &Path {
        &self.inner.temp
    }
}

impl Drop for RuntimeInner {
    fn drop(&mut self) {
        let _ = self.parent_dir.remove_dir_all(&self.generation_name);
        let _ = self._lock.unlock();
    }
}

fn create_private_child(parent: &Path, name: &str) -> Result<PathBuf, String> {
    let path = parent.join(name);
    fs::create_dir(&path).map_err(|error| format!("cannot create runtime {name}: {error}"))?;
    protect_directory(&path)?;
    Ok(path)
}

fn protect_directory(path: &Path) -> Result<(), String> {
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
    fn startup_removes_stale_generation_and_creates_private_layout() {
        let parent =
            std::env::temp_dir().join(format!("agent-envd-runtime-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&parent);
        fs::create_dir_all(parent.join("generation-stale/spool")).expect("stale tree");
        fs::write(parent.join("generation-stale/spool/output"), b"bytes").expect("stale bytes");
        let runtime = RuntimeState::prepare(&parent).expect("runtime");
        assert!(runtime.spool().is_dir());
        assert!(runtime.generation().join("control").is_dir());
        assert!(runtime.generation().join("probe").is_dir());
        assert!(runtime.home().is_dir());
        assert!(runtime.temp().is_dir());
        assert!(!parent.join("generation-stale").exists());
        drop(runtime);
        fs::remove_file(parent.join(".agent-envd.lock")).expect("lock file");
        fs::remove_dir(parent).expect("parent");
    }
}
