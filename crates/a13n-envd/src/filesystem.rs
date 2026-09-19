use crate::{config::Config, device_path, eip::EIPPath};
use cap_std::{ambient_authority, fs::Dir};
#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::ffi::CString;
#[cfg(unix)]
use std::os::unix::{ffi::OsStrExt, io::AsRawFd};
use std::{
    error::Error,
    fmt, fs,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicU64, Ordering},
    },
};

const CANDIDATE_PREFIX: &str = ".eip-stage-";
const CANDIDATE_RANDOM_BYTES: usize = 16;

/// Native Device filesystem. Cwd and Session identity never constrain path access.
pub(crate) struct DeviceFilesystem {
    pub(crate) max_file_bytes: u64,
    staging_quota: StagingQuota,
    session_staging_limits: (u64, u64),
    pending_cleanup: Mutex<Vec<OwnedCandidate>>,
}

pub(crate) struct OpenedFile {
    pub(crate) file: fs::File,
    pub(crate) metadata: fs::Metadata,
}

#[derive(Clone)]
pub(crate) struct CommandCwd {
    pub(crate) native_path: PathBuf,
}

pub(crate) struct StagedCandidate {
    filesystem: Arc<DeviceFilesystem>,
    owned: Option<OwnedCandidate>,
}

pub(crate) struct OwnedCandidate {
    pub(crate) name: String,
    pub(crate) file: fs::File,
    destination: EIPPath,
    destination_name: std::ffi::OsString,
    parent: Dir,
    reservation: StagingReservation,
    removed: bool,
}

#[derive(Clone)]
struct StagingQuota {
    inner: Arc<StagingQuotaInner>,
}

struct StagingQuotaInner {
    state: Mutex<StagingQuotaState>,
    max_bytes: u64,
    max_objects: u64,
    parent: Option<StagingQuota>,
    cleanup_failures: Arc<AtomicU64>,
}

#[derive(Default)]
struct StagingQuotaState {
    bytes: u64,
    objects: u64,
}

struct StagingReservation {
    quota: StagingQuota,
    bytes: u64,
    state: ReservationState,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ReservationState {
    Active,
    Released,
    Retained,
}

impl StagingQuota {
    fn new(max_bytes: u64, max_objects: u64) -> Result<Self, FilesystemInitError> {
        if max_bytes == 0 || max_objects == 0 {
            return Err(FilesystemInitError::new(
                "staging byte and object limits must be positive",
            ));
        }
        Ok(Self {
            inner: Arc::new(StagingQuotaInner {
                state: Mutex::new(StagingQuotaState::default()),
                max_bytes,
                max_objects,
                parent: None,
                cleanup_failures: Arc::new(AtomicU64::new(0)),
            }),
        })
    }

    fn for_session(&self, limits: (u64, u64)) -> Self {
        Self {
            inner: Arc::new(StagingQuotaInner {
                state: Mutex::new(StagingQuotaState::default()),
                max_bytes: limits.0,
                max_objects: limits.1,
                parent: Some(self.clone()),
                cleanup_failures: self.inner.cleanup_failures.clone(),
            }),
        }
    }

    fn usage(&self) -> (u64, u64) {
        let state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        (state.bytes, state.objects)
    }

    fn reserve_object(&self) -> Result<StagingReservation, PathError> {
        self.charge_object()?;
        Ok(StagingReservation {
            quota: self.clone(),
            bytes: 0,
            state: ReservationState::Active,
        })
    }

    fn charge_object(&self) -> Result<(), PathError> {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if state.objects >= self.inner.max_objects {
            return Err(PathError::Quota);
        }
        if let Some(parent) = &self.inner.parent {
            parent.charge_object()?;
        }
        state.objects += 1;
        Ok(())
    }

    fn reserve_bytes(&self, bytes: u64) -> Result<(), PathError> {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let next = state.bytes.checked_add(bytes).ok_or(PathError::Quota)?;
        if next > self.inner.max_bytes {
            return Err(PathError::Quota);
        }
        if let Some(parent) = &self.inner.parent {
            parent.reserve_bytes(bytes)?;
        }
        state.bytes = next;
        Ok(())
    }

