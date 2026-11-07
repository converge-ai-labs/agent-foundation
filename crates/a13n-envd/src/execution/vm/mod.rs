//! Session-scoped VM ownership. SDKs bootstrap the canonical private worker RPC.
#[cfg(feature = "vm-boxlite")]
mod boxlite;
#[cfg(feature = "vm-microsandbox")]
mod microsandbox;
mod runtime_path;
pub(crate) mod staging;
#[cfg(test)]
mod tests;

use super::{
    boundary::{Access, Grant, GrantSource, Sandbox},
    worker::{WorkerConfig, client::Client, guest},
};
use crate::eip;
use std::{
    fs,
    io::{self, Write},
    os::unix::fs::{OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::io::AsyncWriteExt;
use zeroize::Zeroize;

const GUEST_DIRECTORY: &str = "/opt/a13n-worker";
const GUEST_BINARY: &str = "/opt/a13n-worker/envd";

// Observe abandonment only between SDK calls: dropping an in-flight SDK future
// is not a barrier for its background filesystem work or process reaping.
pub(crate) fn check_startup(deadline: tokio::time::Instant, abandoned: bool) -> io::Result<()> {
    if abandoned || tokio::time::Instant::now() >= deadline {
        return Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "VM startup admission ended",
        ));
    }
    Ok(())
}

pub(crate) struct CleanupRetry<'a> {
    directory: &'a Path,
    stage: &'static str,
    delay: Duration,
}

impl<'a> CleanupRetry<'a> {
    pub fn new(directory: &'a Path, stage: &'static str) -> Self {
        Self {
            directory,
            stage,
            delay: Duration::from_secs(1),
        }
    }

    pub async fn failed(&mut self, error: &io::Error) {
        eprintln!(
            "{}",
            serde_json::json!({
                "level": "warn", "event": "a13n-envd.vm_cleanup_pending",
                "directory": self.directory, "stage": self.stage,
                "retry_seconds": self.delay.as_secs(), "capacity_retained": true,
                "error": error.to_string().chars().take(2048).collect::<String>(),
            })
        );
        tokio::time::sleep(self.delay).await;
        self.delay = (self.delay * 2).min(Duration::from_secs(60));
    }
}

struct Mount {
    source: PathBuf,
    target: String,
    read_only: bool,
    // Keep the authorized object alive and recheck pathname-based SDK exports.
    directory: fs::File,
}

pub(crate) struct Plan {
    pub(super) home: PathBuf,
    control: PathBuf,
    pub(super) backend: eip::VmBackend,
    image: String,
    cpus: u8,
    memory_mib: u32,
    worker_bytes: u64,
    mounts: Vec<Mount>,
    token: zeroize::Zeroizing<[u8; 32]>,
    limits: crate::config::DaemonLimits,
    port: u16,
}

