use std::{
    collections::{BTreeMap, VecDeque},
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::Arc,
};

use globset::GlobBuilder;
use ignore::gitignore::{Gitignore, GitignoreBuilder};
use regex::Regex;
use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::{
    eip::{
        EIPPath, FileCopyParams, FileFindParams, FileFindResult, FileInfo, FileKind, FileListEntry,
        FileListParams, FileListResult, FileMkdirParams, FileMoveParams, FilePatchTextParams,
        FileReadTextParams, FileReadTextResult, FileRemoveParams, FileSearchMatch,
        FileSearchParams, FileSearchResult, FileStatParams, FileStatResult, FileWriteMode,
        FileWriteTextParams, SearchMode,
    },
    mount::{Mount, MountPathError, MountRegistry, StagedCandidate},
    operation::{OperationInterruption, OperationLedger},
    transfer::file_info,
};

const MAX_TRAVERSAL_ENTRIES: usize = 10_000;
const MAX_TRAVERSAL_DEPTH: u32 = 128;
const MAX_PATTERN_BYTES: usize = 16 * 1024;
const MAX_PATCH_LINE_BYTES: usize = 64 * 1024;
const MAX_PATCH_HUNKS: u64 = 10_000;
const MAX_SEARCH_BYTES_PER_FILE: u64 = 64 * 1024 * 1024;
const MAX_SEARCH_BYTES_PER_OPERATION: u64 = 256 * 1024 * 1024;

type IgnoreStack = Arc<Vec<Arc<IgnoreSpec>>>;
type TraversalEntry = (PathBuf, String, u32, IgnoreStack);
const RESPONSE_RESERVE_BYTES: u64 = 4096;
const MAX_GIT_IGNORE_BYTES: u64 = 1024 * 1024;

struct IgnoreSpec {
    base: PathBuf,
    matcher: Gitignore,
}

#[derive(Clone)]
pub(crate) struct ResourceRegistry {
    inner: Arc<ResourceInner>,
}

struct ResourceInner {
    max_inline_bytes: u64,
    max_response_bytes: u64,
    operations: OperationLedger,
}

#[derive(Clone)]
struct RemovePlanEntry {
    relative: PathBuf,
    directory: bool,
}

#[derive(Clone, Copy)]
struct WalkOptions {
    max_depth: u32,
    include_hidden: bool,
    respect_git_ignore: bool,
}

struct PageCollector<T> {
    offset: u64,
    max_results: usize,
    max_bytes: u64,
    keep: usize,
    matched: u64,
    items: BTreeMap<Vec<u8>, T>,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ResourceError {
    Invalid,
    Denied,
    NotFound,
    Conflict,
    Unsupported,
    Limit,
    OutputLimit,
    Cancelled,
    Timeout,
    UnknownOutcome,
    Io,
    Internal,
    PartialRemove {
        removed_entries: u64,
        cause: Box<ResourceError>,
    },
}

impl ResourceRegistry {
    pub(crate) fn new(config: &crate::config::Config, operations: OperationLedger) -> Self {
        Self {
            inner: Arc::new(ResourceInner {
                max_inline_bytes: config.limits.max_output_preview_bytes,
                max_response_bytes: config.limits.max_response_bytes,
                operations,
            }),
        }
    }

    pub(crate) fn stat(
        &self,
        mounts: &MountRegistry,
        params: &FileStatParams,
    ) -> Result<FileStatResult, ResourceError> {
        self.check_cancelled(&params.context.operation_id)?;
        let mount = read_mount(mounts, &params.path, "stat")?;
        let metadata = mount
            .metadata(&params.path, params.follow_symlinks)
            .map_err(map_mount_error)?;
        Ok(FileStatResult {
            info: cap_file_info(&params.path, &metadata),
        })
    }

    pub(crate) fn read_text(
        &self,
        mounts: &MountRegistry,
        params: &FileReadTextParams,
    ) -> Result<FileReadTextResult, ResourceError> {
        self.check_cancelled(&params.context.operation_id)?;
        let mount = read_mount(mounts, &params.path, "read_text")?;
        let opened = mount.open_regular(&params.path).map_err(map_mount_error)?;
        let info = file_info(&params.path, &opened.metadata);
        let max_bytes = self
            .inner
            .max_inline_bytes
            .min(
                self.inner
                    .max_response_bytes
                    .saturating_sub(RESPONSE_RESERVE_BYTES),
            )
            .max(1);
        let selection = read_text_selection(
            opened.file,
            params.line_offset,
            params.line_limit,
            params.max_line_length,
            max_bytes,
            &self.inner.operations,
            &params.context.operation_id,
        )?;
        Ok(FileReadTextResult {
            info,
            text: selection.text,
            line_offset: params.line_offset,
            lines_read: selection.lines_read,
            has_more: selection.has_more,
            truncated_lines: selection.truncated_lines,
        })
    }

    pub(crate) fn list(
        &self,
        mounts: &MountRegistry,
        params: &FileListParams,
    ) -> Result<FileListResult, ResourceError> {
        if params.max_results == 0 {
            return Err(ResourceError::Limit);
        }
        let mount = read_mount(mounts, &params.path, "list")?;
        let metadata = mount
            .metadata(&params.path, true)
            .map_err(map_mount_error)?;
        if !metadata.is_dir() {
            return Err(ResourceError::Denied);
        }
        let mut page = PageCollector::new(
            params.offset,
            params.max_results,
            self.response_item_limit(),
        );
        let omitted = walk_entries(
            &mount,
            &params.path,
            WalkOptions {
                max_depth: 1,
                include_hidden: params.include_hidden,
                respect_git_ignore: false,
            },
            &self.inner.operations,
            &params.context.operation_id,
            |entry| {
                if params.include_hidden || !is_hidden_path(&entry.relative_path) {
                    let key = entry.relative_path.as_bytes().to_vec();
                    page.push(key, entry)?;
                }
                Ok(true)
            },
        )?;
        let (entries, has_more) = page.finish()?;
        Ok(FileListResult {
            entries,
            offset: params.offset,
            has_more,
            omitted_unrepresentable_entries: omitted,
        })
    }

    pub(crate) fn find(
        &self,
        mounts: &MountRegistry,
        params: &FileFindParams,
    ) -> Result<FileFindResult, ResourceError> {
        if params.pattern.len() > MAX_PATTERN_BYTES || params.max_results == 0 {
            return Err(ResourceError::Limit);
        }
        let matcher = PathMatcher::new(&params.pattern)?;
        let mount = read_mount(mounts, &params.root, "find")?;
        let mut page = PageCollector::new(
            params.offset,
            params.max_results,
            self.response_item_limit(),
        );
        let omitted = walk_entries(
            &mount,
            &params.root,
            WalkOptions {
                max_depth: if params.recursive {
                    MAX_TRAVERSAL_DEPTH
                } else {
                    1
                },
                include_hidden: params.include_hidden,
                respect_git_ignore: params.respect_git_ignore,
            },
            &self.inner.operations,
            &params.context.operation_id,
            |entry| {
                if (params.include_hidden || !is_hidden_path(&entry.relative_path))
                    && matcher.matches(&entry.relative_path)
                    && (params.kinds.is_empty() || params.kinds.contains(&entry.info.kind))
                {
                    let key = entry.relative_path.as_bytes().to_vec();
                    page.push(key, entry)?;
                }
                Ok(true)
            },
        )?;
        let (entries, has_more) = page.finish()?;
        Ok(FileFindResult {
            entries,
            offset: params.offset,
            has_more,
            omitted_unrepresentable_entries: omitted,
        })
    }

    pub(crate) fn search(
        &self,
        mounts: &MountRegistry,
        params: &FileSearchParams,
    ) -> Result<FileSearchResult, ResourceError> {
        if params.query.is_empty()
            || params.query.len() > MAX_PATTERN_BYTES
            || params.include_pattern.len() > MAX_PATTERN_BYTES
            || params.max_results == 0
            || params.max_line_length == 0
            || params.max_file_bytes == 0
            || params.context_lines > 20
            || params.max_matches_per_file == Some(0)
            || params.max_files == Some(0)
        {
            return Err(ResourceError::Invalid);
        }
        let content = ContentMatcher::new(params.mode, &params.query, params.case_sensitive)?;
        let include = PathMatcher::new(&params.include_pattern)?;
        let mount = read_mount(mounts, &params.root, "search")?;
        let mut page = PageCollector::new(
            params.offset,
            params.max_results,
            self.response_item_limit(),
        );
        let mut scanned_bytes = 0_u64;
        let mut scanned_files = 0_u32;
        let max_file_bytes = params.max_file_bytes.min(MAX_SEARCH_BYTES_PER_FILE);
        let omitted = walk_entries(
            &mount,
            &params.root,
            WalkOptions {
                max_depth: MAX_TRAVERSAL_DEPTH,
                include_hidden: params.include_hidden,
                respect_git_ignore: params.respect_git_ignore,
            },
            &self.inner.operations,
            &params.context.operation_id,
            |entry| {
                if entry.info.kind != FileKind::File
                    || (!params.include_hidden && is_hidden_path(&entry.relative_path))
                    || !include.matches(&entry.relative_path)
                    || entry
                        .info
                        .size_bytes
                        .is_some_and(|size| size > max_file_bytes)
                {
                    return Ok(true);
                }
                if params
                    .max_files
                    .is_some_and(|max_files| scanned_files >= max_files)
                {
                    return Err(ResourceError::Limit);
                }
                scanned_files = scanned_files.checked_add(1).ok_or(ResourceError::Limit)?;
                self.check_cancelled(&params.context.operation_id)?;
                let file_matches = match search_file(
                    &mount,
                    &entry.info.path,
                    &content,
                    params.max_line_length,
                    params.context_lines,
                    params.max_matches_per_file,
                    max_file_bytes,
                    &mut scanned_bytes,
                    &self.inner.operations,
                    &params.context.operation_id,
                ) {
                    Ok(matches) => matches,
                    Err(ResourceError::Unsupported) => return Ok(true),
                    Err(error) => return Err(error),
                };
                for matched in file_matches {
                    let mut key = entry.relative_path.as_bytes().to_vec();
                    key.push(0);
                    key.extend_from_slice(&matched.line_number.to_be_bytes());
                    page.push(key, matched)?;
                }
                Ok(true)
            },
        )?;
        let (matches, has_more) = page.finish()?;
        Ok(FileSearchResult {
            matches,
            offset: params.offset,
            has_more,
            omitted_unrepresentable_entries: omitted,
        })
    }

