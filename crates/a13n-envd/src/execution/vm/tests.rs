use super::*;

#[test]
fn recovery_record_is_complete_and_cannot_be_overwritten() {
    let directory = tempfile::tempdir().unwrap();
    persist_backend(directory.path(), eip::VmBackend::Boxlite).unwrap();
    let path = directory.path().join("backend.json");
    let original = fs::read(&path).unwrap();
    assert_eq!(
        serde_json::from_slice::<eip::VmBackend>(&original).unwrap(),
        eip::VmBackend::Boxlite
    );
    assert_eq!(
        persist_backend(directory.path(), eip::VmBackend::Microsandbox)
            .unwrap_err()
            .kind(),
        io::ErrorKind::AlreadyExists
    );
    assert_eq!(fs::read(path).unwrap(), original);
    assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 1);
}

#[test]
fn generation_recovery_preserves_invalid_records_and_ignores_unstarted_preparation() {
    let generation = tempfile::tempdir().unwrap();
    let session = generation.path().join("vms/session-test");
    fs::create_dir_all(&session).unwrap();
    fs::write(session.join("incomplete-temporary"), b"partial").unwrap();
    reclaim_generation(generation.path()).unwrap();
    fs::write(session.join("backend.json"), b"partial").unwrap();
    assert!(reclaim_generation(generation.path()).is_err());
    assert_eq!(fs::read(session.join("backend.json")).unwrap(), b"partial");
}

fn worker() -> WorkerConfig {
    WorkerConfig {
        computer_use: false,
        managed: false,
        sandbox: Sandbox::Disabled {},
        grant_sources: Vec::new(),
        egress: super::super::boundary::Egress::Inherit {},
        execution: Some(crate::execution::Identity { uid: 0, gid: 0 }),
        groups: Some(Vec::new()),
        allow_sudo: false,
        runtime_directory: "/tmp/a13n-state".into(),
        device_id: "vm-test".into(),
        generation: 1,
        session_id: "ses_vmtest".into(),
        working_directory: "/tmp".into(),
        limits: Default::default(),
        command: Some(crate::config::CommandConfig {
            drop_supervisor_capabilities: false,
            full_control: true,
            base_environment: [
                ("PATH".into(), "/usr/bin:/bin".into()),
                ("HOME".into(), "/tmp".into()),
            ]
            .into(),
            trusted_executable_roots: vec!["/usr/bin".into(), "/bin".into()],
            shell_profiles: vec![crate::config::TrustedShellProfileConfig {
                profile_id: "default".into(),
                display_name: "Shell".into(),
                native_executable: "/usr/bin/dash".into(),
                fixed_arguments: vec!["-c".into()],
                safe_base_environment: Default::default(),
                executable_search_roots: vec!["/usr/bin".into(), "/bin".into()],
                max_script_bytes: 65536,
                allow_login_mode: false,
            }],
            max_arguments: 1024,
            max_argument_bytes: 65536,
            max_environment_entries: 256,
            max_environment_bytes: 65536,
        }),
        lifecycle: eip::SessionLifecyclePolicy {
            idle_timeout_ms: 60_000,
            max_lifetime_ms: None,
        },
    }
}

fn isolation(backend: eip::VmBackend) -> eip::VmIsolation {
    serde_json::from_value(serde_json::json!({
        "mode": "vm", "backend": backend, "image": "mirror.gcr.io/library/ubuntu:24.04",
        "cpus": 1, "memory_mib": 512, "mounts": []
    }))
    .unwrap()
}

