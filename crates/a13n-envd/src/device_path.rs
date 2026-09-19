use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

use crate::{
    eip::{DirectoryEntry, DirectoryListResult, PathStyle},
    filesystem::PathError,
};

pub(crate) fn path_style() -> PathStyle {
    if cfg!(windows) {
        PathStyle::Windows
    } else {
        PathStyle::Posix
    }
}

/// Validate Device syntax independently of the requester's operating system.
fn validate(path: &str, windows: bool) -> Result<(), PathError> {
    if !path.starts_with('/') || path.contains('\0') {
        return Err(PathError::Invalid);
    }
    if path == "/" {
        return Ok(());
    }
    let root = windows && (is_drive_root(path) || is_unc_root(path));
    if path.ends_with('/') && !root {
        return Err(PathError::Invalid);
    }
    for segment in path.trim_end_matches('/')[1..].split('/') {
        if segment.is_empty()
            || matches!(segment, "." | "..")
            || (windows && segment.contains('\\'))
        {
            return Err(PathError::Invalid);
        }
    }
    if windows && !is_drive_path(path) && !path.starts_with("/UNC/") {
        return Err(PathError::Invalid);
    }
    if windows && path.starts_with("/UNC/") && path.trim_end_matches('/').split('/').count() < 4 {
        return Err(PathError::Invalid);
    }
    Ok(())
}

fn is_drive_path(path: &str) -> bool {
    let bytes = path.as_bytes();
    bytes.len() >= 4 && bytes[0] == b'/' && bytes[1].is_ascii_alphabetic() && bytes[2..4] == *b":/"
}

fn is_drive_root(path: &str) -> bool {
    path.len() == 4 && is_drive_path(path)
}
fn is_unc_root(path: &str) -> bool {
    path.starts_with("/UNC/") && path.trim_end_matches('/').split('/').count() == 4
}

pub(crate) fn to_native(path: &str) -> Result<PathBuf, PathError> {
    validate(path, cfg!(windows))?;
    #[cfg(windows)]
    {
        if path == "/" {
            return Err(PathError::Unsupported);
        }
        let native = if let Some(share) = path.strip_prefix("/UNC/") {
            format!("\\\\{}", share.replace('/', "\\"))
        } else {
            path[1..].replace('/', "\\")
        };
        Ok(PathBuf::from(native))
    }
    #[cfg(not(windows))]
    {
        Ok(PathBuf::from(path))
    }
}

pub(crate) fn from_native(path: &Path) -> Result<String, PathError> {
    if !path.is_absolute() {
        return Err(PathError::Invalid);
    }
    let text = path.to_str().ok_or(PathError::Invalid)?;
    #[cfg(windows)]
    let result = {
        let text = text.strip_prefix("\\\\?\\").unwrap_or(text);
        let slash = text.replace('\\', "/");
        if let Some(share) = slash
            .strip_prefix("UNC/")
            .or_else(|| slash.strip_prefix("//"))
        {
            format!("/UNC/{share}")
        } else {
            format!("/{slash}")
        }
    };
    #[cfg(not(windows))]
    let result = text.to_owned();
    validate(&result, cfg!(windows))?;
    Ok(result)
}

pub(crate) fn resolve_directory(path: &str) -> Result<PathBuf, PathError> {
    let native = fs::canonicalize(to_native(path)?).map_err(PathError::from_io)?;
    if !fs::metadata(&native).map_err(PathError::from_io)?.is_dir() {
        return Err(PathError::NotRegular);
    }
    Ok(native)
}

fn parent_path(path: &str) -> Option<String> {
    if path == "/" {
        return None;
    }
    if cfg!(windows) && (is_drive_root(path) || is_unc_root(path)) {
        return Some("/".to_owned());
    }
    let (parent, _) = path.rsplit_once('/')?;
    if parent.is_empty() {
        Some("/".to_owned())
    } else if cfg!(windows) && parent.len() == 3 && parent.ends_with(':') {
        Some(format!("{parent}/"))
    } else {
        Some(parent.to_owned())
    }
}