    pub(crate) fn write_text(
        &self,
        mounts: &MountRegistry,
        params: &FileWriteTextParams,
    ) -> Result<(FileInfo, u64), ResourceError> {
        if params.text.contains('\0') {
            return Err(ResourceError::Unsupported);
        }
        let mount = write_mount(mounts, &params.path, "write_text")?;
        let input = params.text.as_bytes();
        let current = observe_regular(&mount, &params.path)?;
        validate_write_mode(params.mode, current.as_ref())?;
        let final_size = if params.mode == FileWriteMode::Append {
            current.as_ref().map_or(0, std::fs::Metadata::len)
        } else {
            0
        }
        .checked_add(input.len() as u64)
        .ok_or(ResourceError::Limit)?;
        if final_size > mount.max_file_bytes {
            return Err(ResourceError::Limit);
        }
        self.check_cancelled(&params.context.operation_id)?;
        let mut candidate = mount
            .create_candidate(&params.path)
            .map_err(map_mount_error)?;
        candidate
            .reserve_bytes(final_size)
            .map_err(map_mount_error)?;
        let mut intended = Sha256::new();
        if params.mode == FileWriteMode::Append {
            let source = mount
                .open_regular(&params.path)
                .map_err(map_mount_error)?
                .file;
            let prefix = read_file_bounded(
                source,
                mount.max_file_bytes,
                &self.inner.operations,
                &params.context.operation_id,
            )?;
            if std::str::from_utf8(&prefix).is_err() || prefix.contains(&0) {
                return Err(ResourceError::Unsupported);
            }
            intended.update(&prefix);
            candidate
                .file
                .write_all(&prefix)
                .map_err(|_| ResourceError::Io)?;
        }
        intended.update(input);
        candidate
            .file
            .write_all(input)
            .map_err(|_| ResourceError::Io)?;
        if let Some(executable) = params.executable {
            set_executable(&candidate.file, executable).map_err(|_| ResourceError::Unsupported)?;
        } else if let Some(metadata) = &current {
            set_permissions_from(&candidate.file, metadata).map_err(|_| ResourceError::Io)?;
        }
        self.check_cancelled(&params.context.operation_id)?;
        let info = commit_candidate(
            &mount,
            &params.path,
            params.mode,
            &mut candidate,
            final_size,
            &format!("{:x}", intended.finalize()),
        )?;
        Ok((info, input.len() as u64))
    }