    fn release(&self, bytes: u64) {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.bytes = state
            .bytes
            .checked_sub(bytes)
            .expect("staging byte reservation releases exactly once");
        state.objects = state
            .objects
            .checked_sub(1)
            .expect("staging object reservation releases exactly once");
        if let Some(parent) = &self.inner.parent {
            parent.release(bytes);
        }
    }
}

impl StagingReservation {
    fn reserve_bytes(&mut self, bytes: u64) -> Result<(), PathError> {
        if self.state != ReservationState::Active {
            return Err(PathError::Internal);
        }
        self.quota.reserve_bytes(bytes)?;
        self.bytes = self.bytes.checked_add(bytes).ok_or(PathError::Internal)?;
        Ok(())
    }

    fn release(&mut self) {
        if self.state == ReservationState::Active {
            self.quota.release(self.bytes);
            self.state = ReservationState::Released;
        }
    }

    fn retain(&mut self) {
        if self.state == ReservationState::Active {
            self.state = ReservationState::Retained;
        }
    }
}

impl Drop for StagingReservation {
    fn drop(&mut self) {
        self.release();
    }
}

impl DeviceFilesystem {
    pub(crate) fn new(config: &Config) -> Result<Arc<Self>, FilesystemInitError> {
        Ok(Arc::new(Self {
            max_file_bytes: config.limits.max_file_bytes,
            pending_cleanup: Mutex::new(Vec::new()),
            staging_quota: StagingQuota::new(
                config.limits.max_device_staged_file_bytes,
                config.limits.max_device_staged_file_objects,
            )?,
            session_staging_limits: (
                config.limits.max_staged_file_bytes,
                config.limits.max_staged_file_objects,
            ),
        }))
    }

    pub(crate) fn for_session(&self) -> Arc<Self> {
        Arc::new(Self {
            max_file_bytes: self.max_file_bytes,
            staging_quota: self.staging_quota.for_session(self.session_staging_limits),
            session_staging_limits: self.session_staging_limits,
            pending_cleanup: Mutex::new(Vec::new()),
        })
    }

    pub(crate) fn cleanup_failures(&self) -> u64 {
        self.staging_quota
            .inner
            .cleanup_failures
            .load(Ordering::Relaxed)
    }

    pub(crate) fn staging_usage(&self) -> (u64, u64) {
        self.staging_quota.usage()
    }

    /// Retry failed native deletion without releasing its Session owner or Device charge.
    pub(crate) fn retry_cleanup(&self) -> bool {
        let pending = std::mem::take(
            &mut *self
                .pending_cleanup
                .lock()
                .unwrap_or_else(PoisonError::into_inner),
        );
        let mut failed = Vec::new();
        for mut candidate in pending {
            if candidate.delete().is_err() {
                self.staging_quota
                    .inner
                    .cleanup_failures
                    .fetch_add(1, Ordering::Relaxed);
                failed.push(candidate);
            }
        }
        let mut pending = self
            .pending_cleanup
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        pending.extend(failed);
        pending.is_empty()
    }

    pub(crate) fn resolve_command_cwd(&self, path: &EIPPath) -> Result<CommandCwd, PathError> {
        Ok(CommandCwd {
            native_path: device_path::resolve_directory(&path.path)?,
        })
    }

    pub(crate) fn resolve_executable(&self, path: &EIPPath) -> Result<PathBuf, PathError> {
        let native = self.resolve_followed(path)?;
        let metadata = fs::metadata(&native).map_err(PathError::from_io)?;
        if !metadata.is_file() {
            return Err(PathError::NotRegular);
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o111 == 0 {
                return Err(PathError::Denied);
            }
        }
        Ok(native)
    }

    pub(crate) fn resolve_target(&self, path: &EIPPath) -> Result<EIPPath, PathError> {
        Ok(EIPPath {
            path: device_path::from_native(&self.resolve_followed(path)?)?,
        })
    }

    pub(crate) fn open_regular(&self, path: &EIPPath) -> Result<OpenedFile, PathError> {
        let native = self.resolve_followed(path)?;
        // Reject FIFOs/devices before opening; freeze the final entry without following a replacement link.
        if !fs::metadata(&native).map_err(PathError::from_io)?.is_file() {
            return Err(PathError::NotRegular);
        }
        let (parent, name) = self.open_parent(&EIPPath {
            path: device_path::from_native(&native)?,
        })?;
        let mut options = cap_std::fs::OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let file = parent
            .open_with(name, &options)
            .map_err(PathError::from_io)?
            .into_std();
        let metadata = file.metadata().map_err(PathError::from_io)?;
        if !metadata.is_file() {
            return Err(PathError::NotRegular);
        }
        Ok(OpenedFile { file, metadata })
    }