async fn shell(client: &Client, script: &str) -> io::Result<serde_json::Value> {
    let readiness = client.request(serde_json::json!({
        "jsonrpc": "2.0", "id": 0, "eip_session": "ses_vmtest", "eip_owner_epoch": 1,
        "method": "environment.readiness", "params": {"context": {"operation_id": "op_ready", "timeout_ms": 10000}}
    }), Default::default()).await?;
    let value: serde_json::Value =
        serde_json::from_slice(&readiness.payload).map_err(io::Error::other)?;
    readiness.handoff.complete();
    if value["result"]["ready"] != true {
        return Err(io::Error::other(format!("guest readiness failed: {value}")));
    }
    let response = client.request(serde_json::json!({
        "jsonrpc": "2.0", "id": 1, "eip_session": "ses_vmtest", "eip_owner_epoch": 1,
        "method": "shell.exec", "params": {
            "context": {"operation_id": "op_vmtest", "timeout_ms": 10000},
            "request": {"command": {"kind": "shell", "profile_id": "default", "script": script}}
        }
    }), Default::default()).await?;
    let value: serde_json::Value =
        serde_json::from_slice(&response.payload).map_err(io::Error::other)?;
    response.handoff.complete();
    if let Some(error) = value.get("error") {
        return Err(io::Error::other(error.to_string()));
    }
    Ok(value["result"].clone())
}

async fn smoke(kind: eip::VmBackend) {
    let executable = PathBuf::from(
        std::env::var_os("A13N_ENVD_TEST_BINARY")
            .expect("build Envd and set A13N_ENVD_TEST_BINARY"),
    );
    let directory = tempfile::tempdir().unwrap();
    let mut plan = Plan::prepare(
        &isolation(kind),
        worker(),
        directory.path(),
        &executable,
        &Sandbox::Disabled {},
        &[],
        &[],
    )
    .await
    .unwrap();
    let mut backend = Backend::new(kind, &plan.home).await.unwrap();
    // Always remove the VM before propagating a failed assertion or RPC error.
    let result: io::Result<(super::super::worker::WorkerReady, bool)> = async {
        backend.start(&plan).await?;
        let client = plan.connect().await?;
        let ready = client.ready.clone();
        let result = shell(
            &client,
            "test $(id -u) = 0 && test $(id -g) = 0 && printf vm-worker",
        )
        .await?;
        if result["status"]["exit_code"] != 0 {
            return Err(io::Error::other(format!("guest command failed: {result}")));
        }
        let closed = client.close_worker().await?;
        client.shutdown();
        Ok((ready, closed))
    }
    .await;
    backend.remove().await.expect("remove test VM");
    // Removal is idempotent and does not restart a stopped worker.
    backend.remove().await.expect("remove absent test VM");
    let (ready, closed) = result.unwrap();
    assert_eq!(ready.working_directory, "/tmp");
    assert_eq!(ready.path_style, eip::PathStyle::Posix);
    assert!(
        ready
            .available_methods
            .iter()
            .any(|method| method == "file.stat")
    );
    assert!(!plan.control.join("bootstrap.json").exists());
    assert!(closed);
}

#[cfg(feature = "vm-boxlite")]
#[tokio::test]
#[ignore = "requires KVM, image registry access and A13N_ENVD_TEST_BINARY"]
async fn boxlite_real_worker_ready_and_close() {
    smoke(eip::VmBackend::Boxlite).await;
}

#[cfg(feature = "vm-microsandbox")]
#[tokio::test]
#[ignore = "requires KVM, image registry access and A13N_ENVD_TEST_BINARY"]
async fn microsandbox_real_worker_ready_and_close() {
    smoke(eip::VmBackend::Microsandbox).await;
}

