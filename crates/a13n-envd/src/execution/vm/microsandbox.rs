//! Task-scoped SDK backend: never mutate the process-wide MSB_HOME.
use super::Plan;
use microsandbox::setup::{InstallOptions, InstallSource, install_runtime};
use microsandbox::{
    Sandbox,
    backend::{LocalBackend, with_backend},
};
use std::{io, sync::Arc};
use tokio::task::JoinSet;

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
        install_runtime(
            backend.config(),
            InstallOptions {
                source: InstallSource::EmbeddedArchive,
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

    pub async fn start(&mut self, plan: &Plan) -> io::Result<()> {
        let sandbox = with_backend(self.backend.clone(), async {
            let mut builder = Sandbox::builder("worker")
                .image(plan.image.as_str())
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
        .map_err(io::Error::other)?;
        self.sandbox = Some(sandbox);
        let mut execution = self
            .sandbox
            .as_ref()
            .unwrap()
            .exec_stream_with(super::GUEST_BINARY, |execution| {
                execution.args(["--internal-vm-worker"])
            })
            .await
            .map_err(io::Error::other)?;
        self.output.spawn(async move {
            let _ = execution.wait().await;
        });
        Ok(())
    }

    pub async fn remove(&mut self) -> io::Result<()> {
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
        self.output.abort_all();
        while self.output.join_next().await.is_some() {}
        Ok(())
    }
}
