//! Task-scoped SDK backend: never mutate the process-wide MSB_HOME.
use super::Plan;
use microsandbox::setup::{InstallOptions, InstallSource, install_runtime};
use microsandbox::{
    ExecEvent, Sandbox,
    backend::{LocalBackend, with_backend},
};
use std::{io, sync::Arc};
use tokio::task::JoinSet;

// A static has one address. The SDK's const slice was duplicated into async
// installation code paths, embedding the entire compressed runtime seven times.
static RUNTIME_ARCHIVE: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/microsandbox-runtime.tar.gz"));

#[cfg(test)]
#[tokio::test]
async fn embedded_runtime_installs_into_private_home_without_kvm() {
    use std::os::unix::fs::PermissionsExt;

    let home = tempfile::tempdir().unwrap();
    // Match the private generation directory provided by the Device lifecycle.
    std::fs::set_permissions(home.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
    let backend = Backend::new(home.path()).await.unwrap();
    assert!(home.path().join("bin/msb").is_file());
    assert!(home.path().join("lib/libkrunfw.so.5.6.1").is_file());
    drop(backend);
    // Reopening retains a complete installation through the upstream installer.
    Backend::new(home.path()).await.unwrap();
}

pub(crate) struct Backend {
    backend: Arc<dyn microsandbox::Backend>,
    sandbox: Option<Sandbox>,
    output: JoinSet<()>,
    _path: super::runtime_path::RuntimePath,
}

impl Backend {
    pub async fn new(home: &std::path::Path) -> io::Result<Self> {
        let path = super::runtime_path::RuntimePath::new(home)?;
        let backend = LocalBackend::builder()
            .home(path.path())
            .config_path(home.join("config.json"))
            .build()
            .await
            .map_err(io::Error::other)?;
        let mut archive = tempfile::NamedTempFile::new_in(path.path())?;
        std::io::Write::write_all(&mut archive, RUNTIME_ARCHIVE)?;
        install_runtime(
            backend.config(),
            InstallOptions {
                source: InstallSource::Archive(archive.path().to_path_buf()),
                ..Default::default()
            },
        )
        .await
        .map_err(io::Error::other)?;
        Ok(Self {
            backend: backend.into(),
            sandbox: None,
            output: JoinSet::new(),
            _path: path,
        })
    }

    pub async fn start(
        &mut self,
        plan: &Plan,
        check: &(impl Fn(&'static str) -> io::Result<()> + Sync),
    ) -> io::Result<()> {
        check("vm_create")?;
        let sandbox = with_backend(self.backend.clone(), async {
            let mut builder = Sandbox::builder("worker")
                .image(plan.image.as_str())
                .user("0:0")
                .cpus(plan.cpus)
                .memory(plan.memory_mib)
                .port(plan.port, super::super::worker::guest::PORT)
                .detached(false);
            for mount in &plan.mounts {
                builder = builder.volume(&mount.target, |volume| {
                    let volume = volume.bind(&mount.source);
                    if mount.read_only {
                        volume.readonly()
                    } else {
                        volume
                    }
                });
            }
            builder.create().await
        })
        .await
        .map_err(|error| plan.failure("vm_create", error))?;
        self.sandbox = Some(sandbox);
        check("worker_exec")?;
        let mut execution = self
            .sandbox
            .as_ref()
            .unwrap()
            .exec_stream_with(super::GUEST_BINARY, |execution| {
                execution.args(["--internal-vm-worker"])
            })
            .await
            .map_err(|error| plan.failure("worker_exec", error))?;
        self.output.spawn(async move {
            let mut remaining = 4096;
            while let Some(event) = execution.recv().await {
                match event {
                    ExecEvent::Stdout(bytes) | ExecEvent::Stderr(bytes) => {
                        super::report_worker_output(
                            "microsandbox",
                            &String::from_utf8_lossy(&bytes),
                            &mut remaining,
                        );
                    }
                    ExecEvent::Failed(error) => {
                        super::report_worker_output(
                            "microsandbox",
                            &format!("worker failed: {error:?}"),
                            &mut remaining,
                        );
                        break;
                    }
                    ExecEvent::Exited { code } => {
                        super::report_worker_output(
                            "microsandbox",
                            &format!("worker exited: {code}"),
                            &mut remaining,
                        );
                        break;
                    }
                    _ => {}
                }
            }
        });
        Ok(())
    }

    pub async fn remove(&mut self) -> io::Result<()> {
        self.output.abort_all();
        while self.output.join_next().await.is_some() {}
        with_backend(self.backend.clone(), async {
            let sandbox = match Sandbox::get("worker").await {
                Ok(sandbox) => sandbox,
                Err(microsandbox::MicrosandboxError::SandboxNotFound(_)) => return Ok(()),
                Err(error) => return Err(error),
            };
            if sandbox
                .stop_with_timeout(std::time::Duration::from_secs(4))
                .await
                .is_err()
            {
                sandbox.kill().await?;
                sandbox.stop().await?;
            }
            Sandbox::remove("worker").await
        })
        .await
        .map_err(io::Error::other)?;
        self.sandbox.take();
        Ok(())
    }
}