    pub(crate) fn mkdir(
        &self,
        mounts: &MountRegistry,
        params: &FileMkdirParams,
    ) -> Result<(FileInfo, u64), ResourceError> {
        let mount = write_mount(mounts, &params.path, "mkdir")?;
        let relative = mount.relative_path(&params.path).map_err(map_mount_error)?;
        if relative == Path::new(".") {
            return if params.exist_ok {
                let metadata = mount
                    .metadata(&params.path, false)
                    .map_err(map_mount_error)?;
                Ok((cap_file_info(&params.path, &metadata), 0))
            } else {
                Err(ResourceError::Conflict)
            };
        }
        let mut created = 0_u64;
        let segments = params.path.path[1..].split('/').collect::<Vec<_>>();
        let components = relative.components().collect::<Vec<_>>();
        for (index, _component) in components.iter().enumerate() {
            let prefix_path = EIPPath {
                mount_id: params.path.mount_id.clone(),
                path: format!("/{}", segments[..=index].join("/")),
            };
            let prefix = match mount.resolve_nofollow_relative(&prefix_path) {
                Ok(relative) => relative,
                Err(MountPathError::NotFound) => {
                    let parent_path = if index == 0 {
                        EIPPath {
                            mount_id: params.path.mount_id.clone(),
                            path: "/".to_owned(),
                        }
                    } else {
                        EIPPath {
                            mount_id: params.path.mount_id.clone(),
                            path: format!("/{}", segments[..index].join("/")),
                        }
                    };
                    let parent = mount
                        .resolve_followed_relative(&parent_path)
                        .map_err(map_mount_error)?;
                    let relative = parent.join(segments[index]);
                    mount
                        .ensure_unprotected(&relative)
                        .map_err(map_mount_error)?;
                    relative
                }
                Err(error) => return Err(map_mount_error(error)),
            };
            match mount.root.symlink_metadata(&prefix) {
                Ok(metadata) => {
                    if !metadata.is_dir() || metadata.file_type().is_symlink() {
                        return Err(ResourceError::Conflict);
                    }
                    if index + 1 == components.len() && !params.exist_ok {
                        return Err(ResourceError::Conflict);
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    if !params.parents && index + 1 != components.len() {
                        return Err(ResourceError::NotFound);
                    }
                    mount.create_dir(&prefix).map_err(map_mount_error)?;
                    created += 1;
                }
                Err(_) => return Err(ResourceError::Io),
            }
        }
        let metadata = mount
            .metadata(&params.path, false)
            .map_err(map_mount_error)?;
        Ok((cap_file_info(&params.path, &metadata), created))
    }

    pub(crate) fn patch_text(
        &self,
        mounts: &MountRegistry,
        params: &FilePatchTextParams,
    ) -> Result<(FileInfo, u64), ResourceError> {
        if params.patch_format != "unified_diff" || params.patch.contains('\0') {
            return Err(ResourceError::Invalid);
        }
        let mount = write_mount(mounts, &params.path, "patch_text")?;
        if params.patch.len() as u64 > mount.max_file_bytes {
            return Err(ResourceError::Limit);
        }
        let target = mount
            .resolve_contained_target(&params.path)
            .map_err(map_mount_error)?;
        let opened = mount.open_regular(&target).map_err(map_mount_error)?;
        let bytes = read_file_bounded(
            opened.file,
            mount.max_file_bytes,
            &self.inner.operations,
            &params.context.operation_id,
        )?;
        let source = std::str::from_utf8(&bytes).map_err(|_| ResourceError::Unsupported)?;
        if source.contains('\0') {
            return Err(ResourceError::Unsupported);
        }
        let (result, hunks) = apply_unified_diff(source, &params.patch)?;
        if result.len() as u64 > mount.max_file_bytes {
            return Err(ResourceError::Limit);
        }
        self.check_cancelled(&params.context.operation_id)?;
        let mut candidate = mount.create_candidate(&target).map_err(map_mount_error)?;
        candidate
            .reserve_bytes(result.len() as u64)
            .map_err(map_mount_error)?;
        candidate
            .file
            .write_all(result.as_bytes())
            .map_err(|_| ResourceError::Io)?;
        set_permissions_from(&candidate.file, &opened.metadata).map_err(|_| ResourceError::Io)?;
        let expected_digest = format!("{:x}", Sha256::digest(result.as_bytes()));
        self.check_cancelled(&params.context.operation_id)?;
        let mut info = commit_candidate(
            &mount,
            &target,
            FileWriteMode::Replace,
            &mut candidate,
            result.len() as u64,
            &expected_digest,
        )?;
        info.path = params.path.clone();
        Ok((info, hunks))
    }

    pub(crate) fn copy(
        &self,
        mounts: &MountRegistry,
        params: &FileCopyParams,
    ) -> Result<(FileInfo, u64), ResourceError> {
        let source_mount = mounts
            .get(&params.source.mount_id)
            .ok_or(ResourceError::Denied)?;
        if !source_mount.allows("open_reader") {
            return Err(ResourceError::Denied);
        }
        let destination_mount = write_mount(mounts, &params.destination, "copy")?;
        let mut source = source_mount
            .open_regular(&params.source)
            .map_err(map_mount_error)?;
        let destination = observe_regular(&destination_mount, &params.destination)?;
        if destination.is_some() && !params.replace {
            return Err(ResourceError::Conflict);
        }
        if source.metadata.len() > destination_mount.max_file_bytes {
            return Err(ResourceError::Limit);
        }
        let mut candidate = destination_mount
            .create_candidate(&params.destination)
            .map_err(map_mount_error)?;
        let (bytes, expected_digest) = copy_with_digest(
            &mut source.file,
            &mut candidate,
            destination_mount.max_file_bytes,
            &self.inner.operations,
            &params.context.operation_id,
        )?;
        set_permissions_from(&candidate.file, &source.metadata).map_err(|_| ResourceError::Io)?;
        self.check_cancelled(&params.context.operation_id)?;
        let info = commit_candidate(
            &destination_mount,
            &params.destination,
            if params.replace {
                FileWriteMode::Upsert
            } else {
                FileWriteMode::Create
            },
            &mut candidate,
            bytes,
            &expected_digest,
        )?;
        Ok((info, bytes))
    }

    pub(crate) fn move_path(
        &self,
        mounts: &MountRegistry,
        params: &FileMoveParams,
    ) -> Result<FileInfo, ResourceError> {
        if params.source.mount_id != params.destination.mount_id {
            return Err(ResourceError::Unsupported);
        }
        let mount = write_mount(mounts, &params.source, "move")?;
        let destination = match mount.metadata(&params.destination, false) {
            Ok(metadata) => Some(metadata),
            Err(MountPathError::NotFound) => None,
            Err(error) => return Err(map_mount_error(error)),
        };
        if destination.is_some() && !params.replace {
            return Err(ResourceError::Conflict);
        }
        self.check_cancelled(&params.context.operation_id)?;
        mount
            .rename_within(&params.source, &params.destination, params.replace)
            .map_err(map_mount_error)?;
        let metadata = mount
            .metadata(&params.destination, false)
            .map_err(|_| ResourceError::UnknownOutcome)?;
        Ok(cap_file_info(&params.destination, &metadata))
    }

    pub(crate) fn remove(
        &self,
        mounts: &MountRegistry,
        params: &FileRemoveParams,
    ) -> Result<u64, ResourceError> {
        let mount = write_mount(mounts, &params.path, "remove")?;
        let relative = mount
            .resolve_nofollow_relative(&params.path)
            .map_err(map_mount_error)?;
        if relative == Path::new(".") || params.max_entries == 0 {
            return Err(ResourceError::Denied);
        }
        let metadata = mount
            .metadata(&params.path, false)
            .map_err(map_mount_error)?;
        let info = cap_file_info(&params.path, &metadata);
        if info.kind != params.expected_kind {
            return Err(ResourceError::Conflict);
        }
        if metadata.is_dir() {
            if params.recursive {
                let plan = build_remove_plan(
                    &mount,
                    &relative,
                    params.max_entries.min(MAX_TRAVERSAL_ENTRIES as u64),
                    &self.inner.operations,
                    &params.context.operation_id,
                )?;
                let mut removed = 0_u64;
                for entry in plan {
                    let step = (|| {
                        self.check_cancelled(&params.context.operation_id)?;
                        let current = mount
                            .root
                            .symlink_metadata(&entry.relative)
                            .map_err(|_| ResourceError::Conflict)?;
                        let current_is_directory = current.is_dir() && !current.is_symlink();
                        if current_is_directory != entry.directory {
                            return Err(ResourceError::Conflict);
                        }
                        if entry.directory {
                            mount.remove_dir(&entry.relative).map_err(map_mount_error)?;
                        } else {
                            mount
                                .remove_file(&entry.relative)
                                .map_err(map_mount_error)?;
                        }
                        Ok(())
                    })();
                    if let Err(cause) = step {
                        return Err(if removed == 0 {
                            cause
                        } else {
                            ResourceError::PartialRemove {
                                removed_entries: removed,
                                cause: Box::new(cause),
                            }
                        });
                    }
                    removed += 1;
                }
                Ok(removed)
            } else {
                self.check_cancelled(&params.context.operation_id)?;
                mount.remove_dir(&relative).map_err(map_mount_error)?;
                Ok(1)
            }
        } else {
            self.check_cancelled(&params.context.operation_id)?;
            mount.remove_file(&relative).map_err(map_mount_error)?;
            Ok(1)
        }
    }

    fn response_item_limit(&self) -> u64 {
        self.inner
            .max_inline_bytes
            .min(
                self.inner
                    .max_response_bytes
                    .saturating_sub(RESPONSE_RESERVE_BYTES),
            )
            .max(1)
    }

    fn check_cancelled(&self, operation_id: &str) -> Result<(), ResourceError> {
        check_operation(&self.inner.operations, operation_id)
    }
}

fn read_mount(
    mounts: &MountRegistry,
    path: &EIPPath,
    operation: &str,
) -> Result<Arc<Mount>, ResourceError> {
    mounts
        .get(&path.mount_id)
        .filter(|mount| mount.allows(operation))
        .ok_or(ResourceError::Denied)
}

fn write_mount(
    mounts: &MountRegistry,
    path: &EIPPath,
    operation: &str,
) -> Result<Arc<Mount>, ResourceError> {
    mounts
        .get(&path.mount_id)
        .filter(|mount| mount.writable && mount.allows(operation))
        .ok_or(ResourceError::Denied)
}

fn observe_regular(
    mount: &Arc<Mount>,
    path: &EIPPath,
) -> Result<Option<std::fs::Metadata>, ResourceError> {
    match mount.metadata(path, false) {
        Ok(metadata) if metadata.is_symlink() => return Err(ResourceError::Denied),
        Ok(metadata) if !metadata.is_file() => return Err(ResourceError::Denied),
        Ok(_) => {}
        Err(MountPathError::NotFound) => return Ok(None),
        Err(error) => return Err(map_mount_error(error)),
    }
    let opened = mount.open_regular(path).map_err(map_mount_error)?;
    Ok(Some(opened.metadata))
}

fn validate_write_mode(
    mode: FileWriteMode,
    current: Option<&std::fs::Metadata>,
) -> Result<(), ResourceError> {
    match mode {
        FileWriteMode::Create if current.is_some() => Err(ResourceError::Conflict),
        FileWriteMode::Replace | FileWriteMode::Append if current.is_none() => {
            Err(ResourceError::NotFound)
        }
        _ => Ok(()),
    }
}

fn commit_candidate(
    mount: &Arc<Mount>,
    path: &EIPPath,
    mode: FileWriteMode,
    candidate: &mut StagedCandidate,
    expected_size: u64,
    expected_digest: &str,
) -> Result<FileInfo, ResourceError> {
    if !candidate
        .verify_complete(expected_size)
        .map_err(map_mount_error)?
    {
        return Err(ResourceError::Conflict);
    }
    candidate
        .file
        .seek(SeekFrom::Start(0))
        .map_err(|_| ResourceError::Io)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = candidate
            .file
            .read(&mut buffer)
            .map_err(|_| ResourceError::Io)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    if format!("{:x}", hasher.finalize()) != expected_digest {
        return Err(ResourceError::Conflict);
    }
    let replace = match mode {
        FileWriteMode::Create => false,
        FileWriteMode::Replace | FileWriteMode::Append => {
            validate_write_mode(mode, observe_regular(mount, path)?.as_ref())?;
            true
        }
        FileWriteMode::Upsert => {
            let _ = observe_regular(mount, path)?;
            true
        }
    };
    mount
        .publish_candidate(candidate, path, replace)
        .map_err(map_mount_error)?;
    let opened = mount
        .open_regular(path)
        .map_err(|_| ResourceError::UnknownOutcome)?;
    Ok(file_info(path, &opened.metadata))
}

fn copy_with_digest(
    source: &mut std::fs::File,
    destination: &mut StagedCandidate,
    max_bytes: u64,
    operations: &OperationLedger,
    operation_id: &str,
) -> Result<(u64, String), ResourceError> {
    let mut total = 0_u64;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        check_operation(operations, operation_id)?;
        let read = source.read(&mut buffer).map_err(|_| ResourceError::Io)?;
        if read == 0 {
            break;
        }
        let next = total.checked_add(read as u64).ok_or(ResourceError::Limit)?;
        if next > max_bytes {
            return Err(ResourceError::Limit);
        }
        destination
            .reserve_bytes(read as u64)
            .map_err(map_mount_error)?;
        destination
            .file
            .write_all(&buffer[..read])
            .map_err(|_| ResourceError::Io)?;
        hasher.update(&buffer[..read]);
        total = next;
    }
    Ok((total, format!("{:x}", hasher.finalize())))
}

fn check_operation(operations: &OperationLedger, operation_id: &str) -> Result<(), ResourceError> {
    match operations.interruption(operation_id) {
        Some(OperationInterruption::Cancelled) => Err(ResourceError::Cancelled),
        Some(OperationInterruption::TimedOut) => Err(ResourceError::Timeout),
        None => Ok(()),
    }
}

fn build_remove_plan(
    mount: &Arc<Mount>,
    root: &Path,
    max_entries: u64,
    operations: &OperationLedger,
    operation_id: &str,
) -> Result<Vec<RemovePlanEntry>, ResourceError> {
    if max_entries == 0 {
        return Err(ResourceError::Limit);
    }
    let mut pending = vec![(root.to_path_buf(), 0_u32, false)];
    let mut plan = Vec::new();
    let mut discovered = 0_u64;
    while let Some((relative, depth, expanded)) = pending.pop() {
        check_operation(operations, operation_id)?;
        mount
            .ensure_unprotected(&relative)
            .map_err(map_mount_error)?;
        if expanded {
            plan.push(RemovePlanEntry {
                relative,
                directory: true,
            });
            continue;
        }
        discovered = discovered.checked_add(1).ok_or(ResourceError::Limit)?;
        if discovered > max_entries || discovered > MAX_TRAVERSAL_ENTRIES as u64 {
            return Err(ResourceError::Limit);
        }
        let metadata = mount
            .root
            .symlink_metadata(&relative)
            .map_err(|_| ResourceError::Conflict)?;
        if !metadata.is_dir() || metadata.is_symlink() {
            plan.push(RemovePlanEntry {
                relative,
                directory: false,
            });
            continue;
        }
        let reader = mount
            .root
            .read_dir(&relative)
            .map_err(|_| ResourceError::Io)?;
        let pending_entries = pending.iter().filter(|(_, _, expanded)| !expanded).count() as u64;
        let remaining = max_entries
            .min(MAX_TRAVERSAL_ENTRIES as u64)
            .saturating_sub(discovered)
            .saturating_sub(pending_entries);
        let mut children = Vec::new();
        for entry in reader {
            let entry = entry.map_err(|_| ResourceError::Io)?;
            if children.len() as u64 >= remaining {
                return Err(ResourceError::Limit);
            }
            children.push(relative.join(entry.file_name()));
        }
        if depth >= MAX_TRAVERSAL_DEPTH && !children.is_empty() {
            return Err(ResourceError::Limit);
        }
        children.sort();
        pending.push((relative, depth, true));
        pending.extend(
            children
                .into_iter()
                .rev()
                .map(|child| (child, depth + 1, false)),
        );
    }
    Ok(plan)
}

fn walk_entries<F>(
    mount: &Arc<Mount>,
    root: &EIPPath,
    options: WalkOptions,
    operations: &OperationLedger,
    operation_id: &str,
    mut visit: F,
) -> Result<u64, ResourceError>
where
    F: FnMut(FileListEntry) -> Result<bool, ResourceError>,
{
    let root_relative = mount
        .resolve_followed_relative(root)
        .map_err(map_mount_error)?;
    let mut ignore_specs = Arc::new(Vec::new());
    if options.respect_git_ignore {
        let mut ancestors = root_relative.ancestors().collect::<Vec<_>>();
        ancestors.reverse();
        for ancestor in ancestors {
            if ancestor == root_relative {
                break;
            }
            ignore_specs = extend_ignore_specs(mount, ancestor, ignore_specs)?;
        }
    }
    let (mut pending, mut omitted) = read_children_counting(
        mount,
        &root_relative,
        "",
        1,
        options.include_hidden,
        options.respect_git_ignore,
        ignore_specs,
        MAX_TRAVERSAL_ENTRIES,
    )?;
    pending.reverse();
    let mut discovered = 0_usize;
    while let Some((relative, relative_path, depth, ignore_specs)) = pending.pop() {
        check_operation(operations, operation_id)?;
        discovered = discovered.checked_add(1).ok_or(ResourceError::Limit)?;
        if discovered > MAX_TRAVERSAL_ENTRIES {
            return Err(ResourceError::Limit);
        }
        let path = EIPPath {
            mount_id: root.mount_id.clone(),
            path: join_logical(&root.path, &relative_path),
        };
        let metadata = match mount.metadata(&path, false) {
            Ok(metadata) => metadata,
            Err(MountPathError::Denied) => continue,
            Err(error) => return Err(map_mount_error(error)),
        };
        let directory = metadata.is_dir() && !metadata.file_type().is_symlink();
        let info = cap_file_info(&path, &metadata);
        if !visit(FileListEntry {
            relative_path: relative_path.clone(),
            info,
        })? {
            break;
        }
        if directory && depth < options.max_depth {
            let remaining = MAX_TRAVERSAL_ENTRIES
                .saturating_sub(discovered)
                .saturating_sub(pending.len());
            let (mut children, skipped) = read_children_counting(
                mount,
                &relative,
                &relative_path,
                depth + 1,
                options.include_hidden,
                options.respect_git_ignore,
                ignore_specs,
                remaining,
            )?;
            omitted = omitted.saturating_add(skipped);
            children.reverse();
            pending.extend(children);
        }
    }
    Ok(omitted)
}

#[allow(clippy::too_many_arguments)]
fn read_children_counting(
    mount: &Arc<Mount>,
    directory: &Path,
    prefix: &str,
    depth: u32,
    include_hidden: bool,
    respect_git_ignore: bool,
    ignore_specs: IgnoreStack,
    max_children: usize,
) -> Result<(Vec<TraversalEntry>, u64), ResourceError> {
    mount
        .ensure_unprotected(directory)
        .map_err(map_mount_error)?;
    let ignore_specs = if respect_git_ignore {
        extend_ignore_specs(mount, directory, ignore_specs)?
    } else {
        ignore_specs
    };
    let reader = mount
        .root
        .read_dir(directory)
        .map_err(|_| ResourceError::Io)?;
    let mut children = Vec::new();
    let mut omitted = 0_u64;
    for entry in reader {
        let entry = entry.map_err(|_| ResourceError::Io)?;
        let Ok(name) = entry.file_name().into_string() else {
            omitted = omitted.saturating_add(1);
            continue;
        };
        if name.contains('/') || name.contains('\0') {
            omitted = omitted.saturating_add(1);
            continue;
        }
        if !include_hidden && name.starts_with('.') {
            continue;
        }
        let child_relative = directory.join(&name);
        match mount.ensure_unprotected(&child_relative) {
            Ok(()) => {}
            Err(MountPathError::Denied) => continue,
            Err(error) => return Err(map_mount_error(error)),
        }
        let relative_path = if prefix.is_empty() {
            name
        } else {
            format!("{prefix}/{name}")
        };
        let is_directory = entry.file_type().map_err(|_| ResourceError::Io)?.is_dir();
        if respect_git_ignore && is_git_ignored(&child_relative, is_directory, &ignore_specs) {
            continue;
        }
        if children.len() >= max_children {
            return Err(ResourceError::Limit);
        }
        children.push((child_relative, relative_path, depth, ignore_specs.clone()));
    }
    children.sort_by(|left, right| left.1.as_bytes().cmp(right.1.as_bytes()));
    Ok((children, omitted))
}

fn extend_ignore_specs(
    mount: &Arc<Mount>,
    directory: &Path,
    ignore_specs: IgnoreStack,
) -> Result<IgnoreStack, ResourceError> {
    let path = directory.join(".gitignore");
    let Ok(file) = mount.root.open(&path) else {
        return Ok(ignore_specs);
    };
    let mut bytes = Vec::new();
    file.take(MAX_GIT_IGNORE_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| ResourceError::Io)?;
    if bytes.len() as u64 > MAX_GIT_IGNORE_BYTES {
        return Ok(ignore_specs);
    }
    let text = String::from_utf8_lossy(&bytes);
    let mut builder = GitignoreBuilder::new("");
    for line in text.lines() {
        builder
            .add_line(None, line)
            .map_err(|_| ResourceError::Invalid)?;
    }
    let matcher = builder.build().map_err(|_| ResourceError::Invalid)?;
    let mut extended = Vec::with_capacity(ignore_specs.len() + 1);
    extended.extend(ignore_specs.iter().cloned());
    extended.push(Arc::new(IgnoreSpec {
        base: directory.to_path_buf(),
        matcher,
    }));
    Ok(Arc::new(extended))
}

fn is_git_ignored(path: &Path, is_directory: bool, specs: &IgnoreStack) -> bool {
    if path.components().any(|part| part.as_os_str() == ".git") {
        return true;
    }
    let mut ignored = false;
    for spec in specs.iter() {
        let Ok(candidate) = path.strip_prefix(&spec.base) else {
            continue;
        };
        let matched = spec.matcher.matched(candidate, is_directory);
        if matched.is_ignore() {
            ignored = true;
        } else if matched.is_whitelist() {
            ignored = false;
        }
    }
    ignored
}

fn join_logical(root: &str, relative: &str) -> String {
    if root == "/" {
        format!("/{relative}")
    } else {
        format!("{root}/{relative}")
    }
}

struct TextSelection {
    text: String,
    lines_read: u64,
    has_more: bool,
    truncated_lines: Vec<u64>,
}

struct LinePreview {
    text: String,
    terminated: bool,
    truncated: bool,
}

fn read_file_bounded(
    mut file: std::fs::File,
    max_bytes: u64,
    operations: &OperationLedger,
    operation_id: &str,
) -> Result<Vec<u8>, ResourceError> {
    let mut bytes = Vec::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        check_operation(operations, operation_id)?;
        let read = file.read(&mut buffer).map_err(|_| ResourceError::Io)?;
        if read == 0 {
            return Ok(bytes);
        }
        let next = (bytes.len() as u64)
            .checked_add(read as u64)
            .ok_or(ResourceError::Limit)?;
        if next > max_bytes {
            return Err(ResourceError::Limit);
        }
        bytes.extend_from_slice(&buffer[..read]);
    }
}

