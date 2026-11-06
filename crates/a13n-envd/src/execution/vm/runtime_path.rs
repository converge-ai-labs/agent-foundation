//! Short private SDK home aliases keep Unix socket names below sun_path limits.
//! The actual storage stays in the journaled Device generation directory.
use sha2::{Digest, Sha256};
use std::{
    fs, io,
    os::unix::{
        ffi::OsStrExt,
        fs::{DirBuilderExt, MetadataExt, symlink},
    },
    path::{Path, PathBuf},
};

pub(super) struct RuntimePath(PathBuf);

impl RuntimePath {
    pub fn new(home: &Path) -> io::Result<Self> {
        fs::create_dir_all(home)?;
        let home = fs::canonicalize(home)?;
        let uid = unsafe { libc::geteuid() };
        // Intentionally independent of TMPDIR: SDK sockets need a short prefix.
        let parent = PathBuf::from(format!("/tmp/a13n-vm-{uid}"));
        match fs::DirBuilder::new().mode(0o700).create(&parent) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error),
        }
        let metadata = fs::symlink_metadata(&parent)?;
        if !metadata.is_dir() || metadata.uid() != uid || metadata.mode() & 0o777 != 0o700 {
            return Err(io::Error::other(
                "VM socket directory must be owned by the daemon with mode 0700",
            ));
        }
        let digest = Sha256::digest(home.as_os_str().as_bytes());
        let name: String = digest[..16]
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let path = parent.join(name);
        match symlink(&home, &path) {
            Ok(()) => {}
            Err(error)
                if error.kind() == io::ErrorKind::AlreadyExists
                    && fs::read_link(&path)? == home => {}
            Err(error) => return Err(error),
        }
        Ok(Self(path))
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for RuntimePath {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alias_reopens_after_crash_and_removes_only_the_link() {
        let root = tempfile::tempdir().unwrap();
        let home = root.path().join("long-generation-directory".repeat(8));
        let alias = RuntimePath::new(&home).unwrap();
        let path = alias.path().to_owned();
        fs::write(path.join("kept"), b"data").unwrap();
        std::mem::forget(alias); // Simulate a daemon crash without Drop.
        let reopened = RuntimePath::new(&home).unwrap();
        assert_eq!(reopened.path(), path);
        assert!(path.as_os_str().len() < 64);
        drop(reopened);
        assert!(fs::symlink_metadata(path).is_err());
        assert_eq!(fs::read(home.join("kept")).unwrap(), b"data");
    }
}
