//! Handle-relative mutations for Windows/PSEC, without DOS final-path lookup.
//! The directory capability owns path resolution. Each mutation names one child
//! and opens reparse points themselves rather than following a replacement link.
use std::{
    ffi::c_void,
    io,
    mem::{offset_of, size_of},
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle, OwnedHandle},
    },
    path::{Component, Path},
    ptr,
};
use windows_sys::Win32::{
    Foundation::{HANDLE, NTSTATUS, OBJ_CASE_INSENSITIVE, RtlNtStatusToDosError, UNICODE_STRING},
    Storage::FileSystem::{
        DELETE, FILE_ATTRIBUTE_NORMAL, FILE_DISPOSITION_FLAG_DELETE,
        FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE, FILE_DISPOSITION_FLAG_POSIX_SEMANTICS,
        FILE_DISPOSITION_INFO, FILE_DISPOSITION_INFO_EX, FILE_READ_ATTRIBUTES, FILE_RENAME_INFO,
        FILE_RENAME_INFO_0, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
        FileDispositionInfo, FileDispositionInfoEx, SetFileInformationByHandle,
    },
    System::IO::{IO_STATUS_BLOCK, IO_STATUS_BLOCK_0},
};

#[derive(Debug)]
pub(crate) struct Dir(cap_std::fs::Dir);