    pub(crate) fn metadata(
        &self,
        path: &EIPPath,
        follow_symlinks: bool,
    ) -> Result<fs::Metadata, PathError> {
        let native = device_path::to_native(&path.path)?;
        if follow_symlinks {
            fs::metadata(native)
        } else {
            fs::symlink_metadata(native)
        }
        .map_err(PathError::from_io)
    }

    pub(crate) fn resolve_followed(&self, path: &EIPPath) -> Result<PathBuf, PathError> {
        fs::canonicalize(device_path::to_native(&path.path)?).map_err(PathError::from_io)
    }

    pub(crate) fn resolve_entry(&self, path: &EIPPath) -> Result<PathBuf, PathError> {
        let native = device_path::to_native(&path.path)?;
        let Some(name) = native.file_name() else {
            return Ok(native);
        };
        let parent = native.parent().ok_or(PathError::Invalid)?;
        Ok(fs::canonicalize(parent)
            .map_err(PathError::from_io)?
            .join(name))
    }

    pub(crate) fn remove_file(&self, native: &Path) -> Result<(), PathError> {
        let (parent, name) = self.open_parent(&EIPPath {
            path: device_path::from_native(native)?,
        })?;
        parent.remove_file(name).map_err(PathError::from_io)
    }

    pub(crate) fn remove_dir(&self, native: &Path) -> Result<(), PathError> {
        let (parent, name) = self.open_parent(&EIPPath {
            path: device_path::from_native(native)?,
        })?;
        parent.remove_dir(name).map_err(PathError::from_io)
    }

    pub(crate) fn rename(
        &self,
        source: &EIPPath,
        destination: &EIPPath,
        replace: bool,
    ) -> Result<(), PathError> {
        let (source_parent, source_name) = self.open_parent(source)?;
        let (destination_parent, destination_name) = self.open_parent(destination)?;
        if replace {
            source_parent
                .rename(&source_name, &destination_parent, &destination_name)
                .map_err(PathError::from_io)?;
        } else {
            rename_no_replace(
                &source_parent,
                Path::new(&source_name),
                &destination_parent,
                Path::new(&destination_name),
            )
            .map_err(PathError::from_io)?;
        }
        sync_directory(&destination_parent).map_err(|_| PathError::UnknownOutcome)?;
        sync_directory(&source_parent).map_err(|_| PathError::UnknownOutcome)
    }

    pub(crate) fn open_parent(
        &self,
        path: &EIPPath,
    ) -> Result<(Dir, std::ffi::OsString), PathError> {
        let native = self.resolve_entry(path)?;
        let name = native.file_name().ok_or(PathError::Denied)?.to_os_string();
        let parent = native.parent().ok_or(PathError::Denied)?;
        let directory =
            Dir::open_ambient_dir(parent, ambient_authority()).map_err(PathError::from_io)?;
        Ok((directory, name))
    }

    pub(crate) fn create_candidate(
        self: &Arc<Self>,
        destination: &EIPPath,
    ) -> Result<StagedCandidate, PathError> {
        let (parent, destination_name) = self.open_parent(destination)?;
        self.create_candidate_in(destination, parent, destination_name)
    }

