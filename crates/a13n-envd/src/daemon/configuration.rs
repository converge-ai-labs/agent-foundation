//! Resolve one Session's immutable execution configuration within launch authority.
use crate::{
    config::Config,
    eip,
    execution::boundary::{self, Access, Egress, Grant, GrantSource, Sandbox},
};
use std::{collections::BTreeMap, io, path::Path};

impl Config {
    /// Uses the existing engine configuration after validating the request. The
    /// Device configuration remains immutable; only the selected copy reaches
    /// Session construction and worker launch.
    pub(crate) fn for_session(&self, request: &eip::SessionConfiguration) -> io::Result<Self> {
        if let Some(eip::SessionIsolation::Vm(vm)) = &request.isolation {
            return self.for_vm_session(request, vm);
        }
        let mut selected = self.clone();
        if let Some(sandbox) = &request.sandbox {
            selected.sandbox = match sandbox {
                eip::SandboxPolicy::Disabled(_) => Sandbox::Disabled {},
                eip::SandboxPolicy::Restricted(policy) => Sandbox::Restricted {
                    full_disk_read: policy.full_disk_read,
                    grants: policy
                        .grants
                        .iter()
                        .map(|grant| {
                            Ok(Grant {
                                path: crate::device_path::to_native(&grant.path)
                                    .map_err(|_| io::Error::other("invalid Sandbox grant path"))?,
                                access: match grant.access {
                                    eip::GrantAccess::ReadOnly => Access::ReadOnly,
                                    eip::GrantAccess::ReadWrite => Access::ReadWrite,
                                },
                            })
                        })
                        .collect::<io::Result<_>>()?,
                },
            };
            // Resolve the name and identity from the same opened object. A second
            // pathname lookup could authorize a symlink replacement outside the ceiling.
            let mut sources = BTreeMap::new();
            selected
                .sandbox
                .validate_with(&self.bootstrap_files, |path| {
                    let file = self.open_grant(path)?;
                    let resolved = boundary::grant_path(&file)?;
                    #[cfg(target_os = "linux")]
                    let resolved = if self.managed {
                        Path::new("/").join(
                            resolved
                                .strip_prefix(crate::execution::management::ORIGINAL)
                                .map_err(io::Error::other)?,
                        )
                    } else {
                        resolved
                    };
                    sources.insert(resolved.clone(), boundary::grant_identity(&file)?);
                    Ok(resolved)
                })?;
            if !self.sandbox.permits(&selected.sandbox) {
                return Err(io::Error::other(
                    "Session sandbox exceeds deployment grants",
                ));
            }
            // A deployment grant is an object, not permission to follow a
            // replacement directory at the same pathname.
            if self.grant_sources != self.session_grant_sources(&self.sandbox)? {
                return Err(io::Error::other("Deployment grant directory was replaced"));
            }
            selected.grant_sources = match &selected.sandbox {
                Sandbox::Disabled {} => Vec::new(),
                Sandbox::Restricted { grants, .. } => grants
                    .iter()
                    .map(|grant| sources[&grant.path].clone())
                    .collect(),
            };
        }
        selected.egress = request.egress_mode.map_or(self.egress, |mode| match mode {
            eip::EgressMode::Inherit => Egress::Inherit {},
            eip::EgressMode::Deny => Egress::Deny {},
            eip::EgressMode::Controlled => Egress::Controlled {},
        });
        if (self.egress == Egress::Deny {} && selected.egress != Egress::Deny {})
            || (self.egress.controlled() && selected.egress == Egress::Inherit {})
        {
            return Err(io::Error::other("Session egress exceeds deployment policy"));
        }
        if request.egress.is_some() != selected.egress.controlled() {
            return Err(io::Error::other(
                "Egress policy is required exactly for controlled Sessions",
            ));
        }
        if !cfg!(target_os = "linux")
            && (selected.egress.controlled()
                || (!selected.sandbox.restricted() && selected.egress != Egress::Inherit {})
                || (selected.sandbox.restricted() && !cfg!(any(target_os = "macos", windows))))
        {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "Session boundary is unavailable on this platform",
            ));
        }
        #[cfg(target_os = "linux")]
        {
            if boundary::managed(&selected.sandbox, selected.egress) && !self.managed {
                return Err(io::Error::new(
                    io::ErrorKind::Unsupported,
                    "Session network isolation requires a managed launcher",
                ));
            }
            if self.managed
                && selected.sandbox.restricted()
                && !Path::new(crate::execution::management::WORKER_IMAGE).is_dir()
            {
                return Err(io::Error::new(
                    io::ErrorKind::Unsupported,
                    "Restricted worker runtime is unavailable",
                ));
            }
        }
        match request.commands {
            Some(false) => selected.command = None,
            Some(true) if self.command.is_none() => {
                return Err(io::Error::other(
                    "Commands are disabled by deployment policy",
                ));
            }
            _ => {}
        }
        selected.computer_use &=
            !selected.sandbox.restricted() && selected.egress == Egress::Inherit {};
        if let Some(cwd) = &request.working_directory {
            selected.default_working_directory = cwd.clone();
        }
        if let Some(lifecycle) = &request.lifecycle {
            selected.session_idle_timeout =
                std::time::Duration::from_millis(lifecycle.idle_timeout_ms);
            selected.session_max_lifetime = lifecycle
                .max_lifetime_ms
                .map(std::time::Duration::from_millis);
        }
        Ok(selected)
    }

    fn for_vm_session(
        &self,
        request: &eip::SessionConfiguration,
        vm: &eip::VmIsolation,
    ) -> io::Result<Self> {
        let supported = cfg!(target_os = "linux")
            && match vm.backend {
                eip::VmBackend::Boxlite => cfg!(feature = "vm-boxlite"),
                eip::VmBackend::Microsandbox => cfg!(feature = "vm-microsandbox"),
            };
        if !supported {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "VM backend is unavailable in this build",
            ));
        }
        if self.egress != (Egress::Inherit {})
            || request
                .egress_mode
                .is_some_and(|mode| mode != eip::EgressMode::Inherit)
            || request.egress.is_some()
            || matches!(request.sandbox, Some(eip::SandboxPolicy::Restricted(_)))
        {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "VM Sessions require inherit networking and no nested native Sandbox",
            ));
        }
        #[cfg(any(target_os = "linux", target_os = "macos", windows))]
        if self.managed {
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "VM Sessions require an unmanaged Device launcher",
            ));
        }
        let mut selected = self.clone();
        selected.sandbox = Sandbox::Disabled {};
        selected.grant_sources.clear();
        selected.computer_use = false;
        // The VM is the isolation boundary. Keep the SDK's guest-root identity,
        // including its mapping of Host-owned shared files into the guest.
        selected.execution = Some(crate::execution::Identity { uid: 0, gid: 0 });
        selected.allow_sudo = false;
        selected.default_working_directory = request
            .working_directory
            .clone()
            .unwrap_or_else(|| "/tmp".into());
        match request.commands {
            Some(false) => selected.command = None,
            Some(true) if self.command.is_none() => {
                return Err(io::Error::other(
                    "Commands are disabled by deployment policy",
                ));
            }
            _ => {}
        }
        if let Some(command) = &mut selected.command {
            if !command.full_control {
                return Err(io::Error::new(
                    io::ErrorKind::Unsupported,
                    "Host executable allowlists cannot authorize guest executables",
                ));
            }
            // Guest executable paths and environment are independent of the Host.
            command.base_environment = [
                ("PATH".into(), "/usr/local/bin:/usr/bin:/bin".into()),
                ("HOME".into(), "/tmp".into()),
                ("TMPDIR".into(), "/tmp".into()),
            ]
            .into();
            command.trusted_executable_roots =
                vec!["/usr/local/bin".into(), "/usr/bin".into(), "/bin".into()];
            command.shell_profiles = vec![crate::config::TrustedShellProfileConfig {
                profile_id: "default".into(),
                display_name: "POSIX shell".into(),
                native_executable: "/bin/sh".into(),
                fixed_arguments: vec!["-c".into()],
                safe_base_environment: BTreeMap::new(),
                executable_search_roots: command.trusted_executable_roots.clone(),
                max_script_bytes: command.max_argument_bytes as u64,
                allow_login_mode: false,
            }];
        }
        if let Some(lifecycle) = &request.lifecycle {
            selected.session_idle_timeout =
                std::time::Duration::from_millis(lifecycle.idle_timeout_ms);
            selected.session_max_lifetime = lifecycle
                .max_lifetime_ms
                .map(std::time::Duration::from_millis);
        }
        Ok(selected)
    }

    fn open_grant(&self, path: &Path) -> io::Result<std::fs::File> {
        #[cfg(target_os = "linux")]
        if self.managed {
            return crate::execution::management::open_original_directory(path);
        }
        boundary::open_grant(path)
    }

    fn session_grant_sources(&self, sandbox: &Sandbox) -> io::Result<Vec<GrantSource>> {
        match sandbox {
            Sandbox::Disabled {} => Ok(Vec::new()),
            Sandbox::Restricted { grants, .. } => grants
                .iter()
                .map(|grant| boundary::grant_identity(&self.open_grant(&grant.path)?))
                .collect(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn request(value: serde_json::Value) -> eip::SessionConfiguration {
        serde_json::from_value(value).unwrap()
    }

    #[test]
    fn session_selection_does_not_mutate_device_defaults_or_enable_commands() {
        let config = Config::for_test("configuration-test");
        let selected = config
            .for_session(&request(json!({
                "commands": false, "working_directory": "/selected",
                "lifecycle": {"idle_timeout_ms": 1234, "max_lifetime_ms": 5678}
            })))
            .unwrap();
        assert_ne!(
            selected.default_working_directory,
            config.default_working_directory
        );
        assert_eq!(selected.session_idle_timeout.as_millis(), 1234);
        assert_eq!(selected.session_max_lifetime.unwrap().as_millis(), 5678);
        assert!(config.session_max_lifetime.is_none());
        assert!(
            config
                .for_session(&request(json!({"commands": true})))
                .is_err()
        );
    }

    #[test]
    fn vm_selection_never_falls_back_or_reuses_host_execution_paths() {
        let mut config = Config::for_test("vm-configuration");
        config.default_working_directory = "/host-only".into();
        config.execution = Some(crate::execution::Identity {
            uid: 2345,
            gid: 3456,
        });
        for (backend, enabled) in [
            ("boxlite", cfg!(feature = "vm-boxlite")),
            ("microsandbox", cfg!(feature = "vm-microsandbox")),
        ] {
            let mut value = json!({"isolation": {"mode": "vm", "backend": backend, "image": "ubuntu:24.04", "cpus": 1, "memory_mib": 512, "mounts": []}});
            let selected = config.for_session(&request(value.clone()));
            if !cfg!(target_os = "linux") || !enabled {
                assert_eq!(selected.unwrap_err().kind(), io::ErrorKind::Unsupported);
                continue;
            }
            let selected = selected.unwrap();
            assert_eq!(selected.default_working_directory, "/tmp");
            assert_eq!(selected.execution.unwrap().uid, 0);
            assert!(!selected.allow_sudo);
            assert!(!selected.computer_use);
            assert_eq!(config.default_working_directory, "/host-only");
            value["egress_mode"] = json!("deny");
            assert_eq!(
                config
                    .for_session(&request(value.clone()))
                    .unwrap_err()
                    .kind(),
                io::ErrorKind::Unsupported
            );
            value.as_object_mut().unwrap().remove("egress_mode");
            value["sandbox"] = json!({"mode": "restricted", "grants": []});
            assert_eq!(
                config.for_session(&request(value)).unwrap_err().kind(),
                io::ErrorKind::Unsupported
            );
        }
    }

    #[test]
    fn mandatory_network_restrictions_cannot_be_relaxed() {
        for mode in [Egress::Deny {}, Egress::Controlled {}] {
            let mut config = Config::for_test("network-ceiling");
            config.egress = mode;
            assert!(
                config
                    .for_session(&request(json!({"egress_mode": "inherit"})))
                    .is_err()
            );
        }
        let config = Config::for_test("network-launcher");
        assert!(config.for_session(&request(json!({
            "egress_mode": "controlled", "egress": {"destinations": {"mode": "public"}, "secrets": []}
        }))).is_err());
        assert!(config.for_session(&request(json!({
            "egress_mode": "inherit", "egress": {"destinations": {"mode": "public"}, "secrets": []}
        }))).is_err());
    }

    #[test]
    fn sandbox_requests_narrow_checked_deployment_grants() {
        let mut random = [0_u8; 8];
        getrandom::fill(&mut random).unwrap();
        let root = std::env::temp_dir().join(format!(
            "a13n-session-policy-{:016x}",
            u64::from_le_bytes(random)
        ));
        std::fs::create_dir(&root).unwrap();
        std::fs::create_dir(root.join("child")).unwrap();
        let root = std::fs::canonicalize(root).unwrap();
        let mut config = Config::for_test("grant-ceiling");
        config.sandbox = Sandbox::Restricted {
            grants: vec![Grant {
                path: root.clone(),
                access: Access::ReadOnly,
            }],
            full_disk_read: false,
        };
        config.grant_sources = config.sandbox.sources().unwrap();
        let child = crate::device_path::from_native(&root.join("child")).unwrap();
        let selected = config.for_session(&request(json!({
            "sandbox": {"mode": "restricted", "grants": [{"path": child, "access": "read_only"}]}
        }))).unwrap();
        assert_ne!(selected.sandbox, config.sandbox);
        assert_eq!(selected.grant_sources.len(), 1);
        for sandbox in [
            json!({"mode": "disabled"}),
            json!({"mode": "restricted", "grants": [{"path": child, "access": "read_write"}]}),
        ] {
            assert!(
                config
                    .for_session(&request(json!({"sandbox": sandbox})))
                    .is_err()
            );
        }
        #[cfg(unix)]
        {
            let outside = root.with_extension("outside");
            std::fs::create_dir(&outside).unwrap();
            std::os::unix::fs::symlink(&outside, root.join("escape")).unwrap();
            assert!(
                config
                    .for_session(&request(json!({
                        "sandbox": {"mode": "restricted", "grants": [{
                            "path": crate::device_path::from_native(&root.join("escape")).unwrap(),
                            "access": "read_only"
                        }]}
                    })))
                    .is_err()
            );
            std::fs::remove_dir(&outside).unwrap();
        }
        // Even an explicitly repeated grant must not authorize a replaced object.
        std::fs::rename(&root, root.with_extension("retired")).unwrap();
        std::fs::create_dir_all(root.join("child")).unwrap();
        assert!(config.for_session(&request(json!({
            "sandbox": {"mode": "restricted", "grants": [{"path": child, "access": "read_only"}]}
        }))).is_err());
        std::fs::remove_dir_all(&root).unwrap();
        std::fs::remove_dir_all(root.with_extension("retired")).unwrap();
    }
}
