use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    fmt, fs,
    path::{Component, Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
};

#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::ffi::CString;
#[cfg(unix)]
use std::os::unix::{ffi::OsStrExt, io::AsRawFd};

use cap_std::{ambient_authority, fs::Dir};

use crate::{
    config::{Config, TransportConfig, TrustedMountConfig},
    eip::{EIPPath, MountDescriptor},
};

const READ_OPERATIONS: &[&str] = &["stat", "read_text", "open_reader", "list"];
const WRITE_OPERATIONS: &[&str] = &[
    "write_text",
    "open_writer",
    "mkdir",
    "patch_text",
    "copy",
    "move",
    "remove",
];
const OPTIONAL_OPERATIONS: &[&str] = &["find", "search", "command_cwd", "executable_source"];
const CANDIDATE_PREFIX: &str = ".eip-stage-";
const CANDIDATE_RANDOM_BYTES: usize = 16;

#[derive(Clone)]
pub(crate) struct MountRegistry {
    mounts: BTreeMap<String, Arc<Mount>>,
    root_mount_id: Option<String>,
}

pub(crate) struct Mount {
    pub(crate) mount_id: String,
    pub(crate) native_root: PathBuf,
    pub(crate) root: Arc<Dir>,
    pub(crate) writable: bool,
    pub(crate) allow_command_execution: bool,
    pub(crate) max_file_bytes: u64,
    allowed_operations: BTreeSet<String>,
    protected_roots: Vec<PathBuf>,
    staging_quota: StagingQuota,
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
    pub(crate) name: String,
    pub(crate) file: fs::File,
    mount: Arc<Mount>,
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

impl MountRegistry {
    pub(crate) fn initialize_scoped(config: &Config) -> Result<Self, MountInitError> {
        let prepared = config
            .mounts
            .iter()
            .map(PreparedMount::new)
            .collect::<Result<Vec<_>, _>>()?;
        validate_topology(&prepared)?;
        validate_private_runtime_separation(config, &prepared)?;
        Self::from_prepared(config, prepared, config.root_mount_id.as_deref())
    }

    fn from_prepared(
        config: &Config,
        prepared: Vec<PreparedMount>,
        configured_root_mount_id: Option<&str>,
    ) -> Result<Self, MountInitError> {
        let staging_quota = StagingQuota::new(
            config.limits.max_staged_file_bytes,
            config.limits.max_staged_file_objects,
        )?;
        let mut mounts = BTreeMap::new();
        for prepared in prepared {
            if mounts.contains_key(&prepared.mount_id) {
                return Err(MountInitError::new("mount_id values must be unique"));
            }
            let protected_roots = protected_roots_for_mount(config, &prepared.native_root)?;
            let root = open_capability_directory(&prepared.native_root)?;
            let mount = Arc::new(Mount {
                mount_id: prepared.mount_id.clone(),
                native_root: prepared.native_root,
                root: Arc::new(root),
                writable: prepared.writable,
                allow_command_execution: prepared.allow_command_execution,
                max_file_bytes: prepared.max_file_bytes,
                allowed_operations: prepared.allowed_operations,
                protected_roots,
                staging_quota: staging_quota.clone(),
            });
            mounts.insert(prepared.mount_id, mount);
        }
        let root_mount_id = match configured_root_mount_id {
            Some(mount_id) if mounts.contains_key(mount_id) => Some(mount_id.to_owned()),
            Some(_) => {
                return Err(MountInitError::new(
                    "root_mount_id must reference a configured mount",
                ));
            }
            None if mounts.len() == 1 => mounts.keys().next().cloned(),
            None if mounts.len() > 1 => {
                return Err(MountInitError::new(
                    "root_mount_id is required when multiple mounts are configured",
                ));
            }
            None => None,
        };
        Ok(Self {
            mounts,
            root_mount_id,
        })
    }

    pub(crate) fn root_mount_id(&self) -> Option<&str> {
        self.root_mount_id.as_deref()
    }