    fn create_candidate_in(
        self: &Arc<Self>,
        destination: &EIPPath,
        parent: Dir,
        destination_name: std::ffi::OsString,
    ) -> Result<StagedCandidate, PathError> {
        self.retry_cleanup();
        let reservation = self.staging_quota.reserve_object()?;
        for _ in 0..32 {
            let name = random_candidate_name().map_err(|_| PathError::Internal)?;
            let mut options = cap_std::fs::OpenOptions::new();
            options.write(true).read(true).create_new(true);
            match parent.open_with(&name, &options) {
                Ok(file) => {
                    let file = file.into_std();
                    let candidate = StagedCandidate {
                        filesystem: Arc::clone(self),
                        owned: Some(OwnedCandidate {
                            name,
                            file,
                            destination: destination.clone(),
                            destination_name,
                            parent,
                            reservation,
                            removed: false,
                        }),
                    };
                    set_private_file_permissions(&candidate.file).map_err(PathError::from_io)?;
                    return Ok(candidate);
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(PathError::from_io(error)),
            }
        }
        Err(PathError::Internal)
    }

    pub(crate) fn publish_candidate(
        &self,
        candidate: &mut StagedCandidate,
        destination: &EIPPath,
        replace: bool,
    ) -> Result<(), PathError> {
        if candidate.destination != *destination {
            return Err(PathError::Denied);
        }
        let source = PathBuf::from(&candidate.name);
        let target = PathBuf::from(&candidate.destination_name);
        let held = cap_std::fs::Metadata::from_file(&candidate.file).map_err(PathError::from_io)?;
        let named = candidate
            .parent
            .symlink_metadata(&source)
            .map_err(PathError::from_io)?;
        if !named.is_file() || named.is_symlink() || !same_cap_file(&held, &named) {
            return Err(PathError::Denied);
        }
        if replace {
            candidate
                .parent
                .rename(&source, &candidate.parent, &target)
                .map_err(PathError::from_io)?;
        } else {
            rename_no_replace(&candidate.parent, &source, &candidate.parent, &target)
                .map_err(PathError::from_io)?;
        }
        candidate.mark_removed();
        let published = candidate
            .parent
            .symlink_metadata(&target)
            .map_err(|_| PathError::UnknownOutcome)?;
        if !published.is_file() || published.is_symlink() || !same_cap_file(&held, &published) {
            return Err(PathError::UnknownOutcome);
        }
        sync_directory(&candidate.parent).map_err(|_| PathError::UnknownOutcome)?;
        Ok(())
    }
}

/// A retained directory for one conditional publication, not a Session path policy.
pub(crate) struct CommitDirectory {
    root: Dir,
}

impl CommitDirectory {
    pub(crate) fn open(path: &EIPPath) -> Result<Self, PathError> {
        let native = device_path::to_native(&path.path)?;
        fs::create_dir_all(&native).map_err(PathError::from_io)?;
        for ancestor in native.ancestors() {
            let directory =
                Dir::open_ambient_dir(ancestor, ambient_authority()).map_err(PathError::from_io)?;
            sync_directory(&directory).map_err(PathError::from_io)?;
        }
        Ok(Self {
            root: Dir::open_ambient_dir(native, ambient_authority()).map_err(PathError::from_io)?,
        })
    }

    pub(crate) fn lock_file(&self) -> Result<fs::File, PathError> {
        // Linux Dir handles can use O_PATH; flock requires a readable descriptor.
        self.root
            .open(".")
            .map(|file| file.into_std())
            .map_err(PathError::from_io)
    }

    pub(crate) fn open_regular(&self, path: &Path) -> Result<OpenedFile, PathError> {
        if !self
            .root
            .metadata(path)
            .map_err(PathError::from_io)?
            .is_file()
        {
            return Err(PathError::NotRegular);
        }
        let mut options = cap_std::fs::OpenOptions::new();
        options.read(true);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NONBLOCK);
        }
        let file = self
            .root
            .open_with(path, &options)
            .map_err(PathError::from_io)?
            .into_std();
        let metadata = file.metadata().map_err(PathError::from_io)?;
        if !metadata.is_file() {
            return Err(PathError::NotRegular);
        }
        Ok(OpenedFile { file, metadata })
    }

    pub(crate) fn mkdir(&self, path: &Path) -> Result<(), PathError> {
        self.root.create_dir_all(path).map_err(PathError::from_io)?;
        for ancestor in path.ancestors() {
            let ancestor = if ancestor.as_os_str().is_empty() {
                Path::new(".")
            } else {
                ancestor
            };
            let directory = self.root.open_dir(ancestor).map_err(PathError::from_io)?;
            sync_directory(&directory).map_err(PathError::from_io)?;
        }
        Ok(())
    }

