//! BoxLite owns only VM boot and termination; EIP remains the guest worker's job.
use super::Plan;
use boxlite::runtime::options::{PortProtocol, PortSpec, VolumeSpec};
use boxlite::{BoxCommand, BoxOptions, BoxliteOptions, BoxliteRuntime, LiteBox, RootfsSpec};
use futures_util::StreamExt;
use std::io;
use tokio::task::JoinSet;

pub(crate) struct Backend {
    runtime: BoxliteRuntime,
    sandbox: Option<LiteBox>,
    output: JoinSet<()>,
    _path: super::runtime_path::RuntimePath,
}

impl Backend {
    pub fn new(home: &std::path::Path) -> io::Result<Self> {
        let path = super::runtime_path::RuntimePath::new(home)?;
        let runtime = BoxliteRuntime::new(BoxliteOptions {
            home_dir: path.path().to_path_buf(),
            ..Default::default()
        })
        .map_err(io::Error::other)?;
        Ok(Self {
            runtime,
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
        check("image_create")?;
        let volumes = plan
            .mounts
            .iter()
            .filter(|mount| mount.target != super::GUEST_DIRECTORY)
            .map(|mount| {
                let mut volume =
                    VolumeSpec::bind_mount(mount.source.to_string_lossy().as_ref(), &mount.target);
                volume.read_only = mount.read_only;
                volume
            })
            .collect();
        let options = BoxOptions {
            cpus: Some(plan.cpus),
            memory_mib: Some(plan.memory_mib),
            rootfs: RootfsSpec::Image(plan.image.clone()),
            volumes,
            ports: vec![PortSpec {
                host_ip: Some("127.0.0.1".into()),
                host_port: Some(plan.port),
                guest_port: super::super::worker::guest::PORT,
                protocol: PortProtocol::Tcp,
            }],
            user: Some("0:0".into()),
            entrypoint: Some(vec!["/bin/sleep".into()]),
            cmd: Some(vec!["infinity".into()]),
            detach: false,
            auto_delete: Some(0),
            ..Default::default()
        };
        self.sandbox = Some(
            self.runtime
                .create(options, Some("worker".into()))
                .await
                .map_err(|error| plan.failure("image_create", error))?,
        );
        check("vm_start")?;
        self.sandbox
            .as_ref()
            .unwrap()
            .start()
            .await
            .map_err(|error| plan.failure("vm_start", error))?;
        check("bootstrap_copy")?;
        // Bootstrap artifacts use the SDK's copy channel instead of consuming
        // an additional physical filesystem device alongside user mounts.
        self.sandbox
            .as_ref()
            .unwrap()
            .copy_into(
                plan.control.join("."),
                super::GUEST_DIRECTORY,
                boxlite::CopyOptions::default(),
            )
            .await
            .map_err(|error| plan.failure("bootstrap_copy", error))?;
        check("worker_exec")?;
        let mut execution = self
            .sandbox
            .as_ref()
            .unwrap()
            .exec(BoxCommand::new(super::GUEST_BINARY).args(["--internal-vm-worker"]))
            .await
            .map_err(|error| plan.failure("worker_exec", error))?;
        if let Some(mut stream) = execution.stdout() {
            self.output.spawn(async move {
                let mut remaining = 4096;
                while let Some(line) = stream.next().await {
                    super::report_worker_output("boxlite", &line, &mut remaining);
                }
            });
        }
        if let Some(mut stream) = execution.stderr() {
            self.output.spawn(async move {
                let mut remaining = 4096;
                while let Some(line) = stream.next().await {
                    super::report_worker_output("boxlite", &line, &mut remaining);
                }
            });
        }
        self.output.spawn(async move {
            let _ = execution.wait().await;
        });
        Ok(())
    }

    pub async fn remove(&mut self) -> io::Result<()> {
        self.output.abort_all();
        while self.output.join_next().await.is_some() {}
        // Keep the SDK handle on failure so a later removal can retry.
        // The named record may exist even when startup returned an error.
        if self
            .runtime
            .exists("worker")
            .await
            .map_err(io::Error::other)?
        {
            self.runtime
                .remove("worker", true)
                .await
                .map_err(io::Error::other)?;
        }
        self.sandbox.take();
        Ok(())
    }
}