    pub(crate) fn get(&self, mount_id: &str) -> Option<Arc<Mount>> {
        self.mounts.get(mount_id).cloned()
    }

    pub(crate) fn descriptors(&self) -> Vec<MountDescriptor> {
        self.mounts
            .values()
            .map(|mount| mount.descriptor())
            .collect()
    }

    pub(crate) fn supports_anywhere(&self, operation: &str) -> bool {
        self.mounts.values().any(|mount| mount.allows(operation))
    }

    pub(crate) fn supports_commands(&self) -> bool {
        self.mounts
            .values()
            .any(|mount| mount.allow_command_execution && mount.allows("command_cwd"))
    }

    pub(crate) fn resolve_command_cwd(&self, path: &EIPPath) -> Result<CommandCwd, MountPathError> {
        let mount = self.get(&path.mount_id).ok_or(MountPathError::Denied)?;
        if !mount.allow_command_execution || !mount.allows("command_cwd") {
            return Err(MountPathError::Denied);
        }
        let canonical = mount.resolve_followed_relative(path)?;
        let metadata = mount
            .root
            .metadata(&canonical)
            .map_err(MountPathError::from_io)?;
        if !metadata.is_dir() {
            return Err(MountPathError::Denied);
        }
        validate_canonical_relative(&canonical)?;
        Ok(CommandCwd {
            native_path: mount.native_root.join(&canonical),
        })
    }

    pub(crate) fn resolve_executable(&self, path: &EIPPath) -> Result<PathBuf, MountPathError> {
        let mount = self.get(&path.mount_id).ok_or(MountPathError::Denied)?;
        if !mount.allow_command_execution || !mount.allows("executable_source") {
            return Err(MountPathError::Denied);
        }
        let canonical = mount.resolve_followed_relative(path)?;
        let metadata = mount
            .root
            .metadata(&canonical)
            .map_err(MountPathError::from_io)?;
        if !metadata.is_file() {
            return Err(MountPathError::NotRegular);
        }
        #[cfg(unix)]
        {
            use cap_std::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o111 == 0 {
                return Err(MountPathError::Denied);
            }
        }
        Ok(mount.native_root.join(canonical))
    }
}

fn subtree_contains_protected(protected_roots: &[PathBuf], relative: &Path) -> bool {
    let relative = relative.strip_prefix(Path::new(".")).unwrap_or(relative);
    protected_roots
        .iter()
        .any(|protected| protected == relative || protected.starts_with(relative))
}

fn validate_canonical_relative(path: &Path) -> Result<(), MountPathError> {
    for component in path.components() {
        let Component::Normal(segment) = component else {
            if component == Component::CurDir {
                continue;
            }
            return Err(MountPathError::Denied);
        };
        if segment.is_empty() {
            return Err(MountPathError::Denied);
        }
    }
    Ok(())
}

impl StagingQuota {
    fn new(max_bytes: u64, max_objects: u64) -> Result<Self, MountInitError> {
        if max_bytes == 0 || max_objects == 0 {
            return Err(MountInitError::new(
                "staging byte and object limits must be positive",
            ));
        }
        Ok(Self {
            inner: Arc::new(StagingQuotaInner {
                state: Mutex::new(StagingQuotaState::default()),
                max_bytes,
                max_objects,
            }),
        })
    }

    fn reserve_object(&self) -> Result<StagingReservation, MountPathError> {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if state.objects >= self.inner.max_objects {
            return Err(MountPathError::Quota);
        }
        state.objects += 1;
        Ok(StagingReservation {
            quota: self.clone(),
            bytes: 0,
            state: ReservationState::Active,
        })
    }