impl std::ops::Deref for Dir {
    type Target = cap_std::fs::Dir;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl Dir {
    pub(crate) fn open_ambient_dir(
        path: impl AsRef<Path>,
        authority: cap_std::AmbientAuthority,
    ) -> io::Result<Self> {
        cap_std::fs::Dir::open_ambient_dir(path, authority).map(Self)
    }

    fn native_path(&self) -> io::Result<std::path::PathBuf> {
        let mut path = std::ffi::OsString::from(r"\\?\GLOBALROOT");
        path.push(crate::windows_path::nt_name(&self.0)?);
        Ok(path.into())
    }

    pub(crate) fn entries(&self) -> io::Result<std::fs::ReadDir> {
        std::fs::read_dir(self.native_path()?)
    }

    pub(crate) fn remove_dir_all(&self, name: impl AsRef<Path>) -> io::Result<()> {
        child_name(name.as_ref())?;
        // Preserve std's Windows no-follow recursive removal, using the opened
        // parent's NT name instead of cap-std's denied DOS-volume query.
        std::fs::remove_dir_all(self.native_path()?.join(name))
    }

    pub(crate) fn create_dir(&self, name: impl AsRef<Path>) -> io::Result<()> {
        // FILE_CREATE, FILE_DIRECTORY_FILE. The kernel enforces the native write grant.
        open_child(&self.0, name.as_ref(), FILE_READ_ATTRIBUTES, 2, 1).map(drop)
    }

    pub(crate) fn remove_file(&self, name: impl AsRef<Path>) -> io::Result<()> {
        self.remove_entry(name.as_ref(), false)
    }

    pub(crate) fn remove_dir(&self, name: impl AsRef<Path>) -> io::Result<()> {
        self.remove_entry(name.as_ref(), true)
    }

    fn remove_entry(&self, name: &Path, directory: bool) -> io::Result<()> {
        let handle = open_child(
            &self.0,
            name,
            DELETE | FILE_READ_ATTRIBUTES,
            1,
            if directory { 1 } else { 0 },
        )?;
        let handle = std::fs::File::from(handle);
        // File removal also owns directory symlinks/junctions, but must reject
        // a normal directory substituted at the final name. Inspect this exact
        // no-follow handle rather than reopening the path.
        let kind = handle.metadata()?.file_type();
        if !directory && kind.is_dir() && !kind.is_symlink() {
            return Err(io::Error::from(io::ErrorKind::IsADirectory));
        }
        let disposition = FILE_DISPOSITION_INFO { DeleteFile: true };
        if unsafe {
            SetFileInformationByHandle(
                handle.as_raw_handle(),
                FileDispositionInfo,
                (&raw const disposition).cast(),
                size_of::<FILE_DISPOSITION_INFO>() as u32,
            )
        } == 0
        {
            let error = io::Error::last_os_error();
            if error.kind() != io::ErrorKind::PermissionDenied {
                return Err(error);
            }
            let disposition = FILE_DISPOSITION_INFO_EX {
                Flags: FILE_DISPOSITION_FLAG_DELETE
                    | FILE_DISPOSITION_FLAG_POSIX_SEMANTICS
                    | FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE,
            };
            if unsafe {
                SetFileInformationByHandle(
                    handle.as_raw_handle(),
                    FileDispositionInfoEx,
                    (&raw const disposition).cast(),
                    size_of::<FILE_DISPOSITION_INFO_EX>() as u32,
                )
            } == 0
            {
                return Err(io::Error::last_os_error());
            }
        }
        Ok(())
    }

    pub(crate) fn rename(
        &self,
        source: impl AsRef<Path>,
        destination_dir: &Self,
        destination: impl AsRef<Path>,
    ) -> io::Result<()> {
        self.rename_with_replace(source.as_ref(), destination_dir, destination.as_ref(), true)
    }

    pub(crate) fn rename_with_replace(
        &self,
        source: &Path,
        destination_dir: &Self,
        destination: &Path,
        replace: bool,
    ) -> io::Result<()> {
        child_name(destination)?;
        let target: Vec<u16> = crate::windows_path::nt_name(&destination_dir.0)?
            .join(destination)
            .as_os_str()
            .encode_wide()
            .collect();
        let source = open_child(&self.0, source, DELETE, 1, 0)?;
        let byte_len = target.len() * size_of::<u16>();
        let size = size_of::<FILE_RENAME_INFO>() + byte_len;
        // Allocate with the alignment of FILE_RENAME_INFO, including its HANDLE.
        let mut storage = vec![0_usize; size.div_ceil(size_of::<usize>())];
        let info = storage.as_mut_ptr().cast::<FILE_RENAME_INFO>();
        unsafe {
            info.write(FILE_RENAME_INFO {
                Anonymous: FILE_RENAME_INFO_0 {
                    ReplaceIfExists: replace,
                },
                RootDirectory: ptr::null_mut(),
                FileNameLength: u32::try_from(byte_len).map_err(|_| invalid_name())?,
                FileName: [0],
            });
            ptr::copy_nonoverlapping(
                target.as_ptr(),
                (&raw mut (*info).FileName).cast::<u16>(),
                target.len(),
            );
            let bytes = offset_of!(FILE_RENAME_INFO, FileName) + byte_len;
            let mut status = IO_STATUS_BLOCK {
                Anonymous: IO_STATUS_BLOCK_0 { Status: 0 },
                Information: 0,
            };
            // PSEC rejects a non-null RootDirectory for rename. Use the opened
            // destination's NT name; no Mount Manager or guessed drive alias.
            let result = NtSetInformationFile(
                source.as_raw_handle(),
                &mut status,
                info.cast(),
                bytes as u32,
                10,
            );
            if result < 0 {
                return Err(io::Error::from_raw_os_error(
                    RtlNtStatusToDosError(result) as i32
                ));
            }
        }
        Ok(())
    }
}

fn invalid_name() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidInput,
        "directory operation requires one child name",
    )
}

fn child_name(path: &Path) -> io::Result<Vec<u16>> {
    let mut components = path.components();
    if !matches!(components.next(), Some(Component::Normal(_))) || components.next().is_some() {
        return Err(invalid_name());
    }
    let name: Vec<u16> = path.as_os_str().encode_wide().collect();
    if name.contains(&0) || name.contains(&(b':' as u16)) || name.len() > (u16::MAX as usize / 2) {
        return Err(invalid_name());
    }
    Ok(name)
}

#[repr(C)]
struct ObjectAttributes {
    length: u32,
    root_directory: HANDLE,
    object_name: *const UNICODE_STRING,
    attributes: u32,
    security_descriptor: *const c_void,
    security_quality_of_service: *const c_void,
}