async fn mounts_read_only_and_read_write(kind: eip::VmBackend) {
    let executable = PathBuf::from(
        std::env::var_os("A13N_ENVD_TEST_BINARY")
            .expect("build Envd and set A13N_ENVD_TEST_BINARY"),
    );
    let directory = tempfile::tempdir().unwrap();
    let workspace = tempfile::tempdir().unwrap();
    let mut isolation = isolation(kind);
    for (name, access) in [
        ("ro", eip::GrantAccess::ReadOnly),
        ("rw", eip::GrantAccess::ReadWrite),
    ] {
        let source = workspace.path().join(name);
        fs::create_dir(&source).unwrap();
        fs::set_permissions(&source, fs::Permissions::from_mode(0o777)).unwrap();
        fs::write(source.join("identity"), b"original").unwrap();
        fs::set_permissions(source.join("identity"), fs::Permissions::from_mode(0o666)).unwrap();
        isolation.mounts.push(eip::VmMount {
            source: source.to_str().unwrap().into(),
            target: format!("/mnt/{name}"),
            access,
        });
    }
    let mut plan = Plan::prepare(
        &isolation,
        worker(),
        directory.path(),
        &executable,
        &Sandbox::Disabled {},
        &[],
        &[],
    )
    .await
    .unwrap();
    let mut backend = Backend::new(isolation.backend, &plan.home).await.unwrap();
    let result: io::Result<Client> = async {
        backend.start(&plan).await?;
        let client = plan.connect().await?;
        let result = shell(&client, r#"set -eu
 test "$(cat /mnt/ro/identity)" = original
 test "$(cat /mnt/rw/identity)" = original
 if (printf forbidden > /mnt/ro/identity) 2>/dev/null; then exit 10; fi
 if (touch /mnt/ro/forbidden) 2>/dev/null; then exit 11; fi
 printf published > /mnt/rw/candidate
 sync /mnt/rw/candidate
 mv /mnt/rw/candidate /mnt/rw/published
 sync /mnt/rw
 "#).await?;
        if result["status"]["exit_code"] != 0 {
            return Err(io::Error::other(format!("mount command failed: {result}")));
        }
        for (method, params) in [
            ("file.write_text", serde_json::json!({"context":{"operation_id":"op_write"}, "path":{"path":"/mnt/rw/api-published"}, "mode":"create", "text":"engine-publication"})),
            ("file.open_writer", serde_json::json!({"context":{"operation_id":"op_upload"}, "path":{"path":"/mnt/rw/incomplete"}, "mode":"create"})),
        ] {
            let response = client.request(serde_json::json!({"jsonrpc":"2.0", "id":2, "eip_session":"ses_vmtest", "eip_owner_epoch":1, "method":method, "params":params}), Default::default()).await?;
            let value: serde_json::Value = serde_json::from_slice(&response.payload).map_err(io::Error::other)?;
            response.handoff.complete();
            if value.get("error").is_some() { return Err(io::Error::other(value.to_string())); }
        }
        let stage = fs::read_dir(workspace.path().join("rw"))?.filter_map(Result::ok)
            .find(|entry| entry.file_name().to_string_lossy().starts_with(".eip-stage-vm-"))
            .ok_or_else(|| io::Error::other("missing registered staging directory"))?;
        if fs::read_dir(stage.path())?.count() != 1 { return Err(io::Error::other("upload did not use registered staging")); }
        Ok(client)
    }.await;
    backend.remove().await.expect("remove test VM");
    staging::reclaim(directory.path()).unwrap();
    result.unwrap().shutdown();
    assert_eq!(
        fs::read(workspace.path().join("rw/api-published")).unwrap(),
        b"engine-publication"
    );
    assert!(!workspace.path().join("rw/incomplete").exists());
    assert!(
        !fs::read_dir(workspace.path().join("rw"))
            .unwrap()
            .any(|entry| entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".eip-stage-vm-"))
    );
    assert_eq!(
        fs::read(workspace.path().join("rw/published")).unwrap(),
        b"published"
    );
    assert_eq!(
        fs::read(workspace.path().join("ro/identity")).unwrap(),
        b"original"
    );
    assert!(!workspace.path().join("ro/forbidden").exists());
}

#[cfg(feature = "vm-boxlite")]
#[tokio::test]
#[ignore = "requires KVM, image registry access and A13N_ENVD_TEST_BINARY"]
async fn boxlite_mount_access() {
    mounts_read_only_and_read_write(eip::VmBackend::Boxlite).await;
}

#[cfg(feature = "vm-microsandbox")]
#[tokio::test]
#[ignore = "requires KVM, image registry access and A13N_ENVD_TEST_BINARY"]
async fn microsandbox_mount_access() {
    mounts_read_only_and_read_write(eip::VmBackend::Microsandbox).await;
}