impl Plan {
    pub async fn prepare(
        vm: &eip::VmIsolation,
        worker: WorkerConfig,
        directory: &Path,
        executable: &Path,
        ceiling: &Sandbox,
        sources: &[GrantSource],
        protected: &[PathBuf],
    ) -> io::Result<Self> {
        guest::validate(&worker)?;
        fs::set_permissions(directory, fs::Permissions::from_mode(0o700))?;
        let cpus = u8::try_from(vm.cpus)
            .ok()
            .filter(|value| *value > 0)
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "VM cpus must be between 1 and 255",
                )
            })?;
        if vm.memory_mib < 128 || vm.image.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "VM memory_mib must be at least 128 and image must be nonempty",
            ));
        }
        if !matches!(vm.backend, eip::VmBackend::Boxlite)
            && !matches!(vm.backend, eip::VmBackend::Microsandbox)
        {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "unsupported VM backend",
            ));
        }
        let mut mounts = Vec::new();
        for mount in &vm.mounts {
            let target = Path::new(&mount.target);
            if !target.is_absolute()
                || target.components().any(|part| {
                    matches!(
                        part,
                        std::path::Component::ParentDir | std::path::Component::CurDir
                    )
                })
                || ["/proc", "/sys", "/dev", "/run", GUEST_DIRECTORY]
                    .iter()
                    .any(|path| overlaps(target, Path::new(path)))
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "VM mount target must be absolute and must not overlap a reserved path",
                ));
            }
            let read_only = match mount.access {
                eip::GrantAccess::ReadOnly => true,
                eip::GrantAccess::ReadWrite => false,
            };
            let mut policy = Sandbox::Restricted {
                grants: vec![Grant {
                    path: mount.source.clone().into(),
                    access: if read_only {
                        Access::ReadOnly
                    } else {
                        Access::ReadWrite
                    },
                }],
                full_disk_read: false,
            };
            policy
                .validate(protected)
                .map_err(|error| io::Error::new(io::ErrorKind::InvalidInput, error))?;
            let Sandbox::Restricted { grants, .. } = policy else {
                unreachable!()
            };
            let source = grants[0].path.clone();
            let directory = match ceiling {
                Sandbox::Disabled {} => fs::File::open(&source)?,
                Sandbox::Restricted { grants, .. } => {
                    let (index, grant) = grants
                        .iter()
                        .enumerate()
                        .find(|(_, grant)| {
                            source.starts_with(&grant.path)
                                && (read_only || grant.access == Access::ReadWrite)
                        })
                        .ok_or_else(|| {
                            io::Error::new(
                                io::ErrorKind::InvalidInput,
                                "VM mount exceeds Device grant authority",
                            )
                        })?;
                    let root = fs::File::open(&grant.path)?;
                    sources
                        .get(index)
                        .ok_or_else(|| io::Error::other("missing Device grant identity"))?
                        .check(&root)?;
                    let root = cap_std::fs::Dir::from_std_file(root);
                    let relative = source.strip_prefix(&grant.path).unwrap();
                    root.open_dir(if relative.as_os_str().is_empty() {
                        Path::new(".")
                    } else {
                        relative
                    })?
                    .into_std_file()
                }
            };
            if mounts.iter().any(|existing: &Mount| {
                overlaps(&existing.source, &source) || overlaps(Path::new(&existing.target), target)
            }) {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "VM mounts must not overlap",
                ));
            }
            mounts.push(Mount {
                source,
                target: mount.target.clone(),
                read_only,
                directory,
            });
        }
        let control = directory.join("control");
        fs::create_dir(&control)?;
        fs::set_permissions(&control, fs::Permissions::from_mode(0o755))?;
        let worker_bytes = tokio::fs::copy(executable, control.join("envd")).await?;
        fs::set_permissions(control.join("envd"), fs::Permissions::from_mode(0o555))?;
        let mut token = zeroize::Zeroizing::new([0; 32]);
        getrandom::fill(token.as_mut()).map_err(|error| io::Error::other(error.to_string()))?;
        let limits = worker.limits.clone();
        let staging = mounts
            .iter()
            .enumerate()
            .filter(|(_, mount)| !mount.read_only)
            .map(|(index, mount)| staging::register(mount, directory, index))
            .collect::<io::Result<Vec<_>>>()?;
        let mut bootstrap = guest::Bootstrap {
            token: *token,
            worker,
            staging,
        };
        let mut bytes = serde_json::to_vec(&bootstrap).map_err(io::Error::other)?;
        bootstrap.token.zeroize();
        let written = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(control.join("bootstrap.json"))
            .and_then(|mut file| file.write_all(&bytes));
        bytes.zeroize();
        written?;
        mounts.push(Mount {
            source: control.clone(),
            target: GUEST_DIRECTORY.into(),
            read_only: true,
            directory: fs::File::open(&control)?,
        });
        let listener = std::net::TcpListener::bind(("127.0.0.1", 0))?;
        let port = listener.local_addr()?.port();
        // Both SDKs bind their own listener; a collision fails startup, never retries a VM.
        drop(listener);
        Ok(Self {
            home: directory.join("runtime"),
            control,
            backend: vm.backend,
            image: vm.image.clone(),
            cpus,
            memory_mib: vm.memory_mib,
            worker_bytes,
            mounts,
            token,
            limits,
            port,
        })
    }

    pub(crate) fn failure(&self, stage: &str, error: impl std::fmt::Display) -> io::Error {
        let cause = error.to_string();
        eprintln!(
            "{}",
            serde_json::json!({
                "level": "warn", "event": "a13n-envd.vm_start_failed",
                "backend": self.backend, "stage": stage,
                "memory_mib": self.memory_mib, "worker_bytes": self.worker_bytes,
                "error": cause.chars().take(2048).collect::<String>(),
            })
        );
        let advice = if stage == "bootstrap_copy" && cause.contains("No space left on device") {
            "guest bootstrap temporary storage is full; increase memory_mib or use a smaller worker executable"
        } else {
            "inspect the Envd vm_start_failed diagnostic for the SDK cause"
        };
        io::Error::other(format!(
            "{:?} VM {stage} failed (memory_mib={}, worker_bytes={}): {advice}",
            self.backend, self.memory_mib, self.worker_bytes,
        ))
    }

    fn check_sources(&self) -> io::Result<()> {
        for mount in &self.mounts {
            GrantSource::from_metadata(&mount.directory.metadata()?)
                .check(&fs::File::open(&mount.source)?)?;
        }
        Ok(())
    }

    pub async fn connect(&mut self) -> io::Result<Client> {
        let connected = tokio::time::timeout(Duration::from_secs(30), async {
            loop {
                // A forwarding listener can accept before the guest listener exists.
                // Retry bootstrap only; no EIP operation has been admitted yet.
                let attempt = async {
                    let mut socket =
                        tokio::net::TcpStream::connect(("127.0.0.1", self.port)).await?;
                    socket.set_nodelay(true)?;
                    socket.write_all(self.token.as_ref()).await?;
                    let (reader, writer) = socket.into_split();
                    Client::connect(reader, writer, &self.limits).await
                }
                .await;
                match attempt {
                    Ok(client) => return Ok(client),
                    Err(error)
                        if matches!(
                            error.kind(),
                            io::ErrorKind::ConnectionRefused
                                | io::ErrorKind::ConnectionReset
                                | io::ErrorKind::BrokenPipe
                                | io::ErrorKind::UnexpectedEof
                        ) =>
                    {
                        tokio::time::sleep(Duration::from_millis(20)).await
                    }
                    Err(error) => return Err(error),
                }
            }
        })
        .await
        .map_err(|_| {
            io::Error::new(
                io::ErrorKind::TimedOut,
                "VM worker_connect timed out after 30 seconds",
            )
        })
        .and_then(|result| result);
        self.token.zeroize();
        let removed = fs::remove_file(self.control.join("bootstrap.json"));
        let client = connected?;
        removed?;
        Ok(client)
    }
}