    pub(crate) fn write_text(
        &self,
        filesystem: &Arc<DeviceFilesystem>,
        path: &Path,
        destination: &EIPPath,
        text: &str,
    ) -> Result<(), PathError> {
        use std::io::Write;
        let parent = path.parent().ok_or(PathError::Invalid)?;
        let parent = if parent.as_os_str().is_empty() {
            Path::new(".")
        } else {
            parent
        };
        let parent = self.root.open_dir(parent).map_err(PathError::from_io)?;
        let name = path.file_name().ok_or(PathError::Invalid)?.to_os_string();
        let mut candidate = filesystem.create_candidate_in(destination, parent, name)?;
        candidate.reserve_bytes(text.len() as u64)?;
        candidate
            .file
            .write_all(text.as_bytes())
            .map_err(PathError::from_io)?;
        if !candidate.verify_complete(text.len() as u64)? {
            return Err(PathError::UnknownOutcome);
        }
        filesystem.publish_candidate(&mut candidate, destination, true)
    }

    pub(crate) fn remove(&self, path: &Path, max_entries: usize) -> Result<(), PathError> {
        // Walk through capability-relative names and never follow directory links.
        // One explicit stack bounds traversal without native call-stack recursion.
        let mut pending = vec![(path.to_path_buf(), false)];
        let mut entries = 0;
        while let Some((entry, visited)) = pending.pop() {
            if visited {
                self.root.remove_dir(&entry).map_err(PathError::from_io)?;
                continue;
            }
            entries += 1;
            if entries > max_entries {
                return Err(PathError::Quota);
            }
            let metadata = self
                .root
                .symlink_metadata(&entry)
                .map_err(PathError::from_io)?;
            if metadata.is_dir() {
                pending.push((entry.clone(), true));
                for child in self.root.read_dir(&entry).map_err(PathError::from_io)? {
                    let child = child.map_err(PathError::from_io)?;
                    if entries + pending.len() > max_entries {
                        return Err(PathError::Quota);
                    }
                    pending.push((entry.join(child.file_name()), false));
                }
            } else if entry == path {
                return Err(PathError::Invalid);
            } else {
                self.root.remove_file(&entry).map_err(PathError::from_io)?;
            }
        }
        let parent = path.parent().ok_or(PathError::Invalid)?;
        let parent = if parent.as_os_str().is_empty() {
            Path::new(".")
        } else {
            parent
        };
        sync_directory(&self.root.open_dir(parent).map_err(PathError::from_io)?)
            .map_err(PathError::from_io)
    }
}

impl StagedCandidate {
    pub(crate) fn filesystem(&self) -> &Arc<DeviceFilesystem> {
        &self.filesystem
    }
}

impl std::ops::Deref for StagedCandidate {
    type Target = OwnedCandidate;

    fn deref(&self) -> &Self::Target {
        self.owned
            .as_ref()
            .expect("candidate ownership exists until Drop")
    }
}

impl std::ops::DerefMut for StagedCandidate {
    fn deref_mut(&mut self) -> &mut Self::Target {
        self.owned
            .as_mut()
            .expect("candidate ownership exists until Drop")
    }
}

impl OwnedCandidate {
    pub(crate) fn reserve_bytes(&mut self, bytes: u64) -> Result<(), PathError> {
        self.reservation.reserve_bytes(bytes)
    }

    pub(crate) fn verify_complete(&self, expected_size: u64) -> Result<bool, PathError> {
        self.file.sync_all().map_err(PathError::from_io)?;
        let metadata = self.file.metadata().map_err(PathError::from_io)?;
        Ok(metadata.len() == expected_size && !file_has_multiple_links(&metadata))
    }

    pub(crate) fn mark_removed(&mut self) {
        self.removed = true;
        self.reservation.release();
    }

    fn named_identity_matches(&self) -> Result<bool, PathError> {
        let held = cap_std::fs::Metadata::from_file(&self.file).map_err(PathError::from_io)?;
        let named = self
            .parent
            .symlink_metadata(&self.name)
            .map_err(PathError::from_io)?;
        Ok(named.is_file() && !named.is_symlink() && same_cap_file(&held, &named))
    }

    pub(crate) fn delete(&mut self) -> Result<(), PathError> {
        if self.removed {
            return Ok(());
        }
        if !self.named_identity_matches()? {
            return Err(PathError::UnknownOutcome);
        }
        self.parent
            .remove_file(&self.name)
            .map_err(PathError::from_io)?;
        self.mark_removed();
        Ok(())
    }
}

impl Drop for StagedCandidate {
    fn drop(&mut self) {
        if self.delete().is_err() {
            let owned = self
                .owned
                .take()
                .expect("candidate cleanup transfers exactly once");
            self.filesystem
                .pending_cleanup
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .push(owned);
        }
    }
}