fn read_text_selection(
    file: std::fs::File,
    line_offset: u64,
    line_limit: u64,
    max_line_length: u64,
    max_bytes: u64,
    operations: &OperationLedger,
    operation_id: &str,
) -> Result<TextSelection, ResourceError> {
    let max_chars = usize::try_from(max_line_length).map_err(|_| ResourceError::Limit)?;
    let mut reader = BufReader::new(file);
    let mut text = String::new();
    let mut line_index = 0_u64;
    let mut lines_read = 0_u64;
    let mut truncated_lines = Vec::new();
    let mut has_more = false;
    loop {
        if line_index >= line_offset && lines_read == line_limit {
            check_operation(operations, operation_id)?;
            has_more = !reader.fill_buf().map_err(|_| ResourceError::Io)?.is_empty();
            break;
        }
        let Some(line) = read_line_preview(&mut reader, max_chars, operations, operation_id)?
        else {
            break;
        };
        if line_index < line_offset {
            line_index += 1;
            continue;
        }
        let required = line.text.len() as u64 + u64::from(line.terminated);
        if (text.len() as u64).saturating_add(required) > max_bytes {
            if lines_read == 0 {
                return Err(ResourceError::OutputLimit);
            }
            has_more = true;
            break;
        }
        text.push_str(&line.text);
        if line.terminated {
            text.push('\n');
        }
        if line.truncated {
            truncated_lines.push(line_index + 1);
        }
        line_index += 1;
        lines_read += 1;
    }
    Ok(TextSelection {
        text,
        lines_read,
        has_more,
        truncated_lines,
    })
}

fn read_line_preview<R: BufRead>(
    reader: &mut R,
    max_chars: usize,
    operations: &OperationLedger,
    operation_id: &str,
) -> Result<Option<LinePreview>, ResourceError> {
    let capture_limit = max_chars
        .checked_mul(4)
        .and_then(|value| value.checked_add(4))
        .ok_or(ResourceError::Limit)?;
    let mut captured = Vec::new();
    let mut saw_line = false;
    let mut terminated = false;
    let mut overflow = false;
    let mut utf8_tail = Vec::new();
    loop {
        check_operation(operations, operation_id)?;
        let available = reader.fill_buf().map_err(|_| ResourceError::Io)?;
        if available.is_empty() {
            break;
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let consumed = newline.map_or(available.len(), |position| position + 1);
        let content_end = newline.unwrap_or(consumed);
        let content = &available[..content_end];
        if content.contains(&0) {
            return Err(ResourceError::Unsupported);
        }
        let mut validation = std::mem::take(&mut utf8_tail);
        validation.extend_from_slice(content);
        match std::str::from_utf8(&validation) {
            Ok(_) => {}
            Err(error) if error.error_len().is_none() => {
                utf8_tail.extend_from_slice(&validation[error.valid_up_to()..]);
            }
            Err(_) => return Err(ResourceError::Unsupported),
        }
        if newline.is_some() && !utf8_tail.is_empty() {
            return Err(ResourceError::Unsupported);
        }
        saw_line |= !content.is_empty() || newline.is_some();
        let remaining = capture_limit.saturating_sub(captured.len());
        let retained = remaining.min(content.len());
        captured.extend_from_slice(&content[..retained]);
        overflow |= retained < content.len();
        reader.consume(consumed);
        if newline.is_some() {
            terminated = true;
            break;
        }
    }
    if !utf8_tail.is_empty() {
        return Err(ResourceError::Unsupported);
    }
    if !saw_line {
        return Ok(None);
    }
    let valid_length = match std::str::from_utf8(&captured) {
        Ok(_) => captured.len(),
        Err(error) if overflow && error.error_len().is_none() => error.valid_up_to(),
        Err(_) => return Err(ResourceError::Unsupported),
    };
    let valid =
        std::str::from_utf8(&captured[..valid_length]).map_err(|_| ResourceError::Unsupported)?;
    let end = valid
        .char_indices()
        .nth(max_chars)
        .map_or(valid.len(), |(index, _)| index);
    Ok(Some(LinePreview {
        text: valid[..end].to_owned(),
        terminated,
        truncated: overflow || end < valid.len(),
    }))
}

impl<T: Serialize> PageCollector<T> {
    fn new(offset: u64, max_results: u32, max_bytes: u64) -> Self {
        let keep = offset
            .saturating_add(max_results as u64)
            .saturating_add(1)
            .min(MAX_TRAVERSAL_ENTRIES as u64) as usize;
        Self {
            offset,
            max_results: max_results as usize,
            max_bytes,
            keep,
            matched: 0,
            items: BTreeMap::new(),
        }
    }

    fn push(&mut self, key: Vec<u8>, item: T) -> Result<(), ResourceError> {
        self.matched = self.matched.checked_add(1).ok_or(ResourceError::Limit)?;
        if self.matched > MAX_TRAVERSAL_ENTRIES as u64 {
            return Err(ResourceError::Limit);
        }
        if self.keep == 0 {
            return Ok(());
        }
        self.items.insert(key, item);
        if self.items.len() > self.keep {
            self.items.pop_last();
        }
        Ok(())
    }

    fn finish(self) -> Result<(Vec<T>, bool), ResourceError> {
        let mut selected = Vec::new();
        let mut encoded = 0_u64;
        let skip = usize::try_from(self.offset).unwrap_or(usize::MAX);
        for item in self.items.into_values().skip(skip) {
            if selected.len() >= self.max_results {
                break;
            }
            let size = serde_json::to_vec(&item)
                .map_err(|_| ResourceError::Internal)?
                .len() as u64;
            if encoded.saturating_add(size) > self.max_bytes {
                if selected.is_empty() {
                    return Err(ResourceError::OutputLimit);
                }
                break;
            }
            encoded += size;
            selected.push(item);
        }
        let consumed = self.offset.saturating_add(selected.len() as u64);
        Ok((selected, self.matched > consumed))
    }
}

struct PathMatcher {
    matcher: globset::GlobMatcher,
    basename: bool,
}

impl PathMatcher {
    fn new(pattern: &str) -> Result<Self, ResourceError> {
        validate_glob_pattern(pattern)?;
        let anchored = pattern.starts_with('/');
        let normalized = pattern.strip_prefix('/').unwrap_or(pattern);
        GlobBuilder::new(normalized)
            .literal_separator(true)
            .build()
            .map_err(|_| ResourceError::Invalid)
            .map(|glob| Self {
                matcher: glob.compile_matcher(),
                basename: !anchored && !normalized.contains('/'),
            })
    }

    fn matches(&self, path: &str) -> bool {
        let candidate = if self.basename {
            path.rsplit('/').next().unwrap_or(path)
        } else {
            path
        };
        self.matcher.is_match(candidate)
    }
}

fn is_hidden_path(path: &str) -> bool {
    path.split('/').any(|component| component.starts_with('.'))
}

fn validate_glob_pattern(pattern: &str) -> Result<(), ResourceError> {
    if pattern.strip_prefix('/').unwrap_or(pattern).is_empty()
        || pattern.contains(['{', '}', '\\'])
        || pattern
            .split('/')
            .any(|segment| segment.contains("**") && segment != "**")
    {
        return Err(ResourceError::Invalid);
    }
    Ok(())
}

enum ContentMatcher {
    Literal(String),
    Regex(Regex),
}

impl ContentMatcher {
    fn new(mode: SearchMode, query: &str, case_sensitive: bool) -> Result<Self, ResourceError> {
        match mode {
            SearchMode::Literal if case_sensitive => Ok(Self::Literal(query.to_owned())),
            SearchMode::Literal => Regex::new(&format!("(?i:{})", regex::escape(query)))
                .map(Self::Regex)
                .map_err(|_| ResourceError::Invalid),
            SearchMode::Regex => {
                let pattern = if case_sensitive {
                    query.to_owned()
                } else {
                    format!("(?i:{query})")
                };
                Regex::new(&pattern)
                    .map(Self::Regex)
                    .map_err(|_| ResourceError::Invalid)
            }
        }
    }

    fn matches(&self, line: &str) -> bool {
        match self {
            Self::Literal(query) => line.contains(query),
            Self::Regex(regex) => regex.is_match(line),
        }
    }
}

fn truncate_chars(value: &str, max_chars: usize) -> (&str, bool) {
    match value.char_indices().nth(max_chars) {
        Some((end, _)) => (&value[..end], true),
        None => (value, false),
    }
}

struct PendingSearchMatch {
    line_number: u64,
    preview: String,
    preview_truncated: bool,
    context: String,
    context_start_line: u64,
    remaining_context: u32,
}

#[allow(clippy::too_many_arguments)]
fn search_file(
    mount: &Arc<Mount>,
    path: &EIPPath,
    matcher: &ContentMatcher,
    max_line_length: u64,
    context_lines: u32,
    max_matches_per_file: Option<u32>,
    max_file_bytes: u64,
    operation_scanned: &mut u64,
    operations: &OperationLedger,
    operation_id: &str,
) -> Result<Vec<FileSearchMatch>, ResourceError> {
    let opened = mount.open_regular(path).map_err(map_mount_error)?;
    let requested_chars = usize::try_from(max_line_length).map_err(|_| ResourceError::Limit)?;
    let match_limit = max_matches_per_file
        .unwrap_or(MAX_TRAVERSAL_ENTRIES as u32)
        .min(MAX_TRAVERSAL_ENTRIES as u32) as usize;
    let mut reader = BufReader::new(opened.file);
    let mut file_scanned = 0_u64;
    let mut line_number = 0_u64;
    let mut matched = 0_usize;
    let mut matches = Vec::new();
    let mut before = VecDeque::<(u64, String)>::with_capacity(context_lines as usize);
    let mut pending = Vec::<PendingSearchMatch>::new();
    let mut reached_match_limit = false;
    loop {
        check_operation(operations, operation_id)?;
        let remaining = max_file_bytes
            .saturating_sub(file_scanned)
            .min(MAX_SEARCH_BYTES_PER_OPERATION.saturating_sub(*operation_scanned));
        let Some(mut line_bytes) = read_bounded_search_line(&mut reader, remaining)? else {
            break;
        };
        let read = u64::try_from(line_bytes.len()).map_err(|_| ResourceError::Limit)?;
        file_scanned = file_scanned.checked_add(read).ok_or(ResourceError::Limit)?;
        *operation_scanned = operation_scanned
            .checked_add(read)
            .ok_or(ResourceError::Limit)?;
        if line_bytes.contains(&0) {
            return Err(ResourceError::Unsupported);
        }
        let terminated = line_bytes.last() == Some(&b'\n');
        if terminated {
            line_bytes.pop();
        }
        let line = std::str::from_utf8(&line_bytes).map_err(|_| ResourceError::Unsupported)?;
        line_number = line_number.checked_add(1).ok_or(ResourceError::Limit)?;
        let (preview, preview_truncated) = truncate_chars(line, requested_chars);
        let mut rendered = preview.to_owned();
        if terminated {
            rendered.push('\n');
        }

        let mut remaining_pending = Vec::with_capacity(pending.len());
        for mut item in pending {
            item.context.push_str(&rendered);
            item.remaining_context -= 1;
            if item.remaining_context == 0 {
                matches.push(FileSearchMatch {
                    path: path.clone(),
                    line_number: item.line_number,
                    preview: item.preview,
                    preview_truncated: item.preview_truncated,
                    context: item.context,
                    context_start_line: item.context_start_line,
                });
            } else {
                remaining_pending.push(item);
            }
        }
        pending = remaining_pending;
        if reached_match_limit && pending.is_empty() {
            break;
        }

        if matcher.matches(line) && matched < match_limit {
            let context_start_line = before.front().map_or(line_number, |(number, _)| *number);
            let mut context = String::new();
            for (_, value) in &before {
                context.push_str(value);
            }
            context.push_str(&rendered);
            let item = PendingSearchMatch {
                line_number,
                preview: preview.to_owned(),
                preview_truncated,
                context,
                context_start_line,
                remaining_context: context_lines,
            };
            if context_lines == 0 {
                matches.push(FileSearchMatch {
                    path: path.clone(),
                    line_number: item.line_number,
                    preview: item.preview,
                    preview_truncated: item.preview_truncated,
                    context: item.context,
                    context_start_line: item.context_start_line,
                });
            } else {
                pending.push(item);
            }
            matched += 1;
            reached_match_limit = matched == match_limit;
        }
        if context_lines > 0 {
            if before.len() == context_lines as usize {
                before.pop_front();
            }
            before.push_back((line_number, rendered));
        }
    }
    for item in pending {
        matches.push(FileSearchMatch {
            path: path.clone(),
            line_number: item.line_number,
            preview: item.preview,
            preview_truncated: item.preview_truncated,
            context: item.context,
            context_start_line: item.context_start_line,
        });
    }
    Ok(matches)
}

fn read_bounded_search_line<R: BufRead>(
    reader: &mut R,
    max_bytes: u64,
) -> Result<Option<Vec<u8>>, ResourceError> {
    let max_bytes = usize::try_from(max_bytes).unwrap_or(usize::MAX);
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().map_err(|_| ResourceError::Io)?;
        if available.is_empty() {
            return if line.is_empty() {
                Ok(None)
            } else {
                Ok(Some(line))
            };
        }
        let consumed = available
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(available.len(), |index| index + 1);
        if consumed > max_bytes.saturating_sub(line.len()) {
            return Err(ResourceError::Limit);
        }
        line.extend_from_slice(&available[..consumed]);
        reader.consume(consumed);
        if line.last() == Some(&b'\n') {
            return Ok(Some(line));
        }
    }
}

