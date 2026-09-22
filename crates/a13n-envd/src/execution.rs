//! Trusted startup identity, shared by command execution and filesystem workers.
use serde::{Deserialize, Serialize};

#[cfg(target_os = "linux")]
pub(crate) mod discovery;
#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub(crate) mod management;
#[cfg(target_os = "linux")]
pub(crate) use linux::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Identity {
    pub uid: u32,
    pub gid: u32,
}

/// Partial startup layer; UID/GID are resolved together after configuration merge.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Options {
    pub uid: Option<u32>,
    pub gid: Option<u32>,
    pub allow_sudo: Option<bool>,
}

impl Options {
    pub(crate) fn identity(&self) -> Result<Option<Identity>, &'static str> {
        match (self.uid, self.gid) {
            (None, None) => Ok(None),
            (Some(uid), Some(gid)) => Ok(Some(Identity { uid, gid })),
            _ => Err("execution.uid and execution.gid must be configured together"),
        }
    }

    pub(crate) fn allow_sudo(&self) -> Result<bool, &'static str> {
        let enabled = self.allow_sudo.unwrap_or(true);
        if !enabled && !cfg!(target_os = "linux") {
            return Err("disabling privilege escalation requires Linux");
        }
        Ok(enabled)
    }
}

impl Identity {
    pub(crate) fn validate(self) -> Result<Self, &'static str> {
        if self.uid == u32::MAX || self.gid == u32::MAX {
            return Err("execution.uid and execution.gid must be valid native IDs");
        }
        Ok(self)
    }
}

/// With no configured identity, retain the launcher's account, including root.
/// Only the launcher can select a different provisioned account.
pub(crate) fn resolve(configured: Option<Identity>) -> Result<Option<Identity>, &'static str> {
    #[cfg(target_os = "linux")]
    {
        let uid = unsafe { libc::geteuid() };
        let gid = unsafe { libc::getegid() };
        let identity = configured.unwrap_or(Identity { uid, gid }).validate()?;
        if uid != 0 && (identity.uid != uid || identity.gid != gid) {
            return Err("changing execution identity requires a root launcher");
        }
        Ok(Some(identity))
    }
    #[cfg(not(target_os = "linux"))]
    {
        if configured.is_some() {
            return Err("execution UID/GID configuration requires Linux");
        }
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_accepts_root_and_rejects_native_sentinel_ids() {
        for (uid, gid) in [(0, 0), (0, 1000), (1000, 0), (12345, 12346)] {
            let identity = Identity { uid, gid };
            assert_eq!(identity.validate(), Ok(identity));
        }
        for (uid, gid) in [(u32::MAX, 1000), (1000, u32::MAX)] {
            assert!(Identity { uid, gid }.validate().is_err());
        }
    }

    #[test]
    fn identity_rejects_partial_and_obsolete_configuration() {
        for value in [
            r#"{"uid":1000}"#,
            r#"{"gid":1000}"#,
            r#"{"uid":1000,"gid":1000,"isolation":"required"}"#,
            r#"{"uid":-1,"gid":1000}"#,
        ] {
            assert!(serde_json::from_str::<Identity>(value).is_err());
        }
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn unconfigured_identity_never_guesses_an_account() {
        let uid = unsafe { libc::geteuid() };
        let gid = unsafe { libc::getegid() };
        assert_eq!(resolve(None), Ok(Some(Identity { uid, gid })));
        if uid == 0 {
            let configured = Identity {
                uid: 12345,
                gid: 12346,
            };
            assert_eq!(resolve(Some(configured)), Ok(Some(configured)));
        } else {
            assert!(resolve(Some(Identity { uid: uid + 1, gid })).is_err());
        }
    }
}