impl Drop for OwnedCandidate {
    fn drop(&mut self) {
        if !self.removed {
            // A daemon shutdown may end retries; uncertainty never releases the charge.
            self.reservation.retain();
        }
    }
}

fn random_candidate_name() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; CANDIDATE_RANDOM_BYTES];
    getrandom::fill(&mut bytes)?;
    let mut name = String::with_capacity(CANDIDATE_PREFIX.len() + CANDIDATE_RANDOM_BYTES * 2);
    name.push_str(CANDIDATE_PREFIX);
    for byte in bytes {
        use fmt::Write as _;
        let _ = write!(name, "{byte:02x}");
    }
    Ok(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scoped_staging_quota_keeps_siblings_independent_and_releases_both_charges() {
        let device = StagingQuota::new(12, 2).unwrap();
        let first = device.for_session((8, 1));
        let sibling = device.for_session((8, 1));
        let mut a = first.reserve_object().unwrap();
        a.reserve_bytes(8).unwrap();
        assert!(matches!(first.reserve_object(), Err(PathError::Quota)));
        assert!(matches!(a.reserve_bytes(1), Err(PathError::Quota)));
        let mut b = sibling.reserve_object().unwrap();
        assert!(matches!(b.reserve_bytes(8), Err(PathError::Quota)));
        assert_eq!(sibling.usage(), (0, 1));
        b.reserve_bytes(4).unwrap();
        assert_eq!(device.usage(), (12, 2));
        let third = device.for_session((8, 1));
        assert!(matches!(third.reserve_object(), Err(PathError::Quota)));
        assert_eq!(third.usage(), (0, 0));
        drop(a);
        b.reserve_bytes(4).unwrap();
        assert_eq!(device.usage(), (8, 1));
        assert_eq!(first.usage(), (0, 0));
        drop(b);
        assert_eq!(device.usage(), (0, 0));
    }

    #[test]
    fn failed_candidate_deletion_retains_session_owner_and_device_quota_until_retry() {
        let directory =
            std::env::temp_dir().join(crate::operation::random_selector("candidate-test").unwrap());
        fs::create_dir(&directory).unwrap();
        let mut config = Config::for_test("device-test");
        config.limits.max_staged_file_bytes = 4;
        config.limits.max_staged_file_objects = 1;
        config.limits.max_device_staged_file_bytes = 4;
        config.limits.max_device_staged_file_objects = 1;
        let device = DeviceFilesystem::new(&config).unwrap();
        let first = device.for_session();
        let sibling = device.for_session();
        let destination = EIPPath {
            path: device_path::from_native(&directory.join("target")).unwrap(),
        };
        let mut candidate = first.create_candidate(&destination).unwrap();
        candidate.reserve_bytes(4).unwrap();
        assert_eq!(
            candidate.name.len(),
            CANDIDATE_PREFIX.len() + CANDIDATE_RANDOM_BYTES * 2
        );
        let named = directory.join(&candidate.name);
        let displaced = directory.join("displaced");
        fs::rename(&named, &displaced).unwrap();
        fs::write(&named, b"unrelated").unwrap();
        assert!(candidate.delete().is_err());
        drop(candidate);
        assert_eq!(first.pending_cleanup.lock().unwrap().len(), 1);
        assert!(!first.retry_cleanup());
        assert!(matches!(
            sibling.create_candidate(&destination),
            Err(PathError::Quota)
        ));
        {
            let quota = device.staging_quota.inner.state.lock().unwrap();
            assert_eq!((quota.bytes, quota.objects), (4, 1));
        }
        assert_eq!(fs::read(&named).unwrap(), b"unrelated");
        fs::remove_file(&named).unwrap();
        fs::rename(displaced, named).unwrap();
        assert!(first.retry_cleanup());
        assert!(first.retry_cleanup());
        {
            let quota = device.staging_quota.inner.state.lock().unwrap();
            assert_eq!((quota.bytes, quota.objects), (0, 0));
        }
        drop(sibling.create_candidate(&destination).unwrap());
        fs::remove_dir(directory).unwrap();
    }
}

#[cfg(unix)]
fn same_cap_file(left: &cap_std::fs::Metadata, right: &cap_std::fs::Metadata) -> bool {
    use cap_std::fs::MetadataExt;
    left.dev() == right.dev() && left.ino() == right.ino()
}