impl Drop for Plan {
    fn drop(&mut self) {
        // Covers failed startup and cancelled connect futures as well as success.
        // Zeroizing owns the in-memory token independently of this filesystem step.
        let _ = fs::remove_file(self.control.join("bootstrap.json"));
    }
}

fn report_worker_output(backend: &str, line: &str, remaining: &mut usize) {
    if *remaining == 0 {
        return;
    }
    let text: String = line.chars().take(*remaining).collect();
    *remaining = remaining.saturating_sub(text.chars().count());
    eprintln!(
        "{}",
        serde_json::json!({
            "level": "warn", "event": "a13n-envd.vm_worker_output",
            "backend": backend, "text": text,
        })
    );
}

/// Publish the recovery record before any SDK side effect. A crash exposes
/// either a complete record or no record, never partially serialized bytes.
pub(crate) fn persist_backend(directory: &Path, backend: eip::VmBackend) -> io::Result<()> {
    persist_record(directory, "backend.json", &backend)
}

fn persist_record(directory: &Path, name: &str, value: &impl serde::Serialize) -> io::Result<()> {
    let mut temporary = tempfile::NamedTempFile::new_in(directory)?;
    serde_json::to_writer(&mut temporary, value).map_err(io::Error::other)?;
    temporary.as_file().sync_all()?;
    temporary
        .persist_noclobber(directory.join(name))
        .map_err(|error| error.error)?;
    fs::File::open(directory)?.sync_all()?;
    let parent = directory
        .parent()
        .ok_or_else(|| io::Error::other("VM directory has no parent"))?;
    fs::File::open(parent)?.sync_all()?;
    Ok(())
}

