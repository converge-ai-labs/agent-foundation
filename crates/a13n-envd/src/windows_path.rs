//! Resolve opened local files without querying the Mount Manager from MXC.
//!
//! Like Codex's native filesystem helpers, use NT names from verified handles.
//! EIP exposes DOS paths; translate verified NT paths with the DOS device map
//! without opening every drive root.
use std::{
    borrow::Cow,
    ffi::OsString,
    fs::{File, OpenOptions},
    io,
    os::windows::{ffi::OsStringExt, fs::OpenOptionsExt, io::AsRawHandle},
    path::{Path, PathBuf},
};
use windows_sys::Win32::Storage::FileSystem::{
    FILE_FLAG_BACKUP_SEMANTICS, FILE_READ_ATTRIBUTES, FILE_SHARE_DELETE, FILE_SHARE_READ,
    FILE_SHARE_WRITE, GetFinalPathNameByHandleW, GetLogicalDrives, QueryDosDeviceW, VOLUME_NAME_NT,
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

pub(crate) fn open_attributes(path: &Path) -> io::Result<File> {
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

pub(crate) fn canonicalize_handle(file: &File) -> io::Result<PathBuf> {
    let name = nt_name(file)?;
    if let Some(unc) = map_unc(&name) {
        return Ok(unc);
    }
    let drives = unsafe { GetLogicalDrives() };
    if drives == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut indirect_drives = Vec::new();
    for index in 0..26 {
        if drives & (1 << index) == 0 {
            continue;
        }
        let letter = char::from(b'A' + index as u8);
        let Some(root_name) = dos_device(letter) else {
            continue;
        };
        if let Some(path) = map_volume(&name, &root_name, letter) {
            return Ok(path);
        }
        if root_name.starts_with(r"\??") {
            indirect_drives.push(letter);
        }
    }
    // SUBST and other indirect mappings point to DOS paths, not NT object names.
    for letter in indirect_drives {
        let root = PathBuf::from(format!(r"{letter}:\"));
        if let Ok(handle) = open_attributes(&root)
            && let Ok(root_name) = nt_name(&handle)
            && let Some(path) = map_volume(&name, &root_name, letter)
        {
            return Ok(path);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::PermissionDenied,
        "opened file has no accessible DOS volume mapping",
    ))
}

fn dos_device(letter: char) -> Option<PathBuf> {
    use windows_sys::Win32::Foundation::ERROR_INSUFFICIENT_BUFFER;

    let drive = [letter as u16, u16::from(b':'), 0];
    let mut buffer = vec![0_u16; 512];
    loop {
        let written =
            unsafe { QueryDosDeviceW(drive.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32) }
                as usize;
        if written != 0 {
            // QueryDosDevice returns a multistring. Only the first mapping is current.
            let current = buffer[..written].split(|unit| *unit == 0).next()?;
            return Some(PathBuf::from(OsString::from_wide(current)));
        }
        if io::Error::last_os_error().raw_os_error() != Some(ERROR_INSUFFICIENT_BUFFER as i32) {
            return None;
        }
        buffer.resize(buffer.len() * 2, 0);
    }
}

fn map_unc(name: &Path) -> Option<PathBuf> {
    let suffix = name.strip_prefix(r"\Device\Mup").ok()?;
    (suffix.components().count() >= 2).then(|| {
        let mut unc = OsString::from(r"\\?\UNC\");
        unc.push(suffix);
        unc.into()
    })
}

/// Use ordinary paths when passing canonicalized paths to Win32 process consumers.
pub(crate) fn ordinary_path(path: &Path) -> Cow<'_, Path> {
    match path.to_str().map(ordinary_path_text) {
        Some(Cow::Borrowed(value)) => Cow::Borrowed(Path::new(value)),
        Some(Cow::Owned(value)) => Cow::Owned(value.into()),
        None => Cow::Borrowed(path),
    }
}

fn ordinary_path_text(path: &str) -> Cow<'_, str> {
    let Some(tail) = path.strip_prefix(r"\\?\") else {
        return Cow::Borrowed(path);
    };
    if tail
        .get(..4)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case(r"UNC\"))
    {
        return Cow::Owned(format!(r"\\{}", &tail[4..]));
    }
    if matches!(
        tail.as_bytes(),
        [b'A'..=b'Z' | b'a'..=b'z', b':', b'\\', ..]
    ) {
        return Cow::Borrowed(tail);
    }
    Cow::Borrowed(path)
}

pub(crate) fn inherit_system_directories(command: &mut tokio::process::Command) {
    for name in ["SystemRoot", "WINDIR"] {
        if let Some(value) = std::env::var_os(name) {
            command.env(name, ordinary_path(Path::new(&value)).as_os_str());
        }
    }
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
    use std::collections::BTreeMap;

    #[test]
    fn mup_names_keep_the_complete_unc_share() {
        let path = Path::new(r"\Device\Mup\server\share\file");
        assert_eq!(
            map_unc(path),
            Some(PathBuf::from(r"\\?\UNC\server\share\file"))
        );
        assert!(map_unc(Path::new(r"\Device\Mup\server")).is_none());
        assert!(map_unc(Path::new(r"\Device\MupExtra\server\share")).is_none());
    }

    #[test]
    fn process_paths_strip_only_drive_and_unc_verbatim_prefixes() {
        assert_eq!(ordinary_path_text(r"\\?\C:\Windows"), r"C:\Windows");
        assert_eq!(
            ordinary_path_text(r"\\?\UNC\server\share\work"),
            r"\\server\share\work"
        );
        assert_eq!(
            ordinary_path_text(r"\\?\Volume{guid}\work"),
            r"\\?\Volume{guid}\work"
        );
    }

    #[test]
    fn internal_process_inherits_only_windows_system_directories_after_env_clear() {
        let mut command = tokio::process::Command::new("a13n-envd.exe");
        command.env_clear();
        inherit_system_directories(&mut command);
        let environment: BTreeMap<_, _> = command.as_std().get_envs().collect();
        for name in ["SystemRoot", "WINDIR"] {
            if let Some(value) = std::env::var_os(name) {
                assert_eq!(
                    environment.get(std::ffi::OsStr::new(name)),
                    Some(&Some(ordinary_path(Path::new(&value)).as_os_str()))
                );
            }
        }
        assert!(environment.keys().all(|name| {
            ["SystemRoot", "WINDIR"]
                .iter()
                .any(|allowed| name.eq_ignore_ascii_case(allowed))
        }));
    }

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