#[cfg(windows)]
fn same_cap_file(left: &cap_std::fs::Metadata, right: &cap_std::fs::Metadata) -> bool {
    use cap_primitives::fs::_WindowsByHandle;
    match (
        left.volume_serial_number(),
        left.file_index(),
        right.volume_serial_number(),
        right.file_index(),
    ) {
        (Some(left_volume), Some(left_index), Some(right_volume), Some(right_index)) => {
            left_volume == right_volume && left_index == right_index
        }
        _ => false,
    }
}

#[cfg(not(any(unix, windows)))]
fn same_cap_file(_left: &cap_std::fs::Metadata, _right: &cap_std::fs::Metadata) -> bool {
    false
}

#[cfg(unix)]
fn file_has_multiple_links(metadata: &fs::Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    metadata.nlink() != 1
}

#[cfg(not(unix))]
fn file_has_multiple_links(_metadata: &fs::Metadata) -> bool {
    false
}

#[cfg(unix)]
fn set_private_file_permissions(file: &fs::File) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    file.set_permissions(fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn set_private_file_permissions(_file: &fs::File) -> std::io::Result<()> {
    Ok(())
}

#[cfg(unix)]
fn sync_directory(directory: &Dir) -> std::io::Result<()> {
    directory.open(".")?.sync_all()
}

#[cfg(not(unix))]
fn sync_directory(_directory: &Dir) -> std::io::Result<()> {
    Ok(())
}

#[cfg(target_os = "macos")]
fn rename_no_replace(
    source_dir: &Dir,
    source: &Path,
    destination_dir: &Dir,
    destination: &Path,
) -> std::io::Result<()> {
    let source = CString::new(source.as_os_str().as_bytes()).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "source contains NUL")
    })?;
    let destination = CString::new(destination.as_os_str().as_bytes()).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "destination contains NUL")
    })?;
    let result = unsafe {
        libc::renameatx_np(
            source_dir.as_raw_fd(),
            source.as_ptr(),
            destination_dir.as_raw_fd(),
            destination.as_ptr(),
            libc::RENAME_EXCL,
        )
    };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(target_os = "linux")]
fn rename_no_replace(
    source_dir: &Dir,
    source: &Path,
    destination_dir: &Dir,
    destination: &Path,
) -> std::io::Result<()> {
    let source = CString::new(source.as_os_str().as_bytes()).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "source contains NUL")
    })?;
    let destination = CString::new(destination.as_os_str().as_bytes()).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "destination contains NUL")
    })?;
    let result = unsafe {
        libc::syscall(
            libc::SYS_renameat2,
            source_dir.as_raw_fd(),
            source.as_ptr(),
            destination_dir.as_raw_fd(),
            destination.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(windows)]
fn rename_no_replace(
    source_dir: &Dir,
    source: &Path,
    destination_dir: &Dir,
    destination: &Path,
) -> std::io::Result<()> {
    source_dir.rename(source, destination_dir, destination)
}

#[cfg(not(any(target_os = "macos", target_os = "linux", windows)))]
fn rename_no_replace(
    _source_dir: &Dir,
    _source: &Path,
    _destination_dir: &Dir,
    _destination: &Path,
) -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "atomic no-replace rename is unsupported on this platform",
    ))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PathError {
    Invalid,
    Denied,
    NotFound,
    AlreadyExists,
    NotRegular,
    Quota,
    Unsupported,
    UnknownOutcome,
    Io,
    Internal,
}

impl PathError {
    pub(crate) fn from_io(error: std::io::Error) -> Self {
        match error.kind() {
            std::io::ErrorKind::NotFound => Self::NotFound,
            std::io::ErrorKind::AlreadyExists => Self::AlreadyExists,
            std::io::ErrorKind::PermissionDenied => Self::Denied,
            std::io::ErrorKind::NotADirectory | std::io::ErrorKind::IsADirectory => {
                Self::NotRegular
            }
            std::io::ErrorKind::Unsupported => Self::Unsupported,
            _ => Self::Io,
        }
    }
}

#[derive(Debug)]
pub(crate) struct FilesystemInitError {
    message: String,
}

impl FilesystemInitError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for FilesystemInitError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for FilesystemInitError {}