    fn reserve_bytes(&self, bytes: u64) -> Result<(), MountPathError> {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let next = state
            .bytes
            .checked_add(bytes)
            .ok_or(MountPathError::Quota)?;
        if next > self.inner.max_bytes {
            return Err(MountPathError::Quota);
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
    }
}

impl StagingReservation {
    fn reserve_bytes(&mut self, bytes: u64) -> Result<(), MountPathError> {
        if self.state != ReservationState::Active {
            return Err(MountPathError::Internal);
        }
        self.quota.reserve_bytes(bytes)?;
        self.bytes = self
            .bytes
            .checked_add(bytes)
            .ok_or(MountPathError::Internal)?;
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

impl Mount {
    pub(crate) fn allows(&self, operation: &str) -> bool {
        self.allowed_operations.contains(operation)
    }

    pub(crate) fn descriptor(&self) -> MountDescriptor {
        MountDescriptor {
            mount_id: self.mount_id.clone(),
            logical_root: "/".to_owned(),
            writable: self.writable,
            case_sensitive: None,
            supports_atomic_replace: self.writable
                && cfg!(any(target_os = "linux", target_os = "macos")),
            max_file_bytes: self.max_file_bytes,
        }
    }

    pub(crate) fn relative_path(&self, path: &EIPPath) -> Result<PathBuf, MountPathError> {
        if path.mount_id != self.mount_id {
            return Err(MountPathError::Denied);
        }
        logical_to_relative(&path.path)
    }

    pub(crate) fn resolve_contained_target(
        &self,
        path: &EIPPath,
    ) -> Result<EIPPath, MountPathError> {
        let canonical = self.resolve_followed_relative(path)?;
        if canonical == Path::new(".") {
            return Err(MountPathError::Denied);
        }
        let mut segments = Vec::new();
        for component in canonical.components() {
            let Component::Normal(segment) = component else {
                return Err(MountPathError::Denied);
            };
            let segment = segment.to_str().ok_or(MountPathError::Denied)?;
            if segment.is_empty() || segment.contains('/') || segment.contains('\0') {
                return Err(MountPathError::Denied);
            }
            segments.push(segment);
        }
        if segments.is_empty() {
            return Err(MountPathError::Denied);
        }
        Ok(EIPPath {
            mount_id: self.mount_id.clone(),
            path: format!("/{}", segments.join("/")),
        })
    }

    pub(crate) fn open_regular(&self, path: &EIPPath) -> Result<OpenedFile, MountPathError> {
        let relative = self.resolve_followed_relative(path)?;
        let file = self
            .root
            .open(&relative)
            .map_err(MountPathError::from_io)?
            .into_std();
        let metadata = file.metadata().map_err(MountPathError::from_io)?;
        if !metadata.is_file() {
            return Err(MountPathError::NotRegular);
        }
        Ok(OpenedFile { file, metadata })
    }

    pub(crate) fn metadata(
        &self,
        path: &EIPPath,
        follow_symlinks: bool,
    ) -> Result<cap_std::fs::Metadata, MountPathError> {
        let relative = if follow_symlinks {
            self.resolve_followed_relative(path)?
        } else {
            self.resolve_nofollow_relative(path)?
        };
        if follow_symlinks {
            self.root.metadata(relative)
        } else {
            self.root.symlink_metadata(relative)
        }
        .map_err(MountPathError::from_io)
    }

    pub(crate) fn resolve_followed_relative(
        &self,
        path: &EIPPath,
    ) -> Result<PathBuf, MountPathError> {
        let relative = self.relative_path(path)?;
        let canonical = self
            .root
            .canonicalize(relative)
            .map_err(MountPathError::from_io)?;
        validate_canonical_relative(&canonical)?;
        self.ensure_unprotected(&canonical)?;
        Ok(canonical)
    }

    pub(crate) fn resolve_nofollow_relative(
        &self,
        path: &EIPPath,
    ) -> Result<PathBuf, MountPathError> {
        let relative = self.relative_path(path)?;
        if relative == Path::new(".") {
            self.ensure_unprotected(&relative)?;
            return Ok(relative);
        }
        let name = relative.file_name().ok_or(MountPathError::Denied)?;
        let parent = relative
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let canonical_parent = self
            .root
            .canonicalize(parent)
            .map_err(MountPathError::from_io)?;
        validate_canonical_relative(&canonical_parent)?;
        self.ensure_unprotected(&canonical_parent)?;
        let resolved = if canonical_parent == Path::new(".") {
            PathBuf::from(name)
        } else {
            canonical_parent.join(name)
        };
        self.ensure_unprotected(&resolved)?;
        Ok(resolved)
    }

    pub(crate) fn ensure_unprotected(&self, relative: &Path) -> Result<(), MountPathError> {
        let relative = relative.strip_prefix(Path::new(".")).unwrap_or(relative);
        if self
            .protected_roots
            .iter()
            .any(|protected| relative == protected || relative.starts_with(protected))
        {
            Err(MountPathError::Denied)
        } else {
            Ok(())
        }
    }

    fn ensure_subtree_unprotected(&self, relative: &Path) -> Result<(), MountPathError> {
        if subtree_contains_protected(&self.protected_roots, relative) {
            Err(MountPathError::Denied)
        } else {
            Ok(())
        }
    }

    pub(crate) fn create_dir(&self, relative: &Path) -> Result<(), MountPathError> {
        self.ensure_unprotected(relative)?;
        self.root
            .create_dir(relative)
            .map_err(MountPathError::from_io)
    }

    pub(crate) fn remove_file(&self, relative: &Path) -> Result<(), MountPathError> {
        self.ensure_unprotected(relative)?;
        self.root
            .remove_file(relative)
            .map_err(MountPathError::from_io)
    }

    pub(crate) fn remove_dir(&self, relative: &Path) -> Result<(), MountPathError> {
        self.ensure_unprotected(relative)?;
        self.ensure_subtree_unprotected(relative)?;
        self.root
            .remove_dir(relative)
            .map_err(MountPathError::from_io)
    }

    pub(crate) fn rename_within(
        &self,
        source: &EIPPath,
        destination: &EIPPath,
        replace: bool,
    ) -> Result<(), MountPathError> {
        let source = self.resolve_nofollow_relative(source)?;
        self.ensure_subtree_unprotected(&source)?;
        if source == Path::new(".") {
            return Err(MountPathError::Denied);
        }
        let source_parent_path = source
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let source_parent = self
            .root
            .open_dir(source_parent_path)
            .map_err(MountPathError::from_io)?;
        let (destination_parent, destination_name) = self.open_parent(destination)?;
        if replace {
            self.root
                .rename(&source, &destination_parent, &destination_name)
                .map_err(MountPathError::from_io)?;
        } else {
            rename_no_replace(
                &self.root,
                &source,
                &destination_parent,
                Path::new(&destination_name),
            )
            .map_err(MountPathError::from_io)?;
        }
        sync_directory(&destination_parent).map_err(|_| MountPathError::UnknownOutcome)?;
        sync_directory(&source_parent).map_err(|_| MountPathError::UnknownOutcome)
    }

    pub(crate) fn open_parent(
        &self,
        path: &EIPPath,
    ) -> Result<(Dir, std::ffi::OsString), MountPathError> {
        let relative = self.relative_path(path)?;
        if relative == Path::new(".") {
            return Err(MountPathError::Denied);
        }
        let name = relative
            .file_name()
            .ok_or(MountPathError::Denied)?
            .to_os_string();
        let parent = relative
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let canonical_parent = self
            .root
            .canonicalize(parent)
            .map_err(MountPathError::from_io)?;
        validate_canonical_relative(&canonical_parent)?;
        self.ensure_unprotected(&canonical_parent)?;
        let destination = if canonical_parent == Path::new(".") {
            PathBuf::from(&name)
        } else {
            canonical_parent.join(&name)
        };
        self.ensure_unprotected(&destination)?;
        let directory = self
            .root
            .open_dir(canonical_parent)
            .map_err(MountPathError::from_io)?;
        Ok((directory, name))
    }

    pub(crate) fn create_candidate(
        self: &Arc<Self>,
        destination: &EIPPath,
    ) -> Result<StagedCandidate, MountPathError> {
        let (parent, destination_name) = self.open_parent(destination)?;
        let reservation = self.staging_quota.reserve_object()?;
        for _ in 0..32 {
            let name = random_candidate_name().map_err(|_| MountPathError::Internal)?;
            let mut options = cap_std::fs::OpenOptions::new();
            options.write(true).read(true).create_new(true);
            match parent.open_with(&name, &options) {
                Ok(file) => {
                    let file = file.into_std();
                    let candidate = StagedCandidate {
                        name,
                        file,
                        mount: Arc::clone(self),
                        destination: destination.clone(),
                        destination_name,
                        parent,
                        reservation,
                        removed: false,
                    };
                    set_private_file_permissions(&candidate.file)
                        .map_err(MountPathError::from_io)?;
                    return Ok(candidate);
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(MountPathError::from_io(error)),
            }
        }
        Err(MountPathError::Internal)
    }

    pub(crate) fn publish_candidate(
        &self,
        candidate: &mut StagedCandidate,
        destination: &EIPPath,
        replace: bool,
    ) -> Result<(), MountPathError> {
        if candidate.mount().mount_id != self.mount_id || candidate.destination != *destination {
            return Err(MountPathError::Denied);
        }
        let source = PathBuf::from(&candidate.name);
        let target = PathBuf::from(&candidate.destination_name);
        let held =
            cap_std::fs::Metadata::from_file(&candidate.file).map_err(MountPathError::from_io)?;
        let named = candidate
            .parent
            .symlink_metadata(&source)
            .map_err(MountPathError::from_io)?;
        if !named.is_file() || named.is_symlink() || !same_cap_file(&held, &named) {
            return Err(MountPathError::Denied);
        }
        if replace {
            candidate
                .parent
                .rename(&source, &candidate.parent, &target)
                .map_err(MountPathError::from_io)?;
        } else {
            rename_no_replace(&candidate.parent, &source, &candidate.parent, &target)
                .map_err(MountPathError::from_io)?;
        }
        candidate.mark_removed();
        let published = candidate
            .parent
            .symlink_metadata(&target)
            .map_err(|_| MountPathError::UnknownOutcome)?;
        if !published.is_file() || published.is_symlink() || !same_cap_file(&held, &published) {
            return Err(MountPathError::UnknownOutcome);
        }
        sync_directory(&candidate.parent).map_err(|_| MountPathError::UnknownOutcome)?;
        Ok(())
    }
}

impl StagedCandidate {
    pub(crate) fn mount(&self) -> &Arc<Mount> {
        &self.mount
    }

    pub(crate) fn reserve_bytes(&mut self, bytes: u64) -> Result<(), MountPathError> {
        self.reservation.reserve_bytes(bytes)
    }

    pub(crate) fn verify_complete(&self, expected_size: u64) -> Result<bool, MountPathError> {
        self.file.sync_all().map_err(MountPathError::from_io)?;
        let metadata = self.file.metadata().map_err(MountPathError::from_io)?;
        Ok(metadata.len() == expected_size && !file_has_multiple_links(&metadata))
    }

    pub(crate) fn mark_removed(&mut self) {
        self.removed = true;
        self.reservation.release();
    }

    fn named_identity_matches(&self) -> Result<bool, MountPathError> {
        let held = cap_std::fs::Metadata::from_file(&self.file).map_err(MountPathError::from_io)?;
        let named = self
            .parent
            .symlink_metadata(&self.name)
            .map_err(MountPathError::from_io)?;
        Ok(named.is_file() && !named.is_symlink() && same_cap_file(&held, &named))
    }

    pub(crate) fn delete(mut self) -> Result<(), MountPathError> {
        if !self.named_identity_matches()? {
            self.removed = true;
            self.reservation.retain();
            return Err(MountPathError::UnknownOutcome);
        }
        let result = self
            .parent
            .remove_file(&self.name)
            .map_err(MountPathError::from_io);
        self.removed = true;
        if result.is_ok() {
            self.reservation.release();
        } else {
            self.reservation.retain();
        }
        result
    }
}

impl Drop for StagedCandidate {
    fn drop(&mut self) {
        if !self.removed {
            if matches!(self.named_identity_matches(), Ok(true))
                && self.parent.remove_file(&self.name).is_ok()
            {
                self.reservation.release();
            } else {
                self.reservation.retain();
            }
        }
    }
}

struct PreparedMount {
    mount_id: String,
    native_root: PathBuf,
    writable: bool,
    allow_command_execution: bool,
    max_file_bytes: u64,
    allowed_operations: BTreeSet<String>,
}

impl PreparedMount {
    fn new(config: &TrustedMountConfig) -> Result<Self, MountInitError> {
        if !valid_mount_id(&config.mount_id) {
            return Err(MountInitError::new(
                "mount_id must use 1..=128 ASCII letters, digits, dot, dash, or underscore",
            ));
        }
        if config.max_file_bytes == 0 {
            return Err(MountInitError::new("mount max_file_bytes must be positive"));
        }
        let native_root = canonical_directory(&config.native_root, "native_root")?;
        let mut allowed_operations = if config.allowed_operations.is_empty() {
            let mut defaults = READ_OPERATIONS
                .iter()
                .map(|value| (*value).to_owned())
                .collect::<BTreeSet<_>>();
            defaults.extend(OPTIONAL_OPERATIONS.iter().map(|value| (*value).to_owned()));
            if config.writable {
                defaults.extend(WRITE_OPERATIONS.iter().map(|value| (*value).to_owned()));
            }
            defaults
        } else {
            config.allowed_operations.iter().cloned().collect()
        };
        let known = READ_OPERATIONS
            .iter()
            .chain(WRITE_OPERATIONS)
            .chain(OPTIONAL_OPERATIONS)
            .copied()
            .collect::<BTreeSet<_>>();
        if allowed_operations
            .iter()
            .any(|operation| !known.contains(operation.as_str()))
        {
            return Err(MountInitError::new(
                "mount allowed_operations contains an unknown operation",
            ));
        }
        if !config.writable {
            for operation in WRITE_OPERATIONS {
                allowed_operations.remove(*operation);
            }
        }
        Ok(Self {
            mount_id: config.mount_id.clone(),
            native_root,
            writable: config.writable,
            allow_command_execution: config.allow_command_execution,
            max_file_bytes: config.max_file_bytes,
            allowed_operations,
        })
    }
}

fn validate_topology(mounts: &[PreparedMount]) -> Result<(), MountInitError> {
    for (index, mount) in mounts.iter().enumerate() {
        for other in mounts.iter().skip(index + 1) {
            if overlaps(&mount.native_root, &other.native_root) {
                return Err(MountInitError::new(
                    "native mount roots must not overlap or alias",
                ));
            }
        }
    }
    Ok(())
}

fn overlaps(left: &Path, right: &Path) -> bool {
    left == right || left.starts_with(right) || right.starts_with(left)
}

fn validate_private_runtime_separation(
    config: &Config,
    mounts: &[PreparedMount],
) -> Result<(), MountInitError> {
    let Some(runtime) = &config.runtime else {
        return Ok(());
    };
    for mount in mounts {
        if mount.native_root.starts_with(runtime.parent()) {
            return Err(MountInitError::new(
                "native mount roots must not be inside the protected envd runtime parent",
            ));
        }
    }
    Ok(())
}

fn protected_roots_for_mount(
    config: &Config,
    mount_root: &Path,
) -> Result<Vec<PathBuf>, MountInitError> {
    let mut protected_paths = Vec::new();
    if let Some(runtime) = &config.runtime {
        protected_paths.push(runtime.parent());
    }
    if let TransportConfig::ReverseWebSocket(websocket) = &config.transport {
        protected_paths.push(websocket.credential_file.as_path());
    }

    let mut roots = Vec::new();
    for protected in protected_paths {
        if !protected.starts_with(mount_root) {
            continue;
        }
        let relative = protected
            .strip_prefix(mount_root)
            .map_err(|_| MountInitError::new("cannot derive protected envd path"))?;
        if relative.as_os_str().is_empty() {
            return Err(MountInitError::new(
                "native mount root must not equal a protected envd path",
            ));
        }
        roots.push(relative.to_path_buf());
    }
    roots.sort();
    roots.dedup();
    Ok(roots)
}

fn logical_to_relative(path: &str) -> Result<PathBuf, MountPathError> {
    if path == "/" {
        return Ok(PathBuf::from("."));
    }
    if !path.starts_with('/') || path.ends_with('/') || path.contains('\0') {
        return Err(MountPathError::Invalid);
    }
    let mut relative = PathBuf::new();
    for segment in path[1..].split('/') {
        if segment.is_empty() {
            return Err(MountPathError::Invalid);
        }
        let mut components = Path::new(segment).components();
        match (components.next(), components.next()) {
            (Some(Component::Normal(value)), None) => relative.push(value),
            _ => return Err(MountPathError::Invalid),
        }
    }
    if relative.as_os_str().is_empty() {
        return Err(MountPathError::Invalid);
    }
    Ok(relative)
}

fn canonical_directory(path: &Path, field: &str) -> Result<PathBuf, MountInitError> {
    if !path.is_absolute() {
        return Err(MountInitError::new(format!(
            "mount {field} must be absolute"
        )));
    }
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| MountInitError::io("inspect mount directory", error))?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(MountInitError::new(format!(
            "mount {field} must identify an existing non-symlink directory"
        )));
    }
    path.canonicalize()
        .map_err(|error| MountInitError::io("canonicalize mount directory", error))
}

fn open_capability_directory(path: &Path) -> Result<Dir, MountInitError> {
    let Some(name) = path.file_name() else {
        return Dir::open_ambient_dir(path, ambient_authority())
            .map_err(|error| MountInitError::io("open mount root", error));
    };
    let parent_path = path
        .parent()
        .ok_or_else(|| MountInitError::new("mount directory must have a parent"))?;
    let parent = Dir::open_ambient_dir(parent_path, ambient_authority())
        .map_err(|error| MountInitError::io("open mount parent", error))?;
    let parent_file = parent.into_std_file();
    let directory =
        cap_primitives::fs::open_dir_nofollow(&parent_file, Path::new(name)).map_err(|error| {
            MountInitError::io("open mount directory without following links", error)
        })?;
    Ok(Dir::from_std_file(directory))
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
fn valid_candidate_name(name: &str) -> bool {
    name.len() == CANDIDATE_PREFIX.len() + CANDIDATE_RANDOM_BYTES * 2
        && name.starts_with(CANDIDATE_PREFIX)
        && name[CANDIDATE_PREFIX.len()..]
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn valid_mount_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
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
pub(crate) enum MountPathError {
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

impl MountPathError {
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
pub(crate) struct MountInitError {
    message: String,
}

impl MountInitError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }

    fn io(action: &str, error: std::io::Error) -> Self {
        Self::new(format!("{action}: {error}"))
    }
}

impl fmt::Display for MountInitError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for MountInitError {}

#[cfg(test)]
mod tests {
    #[cfg(unix)]
    use super::{MountRegistry, open_capability_directory, random_candidate_name};
    use super::{
        logical_to_relative, subtree_contains_protected, valid_candidate_name, valid_mount_id,
    };
    use std::path::{Path, PathBuf};

    #[test]
    fn protected_descendants_prevent_ancestor_subtree_moves() {
        let protected = vec![PathBuf::from("secrets/attachment-token")];
        assert!(subtree_contains_protected(&protected, Path::new("secrets")));
        assert!(subtree_contains_protected(
            &protected,
            Path::new("secrets/attachment-token")
        ));
        assert!(!subtree_contains_protected(&protected, Path::new("public")));
    }

    #[test]
    fn validates_logical_paths_without_normalizing_authority() {
        assert_eq!(
            logical_to_relative("/").expect("root is valid"),
            std::path::Path::new(".")
        );
        assert_eq!(
            logical_to_relative("/one/two").expect("path is valid"),
            std::path::Path::new("one/two")
        );
        for invalid in [
            "",
            "relative",
            "/one/",
            "/one//two",
            "/one/../two",
            "/one/./two",
        ] {
            assert!(logical_to_relative(invalid).is_err(), "{invalid}");
        }
    }

    #[test]
    fn candidate_and_mount_names_are_bounded() {
        assert!(valid_mount_id("workspace-1"));
        assert!(!valid_mount_id(""));
        assert!(!valid_mount_id("bad/name"));
        assert!(valid_candidate_name(
            ".eip-stage-0123456789abcdef0123456789abcdef"
        ));
        assert!(!valid_candidate_name(".eip-stage-../escape"));
    }

    #[cfg(unix)]
    #[test]
    fn capability_root_open_does_not_follow_a_replaced_final_link() {
        use std::{fs, os::unix::fs::symlink};

        let root = std::env::temp_dir().join(
            random_candidate_name()
                .expect("random test directory name")
                .replace(".eip-stage-", "a13n-envd-mount-"),
        );
        let target = root.join("target");
        let selected = root.join("selected");
        fs::create_dir_all(&target).expect("create target directory");
        symlink(&target, &selected).expect("replace selected root with symlink");

        let result = open_capability_directory(&selected);
        fs::remove_file(&selected).expect("remove test symlink");
        fs::remove_dir_all(&root).expect("remove test directory");

        assert!(result.is_err());
    }

    #[cfg(unix)]
    #[test]
    fn failed_candidate_cleanup_retains_only_its_own_quota() {
        use std::fs;

        use crate::{
            config::{Config, TrustedMountConfig},
            eip::EIPPath,
        };

        let root = std::env::temp_dir().join(
            random_candidate_name()
                .expect("random test directory name")
                .replace(".eip-stage-", "a13n-envd-cleanup-"),
        );
        fs::create_dir(&root).expect("create mount root");
        let mut config = Config::for_test("env-cleanup-test");
        config.limits.max_staged_file_objects = 2;
        config.limits.max_staged_file_bytes = 32;
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: root.clone(),
            writable: true,
            allow_command_execution: false,
            max_file_bytes: 32,
            allowed_operations: Vec::new(),
        });
        let mounts = MountRegistry::initialize_scoped(&config).expect("initialize mount");
        let mount = mounts.get("workspace").expect("workspace mount");
        let destination = EIPPath {
            mount_id: "workspace".to_owned(),
            path: "/target.bin".to_owned(),
        };

        let mut failed = mount
            .create_candidate(&destination)
            .expect("create first candidate");
        failed.reserve_bytes(4).expect("reserve candidate bytes");
        let failed_name = failed.name.clone();
        fs::remove_file(root.join(&failed_name)).expect("remove candidate name externally");
        fs::create_dir(root.join(&failed_name)).expect("replace candidate name with directory");
        assert!(failed.delete().is_err());

        let next = mount
            .create_candidate(&destination)
            .expect("one retained charge does not poison the mount");
        drop(next);
        let later = mount
            .create_candidate(&destination)
            .expect("released candidates remain available");
        drop(later);

        fs::remove_dir(root.join(failed_name)).expect("remove replacement directory");
        drop(mounts);
        fs::remove_dir(&root).expect("remove mount root");
    }
}
