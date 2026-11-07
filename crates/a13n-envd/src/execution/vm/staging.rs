//! Registered shared staging directories, reclaimed only after VM termination.
use super::{GrantSource, Mount};
use crate::filesystem::StagingDirectory;
use cap_std::fs::Dir;
use serde::{Deserialize, Serialize};
use std::{
    fs, io,
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Record {
    root: PathBuf,
    root_identity: GrantSource,
    name: String,
    identity: GrantSource,
}

pub(super) fn register(
    mount: &Mount,
    journal: &Path,
    index: usize,
) -> io::Result<StagingDirectory> {
    let root = Dir::from_std_file(mount.directory.try_clone()?);
    let mut random = [0; 16];
    getrandom::fill(&mut random).map_err(io::Error::other)?;
    let name = format!(
        ".eip-stage-vm-{}",
        random
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>()
    );
    root.create_dir(&name)?;
    let stage = root.open(&name)?.into_std();
    // Shared workspace state, writable by the independently selected guest UID.
    stage.set_permissions(fs::Permissions::from_mode(0o777))?;
    stage.sync_all()?;
    root.open(".")?.sync_all()?;
    let record = Record {
        root: mount.source.clone(),
        root_identity: GrantSource::from_metadata(&mount.directory.metadata()?),
        name: name.clone(),
        identity: GrantSource::from_metadata(&stage.metadata()?),
    };
    super::persist_record(journal, &format!("staging-{index}.json"), &record)?;
    Ok(StagingDirectory {
        root: mount.target.clone().into(),
        directory: Path::new(&mount.target).join(name),
    })
}

pub(crate) fn reclaim(journal: &Path) -> io::Result<()> {
    for entry in fs::read_dir(journal)? {
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !name.starts_with("staging-") || !name.ends_with(".json") {
            continue;
        }
        let record: Record =
            serde_json::from_slice(&fs::read(entry.path())?).map_err(io::Error::other)?;
        let root = fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW)
            .open(&record.root)?;
        record.root_identity.check(&root)?;
        let root = Dir::from_std_file(root);
        let done = entry.path().with_extension("empty");
        let metadata = match root.symlink_metadata(&record.name) {
            Ok(metadata) => Some(metadata),
            Err(error) if error.kind() == io::ErrorKind::NotFound && done.exists() => None,
            Err(error) => return Err(error),
        };
        if let Some(metadata) = metadata {
            if !metadata.is_dir() || metadata.is_symlink() {
                return Err(io::Error::other("VM staging directory was replaced"));
            }
            let stage = root.open_dir(&record.name)?;
            record.identity.check(&stage.try_clone()?.into_std_file())?;
            for child in stage.entries()? {
                let child = child?;
                if child.file_type()?.is_dir() {
                    stage.remove_dir_all(child.file_name())?;
                } else {
                    stage.remove_file(child.file_name())?;
                }
            }
            stage.open(".")?.sync_all()?;
            // Persist completed contents cleanup before unlinking the directory.
            // Recovery may then accept its absence without accepting a pre-cleanup move.
            if !done.exists() {
                super::persist_record(journal, done.file_name().unwrap().to_str().unwrap(), &())?;
            }
            let named = root.open_dir(&record.name)?.into_std_file();
            record.identity.check(&named)?;
            root.remove_dir(&record.name)?;
            root.open(".")?.sync_all()?;
        }
        fs::remove_file(entry.path())?;
        fs::File::open(journal)?.sync_all()?;
        fs::remove_file(done)?;
    }
    Ok(())
}

use std::os::unix::fs::OpenOptionsExt;

#[cfg(test)]
mod tests {
    use super::*;

    fn register_test(root: &Path, journal: &Path) -> PathBuf {
        let mount = Mount {
            source: root.into(),
            target: "/workspace".into(),
            read_only: false,
            directory: fs::File::open(root).unwrap(),
        };
        let mapping = register(&mount, journal, 0).unwrap();
        root.join(mapping.directory.file_name().unwrap())
    }

    #[test]
    fn cleanup_owns_only_registered_contents_and_is_idempotent() {
        let root = tempfile::tempdir().unwrap();
        let journal = tempfile::tempdir().unwrap();
        let stage = register_test(root.path(), journal.path());
        fs::write(stage.join("candidate"), b"linked").unwrap();
        fs::hard_link(stage.join("candidate"), root.path().join("kept-link")).unwrap();
        fs::write(stage.join("move"), b"moved").unwrap();
        fs::rename(stage.join("move"), root.path().join("kept-move")).unwrap();
        fs::create_dir(stage.join("nested")).unwrap();
        fs::write(stage.join("nested/other"), b"cleanup").unwrap();
        std::os::unix::fs::symlink(root.path().join("kept-move"), stage.join("link")).unwrap();
        reclaim(journal.path()).unwrap();
        reclaim(journal.path()).unwrap();
        assert!(!stage.exists());
        assert_eq!(fs::read(root.path().join("kept-link")).unwrap(), b"linked");
        assert_eq!(fs::read(root.path().join("kept-move")).unwrap(), b"moved");
        assert_eq!(fs::read_dir(journal.path()).unwrap().count(), 0);
    }

    #[test]
    fn moved_or_replaced_directory_keeps_recovery_record() {
        let root = tempfile::tempdir().unwrap();
        let journal = tempfile::tempdir().unwrap();
        let stage = register_test(root.path(), journal.path());
        fs::write(stage.join("candidate"), b"original").unwrap();
        let moved = root.path().join("moved");
        fs::rename(&stage, &moved).unwrap();
        assert!(reclaim(journal.path()).is_err());
        fs::create_dir(&stage).unwrap();
        fs::write(stage.join("unrelated"), b"keep").unwrap();
        assert!(reclaim(journal.path()).is_err());
        assert_eq!(fs::read(stage.join("unrelated")).unwrap(), b"keep");
        assert!(journal.path().join("staging-0.json").exists());
        fs::remove_file(stage.join("unrelated")).unwrap();
        fs::remove_dir(&stage).unwrap();
        fs::rename(moved, stage).unwrap();
        reclaim(journal.path()).unwrap();
    }

    #[test]
    fn recovery_accepts_absence_only_after_durable_contents_cleanup() {
        let root = tempfile::tempdir().unwrap();
        let journal = tempfile::tempdir().unwrap();
        let stage = register_test(root.path(), journal.path());
        super::super::persist_record(journal.path(), "staging-0.empty", &()).unwrap();
        fs::remove_dir(stage).unwrap();
        reclaim(journal.path()).unwrap();
        assert_eq!(fs::read_dir(journal.path()).unwrap().count(), 0);
    }
}