#[link(name = "ntdll")]
unsafe extern "system" {
    fn NtSetInformationFile(
        file_handle: HANDLE,
        io_status_block: *mut IO_STATUS_BLOCK,
        file_information: *const c_void,
        length: u32,
        information_class: u32,
    ) -> NTSTATUS;
    fn NtCreateFile(
        file_handle: *mut HANDLE,
        desired_access: u32,
        object_attributes: *const ObjectAttributes,
        io_status_block: *mut IO_STATUS_BLOCK,
        allocation_size: *const i64,
        file_attributes: u32,
        share_access: u32,
        create_disposition: u32,
        create_options: u32,
        ea_buffer: *const c_void,
        ea_length: u32,
    ) -> NTSTATUS;
}

fn open_child(
    parent: &cap_std::fs::Dir,
    name: &Path,
    access: u32,
    disposition: u32,
    options: u32,
) -> io::Result<OwnedHandle> {
    let mut name = child_name(name)?;
    let size = (name.len() * size_of::<u16>()) as u16;
    let name = UNICODE_STRING {
        Length: size,
        MaximumLength: size,
        Buffer: name.as_mut_ptr(),
    };
    let attributes = ObjectAttributes {
        length: size_of::<ObjectAttributes>() as u32,
        root_directory: parent.as_raw_handle(),
        object_name: &name,
        attributes: OBJ_CASE_INSENSITIVE,
        security_descriptor: ptr::null(),
        security_quality_of_service: ptr::null(),
    };
    let mut status = IO_STATUS_BLOCK {
        Anonymous: IO_STATUS_BLOCK_0 { Status: 0 },
        Information: 0,
    };
    let mut handle = ptr::null_mut();
    // SYNCHRONIZE, FILE_SYNCHRONOUS_IO_NONALERT, FILE_OPEN_REPARSE_POINT.
    let result = unsafe {
        NtCreateFile(
            &mut handle,
            access | 0x0010_0000,
            &attributes,
            &mut status,
            ptr::null(),
            FILE_ATTRIBUTE_NORMAL,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            disposition,
            options | 0x20 | 0x0020_0000,
            ptr::null(),
            0,
        )
    };
    if result < 0 {
        return Err(io::Error::from_raw_os_error(
            unsafe { RtlNtStatusToDosError(result) } as i32,
        ));
    }
    Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn handle_relative_mutations() {
        let root =
            std::env::temp_dir().join(crate::operation::random_selector("nt-dir-test").unwrap());
        std::fs::create_dir(&root).unwrap();
        let directory = Dir::open_ambient_dir(&root, cap_std::ambient_authority()).unwrap();
        directory.create_dir("child").unwrap();
        assert_eq!(
            directory.remove_file("child").unwrap_err().kind(),
            io::ErrorKind::IsADirectory
        );
        std::fs::write(root.join("source"), b"source").unwrap();
        directory.rename("source", &directory, "target").unwrap();
        assert_eq!(directory.read("target").unwrap(), b"source");
        std::fs::write(root.join("source"), b"replacement").unwrap();
        assert_eq!(
            directory
                .rename_with_replace(Path::new("source"), &directory, Path::new("target"), false)
                .unwrap_err()
                .kind(),
            io::ErrorKind::AlreadyExists
        );
        assert_eq!(directory.read("target").unwrap(), b"source");
        directory.rename("source", &directory, "target").unwrap();
        assert_eq!(directory.read("target").unwrap(), b"replacement");
        let mut permissions = std::fs::metadata(root.join("target"))
            .unwrap()
            .permissions();
        permissions.set_readonly(true);
        std::fs::set_permissions(root.join("target"), permissions).unwrap();
        directory.remove_file("target").unwrap();
        directory.remove_dir("child").unwrap();
        drop(directory);
        std::fs::remove_dir_all(root).unwrap();
    }
}
