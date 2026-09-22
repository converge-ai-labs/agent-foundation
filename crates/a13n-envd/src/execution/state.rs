//! Private worker state, removed only after its owning child has been reaped.
use std::{fs, io, os::unix::fs::DirBuilderExt, path::PathBuf};

pub(crate) struct StateDirectory(pub PathBuf);

impl StateDirectory {
    pub fn new(identity: Option<super::Identity>) -> io::Result<Self> {
        let mut random = [0_u8; 16];
        getrandom::fill(&mut random).map_err(|error| io::Error::other(error.to_string()))?;
        let path =
            std::env::temp_dir().join(format!("a13n-session-{:032x}", u128::from_ne_bytes(random)));
        fs::DirBuilder::new().mode(0o700).create(&path)?;
        let mut directory = Self(path);
        directory.0 = fs::canonicalize(&directory.0)?;
        if let Some(identity) = identity {
            use std::os::fd::AsRawFd;
            let file = fs::File::open(&directory.0)?;
            if unsafe { libc::fchown(file.as_raw_fd(), identity.uid, identity.gid) } != 0 {
                return Err(io::Error::last_os_error());
            }
        }
        Ok(directory)
    }
}

impl Drop for StateDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
