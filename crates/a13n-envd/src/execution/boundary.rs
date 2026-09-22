//! Immutable Device launch policy. Session policy never changes this boundary.
use serde::{Deserialize, Serialize};
use std::{
    io,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Sandbox {
    Disabled {},
    Restricted { grants: Vec<Grant> },
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Grant {
    pub path: PathBuf,
    pub access: Access,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Access {
    ReadOnly,
    ReadWrite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Egress {
    Inherit {},
    Deny {},
    Controlled {},
}

impl Default for Egress {
    fn default() -> Self {
        Self::Inherit {}
    }
}

impl Default for Sandbox {
    fn default() -> Self {
        Self::Disabled {}
    }
}

impl Egress {
    pub(crate) fn controlled(self) -> bool {
        self == Self::Controlled {}
    }
}

/// Native directory objects authorized when the Device is prepared. Reopening
/// a changed path must fail rather than silently authorize its replacement.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub(crate) struct GrantSource {
    pub device: u64,
    pub inode: u64,
}

#[cfg(unix)]
impl GrantSource {
    pub(crate) fn from_metadata(metadata: &std::fs::Metadata) -> Self {
        use std::os::unix::fs::MetadataExt;
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }

    pub(crate) fn check(&self, file: &std::fs::File) -> io::Result<()> {
        let metadata = file.metadata()?;
        if !metadata.is_dir() || *self != Self::from_metadata(&metadata) {
            return Err(io::Error::other("Sandbox grant directory was replaced"));
        }
        Ok(())
    }
}

impl Sandbox {
    pub(crate) fn sources(&self) -> io::Result<Vec<GrantSource>> {
        #[cfg(unix)]
        if let Self::Restricted { grants } = self {
            return grants
                .iter()
                .map(|grant| Ok(GrantSource::from_metadata(&std::fs::metadata(&grant.path)?)))
                .collect();
        }
        Ok(Vec::new())
    }

    pub(crate) fn restricted(&self) -> bool {
        matches!(self, Self::Restricted { .. })
    }

    pub(crate) fn validate(&mut self, protected: &[PathBuf]) -> io::Result<()> {
        let Self::Restricted { grants } = self else {
            return Ok(());
        };
        for grant in grants.iter_mut() {
            if !grant.path.is_absolute() || !grant.path.is_dir() {
                return Err(io::Error::other(
                    "Sandbox grants must be existing absolute directories",
                ));
            }
            grant.path = std::fs::canonicalize(&grant.path)?;
            if protected.iter().any(|path| overlaps(&grant.path, path))
                || [
                    "/proc",
                    "/sys",
                    "/dev",
                    "/run",
                    "/usr",
                    "/bin",
                    "/sbin",
                    "/lib",
                    "/lib64",
                    "/etc",
                    "/System",
                    "/private/etc",
                    "/private/var/db",
                ]
                .iter()
                .any(|path| overlaps(&grant.path, Path::new(path)))
            {
                return Err(io::Error::other("Sandbox grant overlaps a protected path"));
            }
        }
        grants.sort_by(|a, b| a.path.cmp(&b.path));
        grants.dedup();
        for (index, grant) in grants.iter().enumerate() {
            if grants[..index]
                .iter()
                .any(|previous| overlaps(&previous.path, &grant.path))
            {
                return Err(io::Error::other(
                    "Sandbox grants overlap or have conflicting access",
                ));
            }
        }
        Ok(())
    }
}

fn overlaps(a: &Path, b: &Path) -> bool {
    a.starts_with(b) || b.starts_with(a)
}

/// Nonsecret Device policy. Session publication uses the same descriptor after
/// the worker handshake; destinations and credentials have a separate lifetime.
pub(crate) fn descriptor(
    config: &crate::config::Config,
) -> io::Result<crate::eip::ExecutionBoundary> {
    use crate::eip;
    use sha2::{Digest, Sha256};
    let sandbox = match &config.sandbox {
        Sandbox::Disabled {} => eip::SandboxPolicy::Disabled(eip::DisabledSandbox {
            mode: "disabled".into(),
        }),
        Sandbox::Restricted { grants } => eip::SandboxPolicy::Restricted(eip::RestrictedSandbox {
            mode: "restricted".into(),
            grants: grants
                .iter()
                .map(|grant| {
                    Ok(eip::SandboxGrant {
                        path: crate::device_path::from_native(&grant.path)
                            .map_err(|_| io::Error::other("invalid grant path"))?,
                        access: match grant.access {
                            Access::ReadOnly => eip::GrantAccess::ReadOnly,
                            Access::ReadWrite => eip::GrantAccess::ReadWrite,
                        },
                    })
                })
                .collect::<io::Result<_>>()?,
        }),
    };
    let backend = if config.sandbox.restricted() {
        if cfg!(target_os = "macos") {
            eip::ExecutionBackend::MacosSeatbelt
        } else {
            eip::ExecutionBackend::LinuxBubblewrap
        }
    } else if managed(&config.sandbox, config.egress) {
        eip::ExecutionBackend::LinuxManaged
    } else {
        eip::ExecutionBackend::Native
    };
    let mut boundary = eip::ExecutionBoundary {
        identity: config.execution.map(|identity| eip::ExecutionIdentity {
            uid: identity.uid,
            gid: identity.gid,
        }),
        sandbox,
        egress: match config.egress {
            Egress::Inherit {} => eip::EgressMode::Inherit,
            Egress::Deny {} => eip::EgressMode::Deny,
            Egress::Controlled {} => eip::EgressMode::Controlled,
        },
        // Seatbelt confines access across exec, but does not provide Linux's
        // no_new_privs credential guarantee. Report only the enforced property.
        privilege_gain_blocked: cfg!(target_os = "linux")
            && (config.sandbox.restricted() || !config.allow_sudo),
        backend,
        policy_digest: String::new(),
    };
    let mut value = serde_json::to_value(&boundary).map_err(io::Error::other)?;
    value
        .as_object_mut()
        .expect("boundary object")
        .remove("policy_digest");
    // serde_json::Value maps sort keys; no secrets or runtime identity enter this hash.
    boundary.policy_digest = format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&value).map_err(io::Error::other)?)
    );
    Ok(boundary)
}