/// Called under the Device runtime lock before deleting an old generation.
/// Reclaim existing SDK records; never restart a guest or replay execution.
pub(crate) fn reclaim_generation(generation: &Path) -> io::Result<()> {
    let directory = generation.join("vms");
    if !directory.exists() {
        return Ok(());
    }
    let directory = directory.to_path_buf();
    std::thread::spawn(move || {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()?
            .block_on(async {
                let mut first_error = None;
                for entry in fs::read_dir(directory)? {
                    let result = async {
                        let entry = entry?;
                        if !entry.file_type()?.is_dir() {
                            return Err(io::Error::other("invalid VM state directory"));
                        }
                        let manifest = entry.path().join("backend.json");
                        let backend = match fs::read(&manifest) {
                            Ok(bytes) => {
                                serde_json::from_slice(&bytes).map_err(io::Error::other)?
                            }
                            // No SDK call can happen until this manifest is complete.
                            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
                            Err(error) => return Err(error),
                        };
                        let mut backend =
                            Backend::new(backend, &entry.path().join("runtime")).await?;
                        backend.remove().await?;
                        staging::reclaim(&entry.path())
                    }
                    .await;
                    if let Err(error) = result {
                        eprintln!(
                            "{}",
                            serde_json::json!({
                                "level": "error", "event": "a13n-envd.vm_generation_cleanup_failed",
                                "error": error.to_string().chars().take(2048).collect::<String>(),
                            })
                        );
                        first_error.get_or_insert(error);
                    }
                }
                first_error.map_or(Ok(()), Err)
            })
    })
    .join()
    .map_err(|_| io::Error::other("VM reclamation task panicked"))?
}

fn overlaps(a: &Path, b: &Path) -> bool {
    a.starts_with(b) || b.starts_with(a)
}

pub(crate) enum Backend {
    #[cfg(feature = "vm-boxlite")]
    Boxlite(Box<boxlite::Backend>),
    #[cfg(feature = "vm-microsandbox")]
    Microsandbox(Box<microsandbox::Backend>),
}

impl Backend {
    pub async fn new(backend: eip::VmBackend, home: &Path) -> io::Result<Self> {
        match backend {
            #[cfg(feature = "vm-boxlite")]
            eip::VmBackend::Boxlite => boxlite::Backend::new(home).map(Box::new).map(Self::Boxlite),
            #[cfg(feature = "vm-microsandbox")]
            eip::VmBackend::Microsandbox => microsandbox::Backend::new(home)
                .await
                .map(Box::new)
                .map(Self::Microsandbox),
            #[allow(unreachable_patterns)]
            _ => Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "VM backend is not included in this build",
            )),
        }
    }
    pub async fn start(
        &mut self,
        plan: &Plan,
        check: &(impl Fn(&'static str) -> io::Result<()> + Sync),
    ) -> io::Result<()> {
        // Both SDKs export host paths. These checks detect persistent replacement,
        // but deployment must keep source paths stable during SDK mount setup.
        plan.check_sources()?;
        match self {
            #[cfg(feature = "vm-boxlite")]
            Self::Boxlite(backend) => backend.start(plan, check).await?,
            #[cfg(feature = "vm-microsandbox")]
            Self::Microsandbox(backend) => backend.start(plan, check).await?,
        }
        plan.check_sources()
    }
    pub async fn remove(&mut self) -> io::Result<()> {
        match self {
            #[cfg(feature = "vm-boxlite")]
            Self::Boxlite(backend) => backend.remove().await,
            #[cfg(feature = "vm-microsandbox")]
            Self::Microsandbox(backend) => backend.remove().await,
        }
    }
}
