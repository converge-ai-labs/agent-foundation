//! Resolve opened local files without querying the Mount Manager from MXC.
//!
//! Like Codex's native filesystem helpers, use NT names from verified handles.
//! EIP still exposes DOS paths; match the opened object's volume against opened
//! drive roots instead of guessing a drive letter from the input spelling.
use std::{
    ffi::OsString,
    fs::{File, OpenOptions},
    io,
    os::windows::{ffi::OsStringExt, fs::OpenOptionsExt, io::AsRawHandle},
    path::{Path, PathBuf},
};
use windows_sys::Win32::Storage::FileSystem::{
    FILE_FLAG_BACKUP_SEMANTICS, FILE_READ_ATTRIBUTES, FILE_SHARE_DELETE, FILE_SHARE_READ,
    FILE_SHARE_WRITE, GetFinalPathNameByHandleW, GetLogicalDrives, VOLUME_NAME_NT,
};

pub(crate) fn canonicalize(path: impl AsRef<Path>) -> io::Result<PathBuf> {
    let path = path.as_ref();
    match std::fs::canonicalize(path) {
        Ok(path) => Ok(path),
        Err(error) if error.kind() == io::ErrorKind::PermissionDenied => {
            // Native PSEC can allow opening a file but deny DOS-volume lookup.
            // Opening and querying both handles still uses this process's policy.
            let file = open_attributes(path)?;
            canonicalize_handle(&file)
        }
        Err(error) => Err(error),
    }
}

fn open_attributes(path: &Path) -> io::Result<File> {
    OpenOptions::new()
        .access_mode(FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)
}

pub(crate) fn nt_name(file: &impl AsRawHandle) -> io::Result<PathBuf> {
    let mut name = vec![0_u16; 512];
    loop {
        let length = unsafe {
            GetFinalPathNameByHandleW(
                file.as_raw_handle(),
                name.as_mut_ptr(),
                name.len() as u32,
                VOLUME_NAME_NT,
            )
        } as usize;
        if length == 0 {
            return Err(io::Error::last_os_error());
        }
        if length < name.len() {
            return Ok(PathBuf::from(OsString::from_wide(&name[..length])));
        }
        name.resize(length + 1, 0);
    }
}

fn canonicalize_handle(file: &File) -> io::Result<PathBuf> {
    let name = nt_name(file)?;
    let drives = unsafe { GetLogicalDrives() };
    if drives == 0 {
        return Err(io::Error::last_os_error());
    }
    for index in 0..26 {
        if drives & (1 << index) == 0 {
            continue;
        }
        let letter = char::from(b'A' + index as u8);
        let root = PathBuf::from(format!(r"{letter}:\"));
        let Ok(root_handle) = open_attributes(&root) else {
            continue;
        };
        let Ok(root_name) = nt_name(&root_handle) else {
            continue;
        };
        if let Some(path) = map_volume(&name, &root_name, letter) {
            return Ok(path);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::PermissionDenied,
        "opened file has no accessible DOS volume mapping",
    ))
}

fn map_volume(name: &Path, root: &Path, letter: char) -> Option<PathBuf> {
    // Component matching prevents Volume3 from matching Volume30. The names
    // are normalized by the kernel, not supplied by an untrusted caller.
    let suffix = name.strip_prefix(root).ok()?;
    Some(PathBuf::from(format!(r"\\?\{letter}:\")).join(suffix))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn volume_mapping_preserves_object_path_and_rejects_prefix_collisions() {
        let root = Path::new(r"\Device\HarddiskVolume3\");
        assert_eq!(
            map_volume(Path::new(r"\Device\HarddiskVolume3\work\file"), root, 'C'),
            Some(PathBuf::from(r"\\?\C:\work\file"))
        );
        assert_eq!(map_volume(root, root, 'C'), Some(PathBuf::from(r"\\?\C:\")));
        assert!(map_volume(Path::new(r"\Device\HarddiskVolume30\work"), root, 'C').is_none());
    }

    #[test]
    fn opened_handle_resolves_to_the_same_file_as_standard_canonicalization() {
        let root =
            std::env::temp_dir().join(crate::operation::random_selector("nt-path-test").unwrap());
        std::fs::create_dir(&root).unwrap();
        let path = root.join("file");
        std::fs::write(&path, b"resolved").unwrap();
        let resolved = canonicalize_handle(&open_attributes(&path).unwrap()).unwrap();
        assert_eq!(resolved, std::fs::canonicalize(&path).unwrap());
        assert_eq!(std::fs::read(&resolved).unwrap(), b"resolved");
        std::fs::remove_dir_all(root).unwrap();
    }
}