fn apply_unified_diff(source: &str, patch: &str) -> Result<(String, u64), ResourceError> {
    let source_lines = source.split_inclusive('\n').collect::<Vec<_>>();
    let patch_lines = patch.split_inclusive('\n').collect::<Vec<_>>();
    let mut index = 0_usize;
    if patch_lines
        .get(index)
        .is_some_and(|line| line.starts_with("--- "))
    {
        index += 1;
    }
    if patch_lines
        .get(index)
        .is_some_and(|line| line.starts_with("+++ "))
    {
        index += 1;
    }
    let mut output = String::new();
    let mut output_lines = 0_usize;
    let mut source_index = 0_usize;
    let mut hunks = 0_u64;
    while index < patch_lines.len() {
        if hunks >= MAX_PATCH_HUNKS {
            return Err(ResourceError::Limit);
        }
        let header = patch_lines[index].trim_end_matches(['\r', '\n']);
        let (old_start, old_count, new_start, new_count) = parse_hunk_header(header)?;
        let target = if old_count == 0 {
            old_start
        } else {
            old_start.checked_sub(1).ok_or(ResourceError::Invalid)?
        };
        if target < source_index || target > source_lines.len() {
            return Err(ResourceError::Conflict);
        }
        output.extend(source_lines[source_index..target].iter().copied());
        output_lines += target - source_index;
        let new_target = if new_count == 0 {
            new_start
        } else {
            new_start.checked_sub(1).ok_or(ResourceError::Invalid)?
        };
        if new_target != output_lines {
            return Err(ResourceError::Invalid);
        }
        source_index = target;
        index += 1;
        hunks += 1;
        let mut observed_old = 0_usize;
        let mut observed_new = 0_usize;
        while index < patch_lines.len() && !patch_lines[index].starts_with("@@ ") {
            let line = patch_lines[index];
            if line.len() > MAX_PATCH_LINE_BYTES || line.starts_with("\\ No newline at end of file")
            {
                return Err(ResourceError::Invalid);
            }
            let (tag, mut value) = line.split_at(1);
            let no_newline = patch_lines.get(index + 1).is_some_and(|next| {
                next.trim_end_matches(['\r', '\n']) == "\\ No newline at end of file"
            });
            if no_newline {
                value = value
                    .strip_suffix("\r\n")
                    .or_else(|| value.strip_suffix('\n'))
                    .ok_or(ResourceError::Invalid)?;
            }
            match tag {
                " " => {
                    if source_lines.get(source_index).copied() != Some(value) {
                        return Err(ResourceError::Conflict);
                    }
                    output.push_str(value);
                    output_lines += 1;
                    source_index += 1;
                    observed_old += 1;
                    observed_new += 1;
                }
                "-" => {
                    if source_lines.get(source_index).copied() != Some(value) {
                        return Err(ResourceError::Conflict);
                    }
                    source_index += 1;
                    observed_old += 1;
                }
                "+" => {
                    output.push_str(value);
                    output_lines += 1;
                    observed_new += 1;
                }
                _ => return Err(ResourceError::Invalid),
            }
            if observed_old > old_count || observed_new > new_count {
                return Err(ResourceError::Invalid);
            }
            index += if no_newline { 2 } else { 1 };
        }
        if observed_old != old_count || observed_new != new_count {
            return Err(ResourceError::Invalid);
        }
    }
    if hunks == 0 {
        return Err(ResourceError::Invalid);
    }
    output.extend(source_lines[source_index..].iter().copied());
    Ok((output, hunks))
}

fn parse_hunk_header(header: &str) -> Result<(usize, usize, usize, usize), ResourceError> {
    let ranges = header
        .strip_prefix("@@ -")
        .and_then(|rest| rest.split_once(" @@"))
        .ok_or(ResourceError::Invalid)?
        .0;
    let (old, new) = ranges.split_once(" +").ok_or(ResourceError::Invalid)?;
    let (old_start, old_count) = parse_hunk_range(old)?;
    let (new_start, new_count) = parse_hunk_range(new)?;
    Ok((old_start, old_count, new_start, new_count))
}

fn parse_hunk_range(range: &str) -> Result<(usize, usize), ResourceError> {
    let (start, count) = match range.split_once(',') {
        Some((start, count)) => (start, count),
        None => (range, "1"),
    };
    let start = start.parse::<usize>().map_err(|_| ResourceError::Invalid)?;
    let count = count.parse::<usize>().map_err(|_| ResourceError::Invalid)?;
    if (count > 0 && start == 0) || count > MAX_TRAVERSAL_ENTRIES {
        return Err(ResourceError::Invalid);
    }
    Ok((start, count))
}

#[cfg(unix)]
fn set_executable(file: &std::fs::File, executable: bool) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mut permissions = file.metadata()?.permissions();
    let mode = permissions.mode();
    permissions.set_mode(if executable {
        mode | 0o100
    } else {
        mode & !0o111
    });
    file.set_permissions(permissions)
}

#[cfg(not(unix))]
fn set_executable(_file: &std::fs::File, executable: bool) -> std::io::Result<()> {
    if executable {
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "executable bits unsupported",
        ))
    } else {
        Ok(())
    }
}

#[cfg(unix)]
fn set_permissions_from(file: &std::fs::File, metadata: &std::fs::Metadata) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    file.set_permissions(std::fs::Permissions::from_mode(
        metadata.permissions().mode() & 0o777,
    ))
}

#[cfg(not(unix))]
fn set_permissions_from(
    _file: &std::fs::File,
    _metadata: &std::fs::Metadata,
) -> std::io::Result<()> {
    Ok(())
}

fn cap_file_info(path: &EIPPath, metadata: &cap_std::fs::Metadata) -> FileInfo {
    let kind = if metadata.is_file() {
        FileKind::File
    } else if metadata.is_dir() {
        FileKind::Directory
    } else if metadata.is_symlink() {
        FileKind::Symlink
    } else {
        FileKind::Other
    };
    FileInfo {
        path: path.clone(),
        kind,
        size_bytes: metadata.is_file().then_some(metadata.len()),
        modified_at: metadata
            .modified()
            .ok()
            .map(cap_std::time::SystemTime::into_std)
            .map(chrono::DateTime::from),
        executable: cap_executable(metadata),
    }
}