/// One bounded observation. No Session, operation ledger, or recursive traversal.
pub(crate) fn list_directory(
    path: &str,
    offset: u64,
    limit: u64,
    max_bytes: u64,
) -> Result<DirectoryListResult, PathError> {
    const MAX_ENTRIES: u64 = 10_000;
    const MAX_SCAN: usize = 100_000;
    let keep = offset
        .checked_add(limit)
        .filter(|value| *value <= MAX_ENTRIES)
        .ok_or(PathError::Quota)? as usize;
    if limit == 0 {
        return Err(PathError::Invalid);
    }
    #[cfg(windows)]
    if path == "/" {
        return list_volumes(offset, limit);
    }
    let native = resolve_directory(path)?;
    let resolved = from_native(&native)?;
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut entries = BTreeMap::new();
    let mut matched = 0_u64;
    let mut retained_bytes = 0_u64;
    for (index, entry) in fs::read_dir(&native)
        .map_err(PathError::from_io)?
        .enumerate()
    {
        if index >= MAX_SCAN || Instant::now() >= deadline {
            return Err(PathError::Quota);
        }
        let entry = entry.map_err(PathError::from_io)?;
        let Ok(name) = entry.file_name().into_string() else {
            continue;
        };
        let metadata = match fs::metadata(entry.path()) {
            Ok(metadata) => metadata,
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
                ) =>
            {
                continue;
            }
            Err(error) => return Err(PathError::from_io(error)),
        };
        if !metadata.is_dir() {
            continue;
        }
        matched += 1;
        let entry_path = from_native(&entry.path())?;
        retained_bytes += (name.len() + entry_path.len()) as u64;
        entries.insert(
            name.clone(),
            DirectoryEntry {
                name,
                path: entry_path,
            },
        );
        if entries.len() > keep
            && let Some((_, removed)) = entries.pop_last()
        {
            retained_bytes -= (removed.name.len() + removed.path.len()) as u64;
        }
        if retained_bytes > max_bytes / 6 {
            return Err(PathError::Quota);
        }
    }
    Ok(DirectoryListResult {
        parent_path: parent_path(&resolved),
        path: resolved,
        entries: entries.into_values().skip(offset as usize).collect(),
        next_offset: (matched > keep as u64).then_some(keep as u64),
    })
}

#[cfg(windows)]
fn list_volumes(offset: u64, limit: u64) -> Result<DirectoryListResult, PathError> {
    // Enumerate drive letters without probing UNC namespaces or mapped remote drives.
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetLogicalDrives() -> u32;
        fn GetDriveTypeW(root: *const u16) -> u32;
    }
    let drives = unsafe { GetLogicalDrives() };
    if drives == 0 {
        return Err(PathError::from_io(std::io::Error::last_os_error()));
    }
    let entries: Vec<_> = (0..26)
        .filter(|index| drives & (1 << index) != 0)
        .filter(|index| {
            let root = [
                u16::from(b'A' + *index as u8),
                u16::from(b':'),
                u16::from(b'\\'),
                0,
            ];
            // DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_CDROM, DRIVE_RAMDISK.
            matches!(unsafe { GetDriveTypeW(root.as_ptr()) }, 2 | 3 | 5 | 6)
        })
        .map(|index| {
            let letter = (b'A' + index as u8) as char;
            DirectoryEntry {
                name: format!("{letter}:"),
                path: format!("/{letter}:/"),
            }
        })
        .collect();
    let end = offset + limit;
    let next_offset = (end < entries.len() as u64).then_some(end);
    Ok(DirectoryListResult {
        path: "/".to_owned(),
        parent_path: None,
        entries: entries
            .into_iter()
            .skip(offset as usize)
            .take(limit as usize)
            .collect(),
        next_offset,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn device_syntax_is_independent_of_local_platform() {
        for path in [
            "/",
            "/C:/",
            "/C:/work",
            "/UNC/server/share",
            "/UNC/server/share/",
            "/UNC/server/share/work",
        ] {
            assert!(validate(path, true).is_ok(), "{path}");
        }
        for path in [
            "C:work",
            "/C:work",
            "/C:/work/",
            "/C:/../work",
            "/UNC/server",
            "/C:/a\\b",
            "/C://work",
        ] {
            assert!(validate(path, true).is_err(), "{path}");
        }
        assert!(validate("/one\\two", false).is_ok());
        assert!(validate("/a/../b", false).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn native_round_trip_and_root_parent() {
        assert_eq!(
            from_native(&to_native("/tmp/example").unwrap()).unwrap(),
            "/tmp/example"
        );
        assert_eq!(parent_path("/"), None);
        assert_eq!(parent_path("/tmp"), Some("/".to_owned()));
    }
}