pub(crate) fn managed(sandbox: &Sandbox, egress: Egress) -> bool {
    egress.controlled() || (!sandbox.restricted() && egress == Egress::Deny {})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tagged_policy_requires_explicit_mode_and_rejects_unused_fields() {
        for input in [
            "{}",
            r#"{"enabled":true}"#,
            r#"{"mode":"inherit","hosts":[]}"#,
        ] {
            assert!(serde_json::from_str::<Egress>(input).is_err());
        }
        for input in [
            "{}",
            r#"{"mode":"disabled","grants":[]}"#,
            r#"{"mode":"restricted"}"#,
        ] {
            assert!(serde_json::from_str::<Sandbox>(input).is_err());
        }
        assert_eq!(
            serde_json::from_str::<Egress>(r#"{"mode":"inherit"}"#).unwrap(),
            Egress::Inherit {}
        );
        assert!(serde_json::from_str::<Sandbox>(r#"{"mode":"restricted","grants":[]}"#).is_ok());
    }

    #[test]
    fn grants_are_canonical_unique_and_nonoverlapping() {
        let mut random = [0_u8; 8];
        getrandom::fill(&mut random).unwrap();
        let path =
            std::env::temp_dir().join(format!("a13n-grant-{:016x}", u64::from_le_bytes(random)));
        std::fs::create_dir(&path).unwrap();
        let grant = Grant {
            path: path.clone(),
            access: Access::ReadWrite,
        };
        let mut sandbox = Sandbox::Restricted {
            grants: vec![grant.clone(), grant.clone()],
        };
        sandbox.validate(&[]).unwrap();
        assert_eq!(
            sandbox,
            Sandbox::Restricted {
                grants: vec![Grant {
                    path: std::fs::canonicalize(&path).unwrap(),
                    access: Access::ReadWrite,
                }]
            }
        );
        // Config supplies canonical bootstrap paths, including on macOS where
        // the temporary directory may be reached through /var -> /private/var.
        assert!(
            sandbox
                .validate(&[std::fs::canonicalize(&path).unwrap()])
                .is_err()
        );
        let mut conflict = Sandbox::Restricted {
            grants: vec![
                grant,
                Grant {
                    path,
                    access: Access::ReadOnly,
                },
            ],
        };
        assert!(conflict.validate(&[]).is_err());
        let mut parent = Sandbox::Restricted {
            grants: vec![Grant {
                path: PathBuf::from("/"),
                access: Access::ReadWrite,
            }],
        };
        assert!(parent.validate(&[]).is_err());
        if let Sandbox::Restricted { grants } = sandbox {
            std::fs::remove_dir(&grants[0].path).unwrap();
        }
    }

    #[test]
    fn only_network_boundaries_requiring_privilege_use_management() {
        let restricted = Sandbox::Restricted { grants: vec![] };
        assert!(!managed(&restricted, Egress::Deny {}));
        assert!(!managed(&restricted, Egress::Inherit {}));
        assert!(managed(&restricted, Egress::Controlled {}));
        assert!(managed(&Sandbox::Disabled {}, Egress::Deny {}));
        assert!(!managed(&Sandbox::Disabled {}, Egress::Inherit {}));
    }
}