#[cfg(unix)]
fn cap_executable(metadata: &cap_std::fs::Metadata) -> Option<bool> {
    use cap_std::fs::MetadataExt;
    metadata.is_file().then(|| metadata.mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn cap_executable(metadata: &cap_std::fs::Metadata) -> Option<bool> {
    metadata.is_file().then_some(false)
}

fn map_mount_error(error: MountPathError) -> ResourceError {
    match error {
        MountPathError::Invalid => ResourceError::Invalid,
        MountPathError::Denied | MountPathError::NotRegular => ResourceError::Denied,
        MountPathError::NotFound => ResourceError::NotFound,
        MountPathError::AlreadyExists => ResourceError::Conflict,
        MountPathError::Quota => ResourceError::Limit,
        MountPathError::Unsupported => ResourceError::Unsupported,
        MountPathError::UnknownOutcome => ResourceError::UnknownOutcome,
        MountPathError::Io => ResourceError::Io,
        MountPathError::Internal => ResourceError::Internal,
    }
}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        io::{BufReader, Cursor, Write},
        path::PathBuf,
        time::Duration,
    };

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use sha2::{Digest, Sha256};

    use crate::{
        config::{Config, TrustedMountConfig},
        eip::{
            EIPCallContext, EIPPath, FileFindParams, FileKind, FileListParams, FileReadTextParams,
            FileRemoveParams, FileSearchParams, FileStatParams, FileWriteMode, FileWriteTextParams,
            SearchMode,
        },
        mount::MountRegistry,
        operation::{OperationLedger, random_selector},
    };
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use crate::{
        eip::{FileCopyParams, FileMkdirParams, FileMoveParams, FilePatchTextParams},
        runtime::RuntimeState,
    };

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use super::commit_candidate;
    use super::{
        ResourceError, ResourceRegistry, apply_unified_diff, join_logical, read_bounded_search_line,
    };

    struct TempTree(PathBuf);

    impl TempTree {
        fn new() -> Self {
            let path = std::env::temp_dir().join(
                random_selector("a13n-envd-resource-test").expect("random temporary directory"),
            );
            fs::create_dir(&path).expect("creates temporary directory");
            Self(path)
        }

        fn child(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for TempTree {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    struct Fixture {
        _tree: TempTree,
        native: PathBuf,
        mounts: MountRegistry,
        resources: ResourceRegistry,
    }

    impl Fixture {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        fn new() -> Self {
            Self::with_mount(true, 64 * 1024 * 1024, 64)
        }

        fn read_only() -> Self {
            Self::with_mount(false, 64 * 1024 * 1024, 64)
        }

        #[cfg(any(target_os = "linux", target_os = "macos"))]
        fn with_staging_limits(max_bytes: u64, max_objects: u64) -> Self {
            Self::with_mount(true, max_bytes, max_objects)
        }

        fn with_mount(writable: bool, max_bytes: u64, max_objects: u64) -> Self {
            let tree = TempTree::new();
            let native = tree.child("native");
            fs::create_dir(&native).expect("native root");
            let mut config = Config::for_test("env-resource-test");
            config.limits.max_staged_file_bytes = max_bytes;
            config.limits.max_staged_file_objects = max_objects;
            config.mounts.push(TrustedMountConfig {
                mount_id: "workspace".to_owned(),
                native_root: native.clone(),
                writable,
                allow_command_execution: false,
                max_file_bytes: 1024 * 1024,
                allowed_operations: Vec::new(),
            });
            let operations = OperationLedger::new(
                config.environment_id.clone(),
                7,
                256,
                Duration::from_secs(60),
                Duration::from_secs(60),
            );
            let mounts = MountRegistry::initialize_scoped(&config).expect("mounts initialize");
            let resources = ResourceRegistry::new(&config, operations);
            Self {
                _tree: tree,
                native,
                mounts,
                resources,
            }
        }
    }

    fn context(operation_id: &str) -> EIPCallContext {
        EIPCallContext {
            operation_id: operation_id.to_owned(),
            timeout_ms: None,
        }
    }

    fn path(value: &str) -> EIPPath {
        EIPPath {
            mount_id: "workspace".to_owned(),
            path: value.to_owned(),
        }
    }

    #[test]
    fn applies_strict_unified_diff() {
        let cases = [
            (
                "one\ntwo\n",
                "--- a/file\n+++ b/file\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n",
                "one\nthree\n",
            ),
            ("a\rb\n", "@@ -1 +1 @@\n-a\rb\n+x\n", "x\n"),
            ("a\u{2028}b\n", "@@ -1 +1 @@\n-a\u{2028}b\n+x\n", "x\n"),
            (
                "tail",
                "@@ -1 +1 @@\n-tail\n\\ No newline at end of file\n+done\n\\ No newline at end of file",
                "done",
            ),
        ];
        for (source, patch, expected) in cases {
            let (result, hunks) = apply_unified_diff(source, patch).expect("patch applies");
            assert_eq!(result, expected);
            assert_eq!(hunks, 1);
        }
    }

    #[test]
    fn joins_mount_relative_paths() {
        assert_eq!(join_logical("/", "a/b"), "/a/b");
        assert_eq!(join_logical("/root", "a"), "/root/a");
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn resource_candidates_enforce_and_release_shared_staging_quota() {
        let fixture = Fixture::with_staging_limits(8, 1);
        let oversized = fixture.resources.write_text(
            &fixture.mounts,
            &FileWriteTextParams {
                context: context("oversized-staging-write"),
                path: path("/oversized.txt"),
                mode: FileWriteMode::Create,
                text: "123456789".to_owned(),
                executable: None,
            },
        );
        assert_eq!(oversized, Err(ResourceError::Limit));
        assert!(!fixture.native.join("oversized.txt").exists());

        let written = fixture
            .resources
            .write_text(
                &fixture.mounts,
                &FileWriteTextParams {
                    context: context("bounded-staging-write"),
                    path: path("/bounded.txt"),
                    mode: FileWriteMode::Create,
                    text: "12345678".to_owned(),
                    executable: None,
                },
            )
            .expect("failed candidate deletion returns quota to the shared manager");
        assert_eq!(written.1, 8);
        assert_eq!(
            fs::read_to_string(fixture.native.join("bounded.txt")).expect("bounded destination"),
            "12345678"
        );
    }

    #[test]
    fn bounded_search_line_rejects_before_reading_an_unterminated_overflow() {
        let mut reader = BufReader::new(Cursor::new(b"123456789"));
        assert_eq!(
            read_bounded_search_line(&mut reader, 8),
            Err(ResourceError::Limit)
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn candidate_path_substitution_cannot_publish_unverified_content() {
        let fixture = Fixture::new();
        let mount = fixture.mounts.get("workspace").expect("workspace mount");
        let destination = path("/verified.bin");
        let expected = b"verified";
        let mut candidate = mount
            .create_candidate(&destination)
            .expect("create candidate");
        candidate
            .reserve_bytes(expected.len() as u64)
            .expect("reserve candidate bytes");
        candidate
            .file
            .write_all(expected)
            .expect("write verified candidate");
        let candidate_path = fixture.native.join(&candidate.name);
        let displaced_path = fixture.native.join("displaced-candidate");
        fs::rename(&candidate_path, &displaced_path).expect("displace verified candidate name");
        fs::write(&candidate_path, b"substituted").expect("substitute staging pathname");

        assert_eq!(
            commit_candidate(
                &mount,
                &destination,
                FileWriteMode::Create,
                &mut candidate,
                expected.len() as u64,
                &format!("{:x}", Sha256::digest(expected)),
            ),
            Err(ResourceError::Denied)
        );
        assert!(!fixture.native.join("verified.bin").exists());
        drop(candidate);
        assert_eq!(fs::read(&candidate_path).unwrap(), b"substituted");

        fs::remove_file(candidate_path).expect("remove substituted name");
        fs::remove_file(displaced_path).expect("remove displaced candidate");
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn publication_respects_create_and_upsert_during_external_writes() {
        let fixture = Fixture::new();
        let mount = fixture.mounts.get("workspace").expect("workspace mount");

        let upsert_path = path("/upsert.bin");
        let upsert_bytes = b"envd-upsert";
        let mut upsert = mount
            .create_candidate(&upsert_path)
            .expect("create upsert candidate");
        upsert
            .reserve_bytes(upsert_bytes.len() as u64)
            .expect("reserve upsert bytes");
        upsert
            .file
            .write_all(upsert_bytes)
            .expect("write upsert candidate");
        fs::write(fixture.native.join("upsert.bin"), b"external").expect("external create");
        commit_candidate(
            &mount,
            &upsert_path,
            FileWriteMode::Upsert,
            &mut upsert,
            upsert_bytes.len() as u64,
            &format!("{:x}", Sha256::digest(upsert_bytes)),
        )
        .expect("upsert replaces the currently observed regular file");
        assert_eq!(
            fs::read(fixture.native.join("upsert.bin")).unwrap(),
            upsert_bytes
        );

        let create_path = path("/create.bin");
        let create_bytes = b"envd-create";
        let mut create = mount
            .create_candidate(&create_path)
            .expect("create no-replace candidate");
        create
            .reserve_bytes(create_bytes.len() as u64)
            .expect("reserve create bytes");
        create
            .file
            .write_all(create_bytes)
            .expect("write create candidate");
        fs::write(fixture.native.join("create.bin"), b"external").expect("external wins create");
        assert_eq!(
            commit_candidate(
                &mount,
                &create_path,
                FileWriteMode::Create,
                &mut create,
                create_bytes.len() as u64,
                &format!("{:x}", Sha256::digest(create_bytes)),
            ),
            Err(ResourceError::Conflict)
        );
        assert_eq!(
            fs::read(fixture.native.join("create.bin")).unwrap(),
            b"external"
        );
    }

    #[test]
    fn observes_text_and_structured_resources_with_explicit_offsets() {
        let fixture = Fixture::read_only();
        fs::create_dir(fixture.native.join("docs")).expect("docs directory");
        fs::create_dir_all(fixture.native.join("nested/child")).expect("nested directories");
        fs::write(fixture.native.join("docs/main.txt"), "alpha\nbeta\n").expect("text fixture");
        fs::write(
            fixture.native.join("docs/invalid-after-page.bin"),
            b"valid\n\xff",
        )
        .expect("invalid trailing text fixture");
        fs::write(
            fixture.native.join("docs/boundaries.txt"),
            "a\rb\nc\u{2028}d\r\ne",
        )
        .expect("line boundary fixture");
        for index in 0..8 {
            fs::write(
                fixture.native.join(format!("docs/item-{index}.txt")),
                format!("needle {index}\n"),
            )
            .expect("search fixture");
        }

        let stat = fixture
            .resources
            .stat(
                &fixture.mounts,
                &FileStatParams {
                    context: context("stat"),
                    path: path("/docs/main.txt"),
                    follow_symlinks: true,
                },
            )
            .expect("stat succeeds");
        assert_eq!(stat.info.kind, FileKind::File);

        let first = fixture
            .resources
            .read_text(
                &fixture.mounts,
                &FileReadTextParams {
                    context: context("text-1"),
                    path: path("/docs/main.txt"),
                    line_offset: 0,
                    line_limit: 1,
                    max_line_length: 2_000,
                },
            )
            .expect("first text segment");
        assert_eq!(first.text, "alpha\n");
        assert_eq!(first.lines_read, 1);
        assert!(first.has_more);
        let second = fixture
            .resources
            .read_text(
                &fixture.mounts,
                &FileReadTextParams {
                    context: context("text-2"),
                    path: path("/docs/main.txt"),
                    line_offset: first.line_offset + first.lines_read,
                    line_limit: 1,
                    max_line_length: 2_000,
                },
            )
            .expect("second text segment");
        assert_eq!(second.text, "beta\n");
        assert_eq!(second.lines_read, 1);
        assert!(!second.has_more);

        let valid_page = fixture
            .resources
            .read_text(
                &fixture.mounts,
                &FileReadTextParams {
                    context: context("text-invalid-after-page"),
                    path: path("/docs/invalid-after-page.bin"),
                    line_offset: 0,
                    line_limit: 1,
                    max_line_length: 2_000,
                },
            )
            .expect("invalid UTF-8 after the requested page is not scanned");
        assert_eq!(valid_page.text, "valid\n");
        assert_eq!(valid_page.lines_read, 1);
        assert!(valid_page.has_more);

        let boundaries = fixture
            .resources
            .read_text(
                &fixture.mounts,
                &FileReadTextParams {
                    context: context("text-boundaries"),
                    path: path("/docs/boundaries.txt"),
                    line_offset: 1,
                    line_limit: 1,
                    max_line_length: 2_000,
                },
            )
            .expect("LF-only line selection succeeds");
        assert_eq!(boundaries.text, "c\u{2028}d\r\n");
        assert_eq!(boundaries.lines_read, 1);
        assert!(boundaries.has_more);

        let first_list = fixture
            .resources
            .list(
                &fixture.mounts,
                &FileListParams {
                    context: context("list-1"),
                    path: path("/docs"),
                    offset: 0,
                    max_results: 3,
                    include_hidden: false,
                },
            )
            .expect("first list segment");
        assert_eq!(first_list.entries.len(), 3);
        assert!(first_list.has_more);
        let continued = fixture
            .resources
            .list(
                &fixture.mounts,
                &FileListParams {
                    context: context("list-2"),
                    path: path("/docs"),
                    offset: first_list.offset + first_list.entries.len() as u64,
                    max_results: 3,
                    include_hidden: false,
                },
            )
            .expect("list continuation");
        assert_eq!(continued.entries.len(), 3);
        assert!(continued.has_more);
        assert_ne!(continued.entries, first_list.entries);

        let found = fixture
            .resources
            .find(
                &fixture.mounts,
                &FileFindParams {
                    context: context("find"),
                    root: path("/"),
                    pattern: "*.txt".to_owned(),
                    offset: 0,
                    max_results: 100,
                    recursive: true,
                    include_hidden: false,
                    kinds: vec![FileKind::File],
                    respect_git_ignore: false,
                },
            )
            .expect("find succeeds");
        assert_eq!(found.entries.len(), 10);
        assert!(!found.has_more);

        let searched = fixture
            .resources
            .search(
                &fixture.mounts,
                &FileSearchParams {
                    context: context("search"),
                    root: path("/docs"),
                    query: "needle".to_owned(),
                    mode: SearchMode::Literal,
                    case_sensitive: true,
                    offset: 0,
                    max_results: 100,
                    include_hidden: false,
                    max_line_length: 2_000,
                    include_pattern: "**/*".to_owned(),
                    respect_git_ignore: false,
                    context_lines: 0,
                    max_matches_per_file: None,
                    max_files: None,
                    max_file_bytes: super::MAX_SEARCH_BYTES_PER_FILE,
                },
            )
            .expect("search succeeds");
        assert_eq!(searched.matches.len(), 8);
        assert!(!searched.has_more);
        assert!(
            searched
                .matches
                .windows(2)
                .all(|pair| pair[0].path.path <= pair[1].path.path)
        );

        let boundary_search = fixture
            .resources
            .search(
                &fixture.mounts,
                &FileSearchParams {
                    context: context("search-boundaries"),
                    root: path("/docs"),
                    query: "d".to_owned(),
                    mode: SearchMode::Literal,
                    case_sensitive: true,
                    offset: 0,
                    max_results: 10,
                    include_hidden: false,
                    max_line_length: 2_000,
                    include_pattern: "**/*".to_owned(),
                    respect_git_ignore: false,
                    context_lines: 0,
                    max_matches_per_file: None,
                    max_files: None,
                    max_file_bytes: super::MAX_SEARCH_BYTES_PER_FILE,
                },
            )
            .expect("LF-only search succeeds");
        let boundary_match = boundary_search
            .matches
            .iter()
            .find(|matched| matched.path.path == "/docs/boundaries.txt")
            .expect("boundary file match");
        assert_eq!(boundary_match.line_number, 2);
        assert_eq!(boundary_match.preview, "c\u{2028}d\r");
    }

    #[test]
    fn reads_and_searches_files_larger_than_the_mutation_limit() {
        let fixture = Fixture::read_only();
        let mut content = b"needle at the start".to_vec();
        content.resize(2 * 1024 * 1024, b'x');
        fs::write(fixture.native.join("large.txt"), content).expect("large source fixture");

        let read = fixture
            .resources
            .read_text(
                &fixture.mounts,
                &FileReadTextParams {
                    context: context("large-read"),
                    path: path("/large.txt"),
                    line_offset: 0,
                    line_limit: 1,
                    max_line_length: 32,
                },
            )
            .expect("large source remains readable through a bounded text page");
        assert_eq!(read.lines_read, 1);
        assert!(read.text.starts_with("needle at the start"));
        assert_eq!(read.truncated_lines, vec![1]);

        let searched = fixture
            .resources
            .search(
                &fixture.mounts,
                &FileSearchParams {
                    context: context("large-search"),
                    root: path("/"),
                    query: "needle".to_owned(),
                    mode: SearchMode::Literal,
                    case_sensitive: true,
                    offset: 0,
                    max_results: 10,
                    include_hidden: false,
                    max_line_length: 32,
                    include_pattern: "**/*".to_owned(),
                    respect_git_ignore: false,
                    context_lines: 0,
                    max_matches_per_file: None,
                    max_files: None,
                    max_file_bytes: super::MAX_SEARCH_BYTES_PER_FILE,
                },
            )
            .expect("search uses its own scan ceiling");
        assert_eq!(searched.matches.len(), 1);
        assert_eq!(searched.matches[0].path, path("/large.txt"));
    }

    #[test]
    fn traversal_prunes_hidden_directories_and_pages_by_global_path_order() {
        let fixture = Fixture::read_only();
        fs::create_dir_all(fixture.native.join("a")).expect("ordered directory");
        fs::write(fixture.native.join("a/x.txt"), "visible").expect("nested file");
        fs::write(fixture.native.join("a-b.txt"), "visible").expect("sibling file");
        fs::create_dir(fixture.native.join(".hidden")).expect("hidden directory");
        let hidden = fs::File::create(fixture.native.join(".hidden/oversized.txt"))
            .expect("hidden sparse source");
        hidden
            .set_len(super::MAX_SEARCH_BYTES_PER_FILE + 1)
            .expect("extends hidden sparse source");

        let found = fixture
            .resources
            .find(
                &fixture.mounts,
                &FileFindParams {
                    context: context("ordered-find"),
                    root: path("/"),
                    pattern: "*.txt".to_owned(),
                    offset: 0,
                    max_results: 2,
                    recursive: true,
                    include_hidden: false,
                    kinds: vec![FileKind::File],
                    respect_git_ignore: false,
                },
            )
            .expect("find prunes hidden directories");
        assert_eq!(
            found
                .entries
                .iter()
                .map(|entry| entry.relative_path.as_str())
                .collect::<Vec<_>>(),
            vec!["a-b.txt", "a/x.txt"]
        );

        let searched = fixture
            .resources
            .search(
                &fixture.mounts,
                &FileSearchParams {
                    context: context("hidden-search"),
                    root: path("/"),
                    query: "absent".to_owned(),
                    mode: SearchMode::Literal,
                    case_sensitive: true,
                    offset: 0,
                    max_results: 10,
                    include_hidden: false,
                    max_line_length: 32,
                    include_pattern: "**/*".to_owned(),
                    respect_git_ignore: false,
                    context_lines: 0,
                    max_matches_per_file: None,
                    max_files: None,
                    max_file_bytes: super::MAX_SEARCH_BYTES_PER_FILE,
                },
            )
            .expect("search never scans the hidden sparse source");
        assert!(searched.matches.is_empty());
    }

    #[test]
    fn find_and_search_apply_gitignore_include_context_and_limits_in_resource_worker() {
        let fixture = Fixture::read_only();
        fs::create_dir_all(fixture.native.join("src")).expect("source directory");
        fs::create_dir_all(fixture.native.join("ignored")).expect("ignored directory");
        fs::write(fixture.native.join(".gitignore"), "ignored/\n").expect("gitignore fixture");
        fs::write(
            fixture.native.join("src/match.py"),
            "before\nneedle one\nafter\nneedle two\n",
        )
        .expect("search fixture");
        fs::write(fixture.native.join("src/other.txt"), "needle\n").expect("excluded fixture");
        fs::write(fixture.native.join("ignored/hidden.py"), "needle\n")
            .expect("ignored search fixture");

        let found = fixture
            .resources
            .find(
                &fixture.mounts,
                &FileFindParams {
                    context: context("gitignore-find"),
                    root: path("/"),
                    pattern: "*.py".to_owned(),
                    offset: 0,
                    max_results: 10,
                    recursive: true,
                    include_hidden: false,
                    kinds: vec![FileKind::File],
                    respect_git_ignore: true,
                },
            )
            .expect("gitignore-aware find succeeds");
        assert_eq!(
            found
                .entries
                .iter()
                .map(|entry| entry.relative_path.as_str())
                .collect::<Vec<_>>(),
            vec!["src/match.py"]
        );

        let searched = fixture
            .resources
            .search(
                &fixture.mounts,
                &FileSearchParams {
                    context: context("filtered-search"),
                    root: path("/"),
                    query: "needle".to_owned(),
                    mode: SearchMode::Literal,
                    case_sensitive: true,
                    offset: 0,
                    max_results: 10,
                    include_hidden: false,
                    max_line_length: 2_000,
                    include_pattern: "**/*.py".to_owned(),
                    respect_git_ignore: true,
                    context_lines: 1,
                    max_matches_per_file: Some(1),
                    max_files: Some(10),
                    max_file_bytes: super::MAX_SEARCH_BYTES_PER_FILE,
                },
            )
            .expect("bounded filtered search succeeds");
        assert_eq!(searched.matches.len(), 1);
        assert_eq!(searched.matches[0].path, path("/src/match.py"));
        assert_eq!(searched.matches[0].line_number, 2);
        assert_eq!(searched.matches[0].context, "before\nneedle one\nafter\n");
        assert_eq!(searched.matches[0].context_start_line, 1);
        assert!(!searched.has_more);
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn traversal_omits_unrepresentable_names() {
        use std::os::unix::ffi::OsStringExt;

        let fixture = Fixture::read_only();
        let invalid = std::ffi::OsString::from_vec(vec![b'b', b'a', b'd', 0xff]);
        if let Err(error) = fs::write(fixture.native.join(invalid), "content") {
            if error.raw_os_error() == Some(libc::EILSEQ) {
                return;
            }
            panic!("non-UTF-8 fixture: {error}");
        }
        fs::write(fixture.native.join("valid.txt"), "content").expect("UTF-8 fixture");

        let listed = fixture
            .resources
            .list(
                &fixture.mounts,
                &FileListParams {
                    context: context("unrepresentable-list"),
                    path: path("/"),
                    offset: 0,
                    max_results: 10,
                    include_hidden: true,
                },
            )
            .expect("listing omits the non-UTF-8 name");
        assert_eq!(listed.entries.len(), 1);
        assert_eq!(listed.entries[0].relative_path, "valid.txt");
        assert_eq!(listed.omitted_unrepresentable_entries, 1);
    }

    #[cfg(unix)]
    #[test]
    fn broad_mount_subtracts_the_runtime_parent_from_all_resource_paths() {
        use std::os::unix::fs::symlink;

        let tree = TempTree::new();
        let runtime_parent = tree.child("container/runtime");
        fs::create_dir_all(tree.child("container/data")).expect("visible sibling directory");
        fs::write(tree.child("container/data/file.txt"), "visible").expect("visible file");
        let runtime = RuntimeState::prepare(&runtime_parent).expect("runtime state");
        symlink("container/runtime", tree.child("runtime-link")).expect("runtime symlink");

        let mut config = Config::for_test("env-broad-mount-test");
        config.runtime = Some(runtime);
        config.mounts.push(TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: tree.0.clone(),
            writable: true,
            allow_command_execution: false,
            max_file_bytes: 1024 * 1024,
            allowed_operations: Vec::new(),
        });
        let operations = OperationLedger::new(
            config.environment_id.clone(),
            11,
            256,
            Duration::from_secs(60),
            Duration::from_secs(60),
        );
        let mounts = MountRegistry::initialize_scoped(&config).expect("broad mount initializes");
        let resources = ResourceRegistry::new(&config, operations);

        let listed = resources
            .list(
                &mounts,
                &FileListParams {
                    context: context("protected-list"),
                    path: path("/container"),
                    offset: 0,
                    max_results: 10,
                    include_hidden: true,
                },
            )
            .expect("protected child is subtracted from traversal");
        assert_eq!(
            listed
                .entries
                .iter()
                .map(|entry| entry.relative_path.as_str())
                .collect::<Vec<_>>(),
            vec!["data"]
        );
        assert_eq!(
            resources.stat(
                &mounts,
                &FileStatParams {
                    context: context("protected-stat"),
                    path: path("/container/runtime"),
                    follow_symlinks: false,
                },
            ),
            Err(ResourceError::Denied)
        );
        assert_eq!(
            resources.stat(
                &mounts,
                &FileStatParams {
                    context: context("protected-link"),
                    path: path("/runtime-link"),
                    follow_symlinks: true,
                },
            ),
            Err(ResourceError::Denied)
        );

        assert_eq!(
            mounts
                .get("workspace")
                .expect("workspace mount")
                .ensure_unprotected(std::path::Path::new("container/runtime")),
            Err(crate::mount::MountPathError::Denied)
        );
        let removed = resources.remove(
            &mounts,
            &FileRemoveParams {
                context: context("protected-remove"),
                path: path("/container"),
                expected_kind: FileKind::Directory,
                recursive: true,
                max_entries: 100,
            },
        );
        assert_eq!(removed, Err(ResourceError::Denied));
        assert!(tree.child("container/data/file.txt").exists());
    }

    #[cfg(windows)]
    #[test]
    fn recursively_removes_a_windows_directory_tree() {
        let fixture = Fixture::with_mount(true, 64 * 1024 * 1024, 64);
        fs::create_dir_all(fixture.native.join("tree/first/nested"))
            .expect("first directory branch");
        fs::create_dir_all(fixture.native.join("tree/second")).expect("second directory branch");
        fs::write(fixture.native.join("tree/first/nested/one.txt"), "one").expect("first file");
        fs::write(fixture.native.join("tree/second/two.txt"), "two").expect("second file");

        let removed = fixture
            .resources
            .remove(
                &fixture.mounts,
                &FileRemoveParams {
                    context: context("recursive-remove"),
                    path: path("/tree"),
                    expected_kind: FileKind::Directory,
                    recursive: true,
                    max_entries: 6,
                },
            )
            .expect("recursive removal succeeds");

        assert_eq!(removed, 6);
        assert!(!fixture.native.join("tree").exists());
    }

    #[cfg(windows)]
    #[test]
    fn replaces_an_existing_windows_file_without_native_identity() {
        let fixture = Fixture::with_mount(true, 64 * 1024 * 1024, 64);
        fs::write(fixture.native.join("target.txt"), "old").expect("existing target");

        let (info, bytes) = fixture
            .resources
            .write_text(
                &fixture.mounts,
                &FileWriteTextParams {
                    context: context("replace"),
                    path: path("/target.txt"),
                    mode: FileWriteMode::Replace,
                    text: "new content".to_owned(),
                    executable: None,
                },
            )
            .expect("replace succeeds");

        assert_eq!(info.path, path("/target.txt"));
        assert_eq!(bytes, 11);
        assert_eq!(
            fs::read_to_string(fixture.native.join("target.txt")).expect("replaced target"),
            "new content"
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn mutations_preserve_preconditions_and_publish_complete_candidates() {
        let fixture = Fixture::new();
        fixture
            .resources
            .mkdir(
                &fixture.mounts,
                &FileMkdirParams {
                    context: context("mkdir"),
                    path: path("/work/nested"),
                    parents: true,
                    exist_ok: false,
                },
            )
            .expect("mkdir succeeds");
        let (_created, bytes) = fixture
            .resources
            .write_text(
                &fixture.mounts,
                &FileWriteTextParams {
                    context: context("write"),
                    path: path("/work/nested/source.txt"),
                    mode: FileWriteMode::Create,
                    text: "hello world\n".to_owned(),
                    executable: Some(false),
                },
            )
            .expect("create succeeds");
        assert_eq!(bytes, 12);
        let (_appended, _) = fixture
            .resources
            .write_text(
                &fixture.mounts,
                &FileWriteTextParams {
                    context: context("append"),
                    path: path("/work/nested/source.txt"),
                    mode: FileWriteMode::Append,
                    text: "tail\n".to_owned(),
                    executable: None,
                },
            )
            .expect("append uses atomic replacement");
        fixture
            .resources
            .patch_text(
                &fixture.mounts,
                &FilePatchTextParams {
                    context: context("patch"),
                    path: path("/work/nested/source.txt"),
                    patch_format: "unified_diff".to_owned(),
                    patch: "@@ -1,2 +1,2 @@\n-hello world\n+hello block2\n tail\n".to_owned(),
                },
            )
            .expect("patch succeeds");
        assert_eq!(
            fs::read_to_string(fixture.native.join("work/nested/source.txt"))
                .expect("reads patched file"),
            "hello block2\ntail\n"
        );

        fixture
            .resources
            .copy(
                &fixture.mounts,
                &FileCopyParams {
                    context: context("copy"),
                    source: path("/work/nested/source.txt"),
                    destination: path("/work/nested/copy.txt"),
                    replace: false,
                },
            )
            .expect("atomic copy succeeds");
        let moved = fixture
            .resources
            .move_path(
                &fixture.mounts,
                &FileMoveParams {
                    context: context("move"),
                    source: path("/work/nested/copy.txt"),
                    destination: path("/work/moved.txt"),
                    replace: false,
                },
            )
            .expect("atomic move succeeds");
        fixture
            .resources
            .remove(
                &fixture.mounts,
                &FileRemoveParams {
                    context: context("remove"),
                    path: moved.path,
                    expected_kind: FileKind::File,
                    recursive: false,
                    max_entries: 1,
                },
            )
            .expect("removal succeeds");
        assert!(!fixture.native.join("work/moved.txt").exists());
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn truncates_long_search_previews_and_preflights_recursive_remove() {
        let fixture = Fixture::new();
        fs::write(fixture.native.join("long.txt"), vec![b'a'; 64 * 1024 + 1])
            .expect("long-line fixture");
        let searched = fixture.resources.search(
            &fixture.mounts,
            &FileSearchParams {
                context: context("long-search"),
                root: path("/"),
                query: "a".to_owned(),
                mode: SearchMode::Literal,
                case_sensitive: true,
                offset: 0,
                max_results: 100,
                include_hidden: false,
                max_line_length: 2_000,
                include_pattern: "**/*".to_owned(),
                respect_git_ignore: false,
                context_lines: 0,
                max_matches_per_file: None,
                max_files: None,
                max_file_bytes: super::MAX_SEARCH_BYTES_PER_FILE,
            },
        );
        let searched = searched.expect("long search line is readable through a bounded preview");
        assert_eq!(searched.matches.len(), 1);
        assert_eq!(searched.matches[0].preview.len(), 2_000);
        assert!(searched.matches[0].preview_truncated);

        fs::create_dir_all(fixture.native.join("tree/child")).expect("remove tree");
        fs::write(fixture.native.join("tree/child/file"), b"data").expect("remove file");
        let bounded = fixture.resources.remove(
            &fixture.mounts,
            &FileRemoveParams {
                context: context("remove-bounded"),
                path: path("/tree"),
                expected_kind: FileKind::Directory,
                recursive: true,
                max_entries: 2,
            },
        );
        assert!(matches!(bounded, Err(ResourceError::Limit)));
        assert!(fixture.native.join("tree/child/file").exists());

        let removed = fixture
            .resources
            .remove(
                &fixture.mounts,
                &FileRemoveParams {
                    context: context("remove-complete"),
                    path: path("/tree"),
                    expected_kind: FileKind::Directory,
                    recursive: true,
                    max_entries: 3,
                },
            )
            .expect("bounded recursive remove succeeds");
        assert_eq!(removed, 3);
        assert!(!fixture.native.join("tree").exists());
    }

    #[test]
    fn validates_patch_counts_and_no_newline_markers() {
        assert!(matches!(
            apply_unified_diff("one\ntwo\n", "@@ -1,1 +1,1 @@\n one\n-two\n+three\n"),
            Err(ResourceError::Invalid)
        ));
        assert!(matches!(
            apply_unified_diff("one\ntwo\n", "@@ -2 +9 @@\n-two\n+three\n"),
            Err(ResourceError::Invalid)
        ));
        let patch =
            "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n";
        let (result, hunks) = apply_unified_diff("old", patch).expect("no-newline patch applies");
        assert_eq!(result, "new");
        assert_eq!(hunks, 1);
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn staged_replacement_refuses_symlink_leaf_but_patch_follows_contained_target() {
        use std::os::unix::fs::symlink;

        let fixture = Fixture::new();
        fs::write(fixture.native.join("target.txt"), b"original").expect("target fixture");
        symlink("target.txt", fixture.native.join("link.txt")).expect("symlink fixture");
        let result = fixture.resources.write_text(
            &fixture.mounts,
            &FileWriteTextParams {
                context: context("symlink-write"),
                path: path("/link.txt"),
                mode: FileWriteMode::Replace,
                text: "replacement".to_owned(),
                executable: None,
            },
        );
        assert!(matches!(result, Err(ResourceError::Denied)));
        assert_eq!(
            fs::read_to_string(fixture.native.join("target.txt")).expect("target remains"),
            "original"
        );

        let patch = "@@ -1 +1 @@\n-original\n\\ No newline at end of file\n+patched\n\\ No newline at end of file\n";
        let (info, hunks) = fixture
            .resources
            .patch_text(
                &fixture.mounts,
                &FilePatchTextParams {
                    context: context("symlink-patch"),
                    path: path("/link.txt"),
                    patch_format: "unified_diff".to_owned(),
                    patch: patch.to_owned(),
                },
            )
            .expect("patches contained symlink target");
        assert_eq!(info.path, path("/link.txt"));
        assert_eq!(hunks, 1);
        assert!(
            fs::symlink_metadata(fixture.native.join("link.txt"))
                .expect("link remains")
                .file_type()
                .is_symlink()
        );
        assert_eq!(
            fs::read_to_string(fixture.native.join("target.txt")).expect("patched target"),
            "patched"
        );
    }
}
