use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::sync::{Arc, RwLock};

use hyper::header::{HeaderMap, HeaderValue};
use tokio::sync::watch;

const MAX_HOSTS: usize = 256;
const MAX_SECRETS: usize = 64;
const MAX_SECRET_BYTES: usize = 8192;

/// Write-only input. Deliberately has neither Serialize nor a value-bearing Debug.
#[derive(Clone)]
pub(crate) struct SecretInput {
    pub env: String,
    pub value: String,
    pub inject_hosts: Vec<String>,
}

impl fmt::Debug for SecretInput {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("SecretInput([REDACTED])")
    }
}

#[derive(Debug)]
pub(crate) struct PolicyInput {
    pub destinations: crate::eip::EgressDestinations,
    pub secrets: Vec<SecretInput>,
}

#[cfg(test)]
impl Default for PolicyInput {
    fn default() -> Self {
        Self {
            destinations: public_destinations(),
            secrets: Vec::new(),
        }
    }
}

/// One atomic update, with revision checking to prevent lost concurrent changes.
#[derive(Debug)]
pub(crate) struct Update {
    pub expected_revision: u64,
    /// Omitted leaves policy unchanged; an explicit object replaces it.
    pub destinations: Option<crate::eip::EgressDestinations>,
    pub set_secrets: Vec<SecretInput>,
    pub remove_secrets: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PolicyError {
    Invalid,
    Conflict,
    Denied,
    Closed,
}

impl Drop for SecretInput {
    fn drop(&mut self) {
        use zeroize::Zeroize;
        self.value.zeroize();
    }
}

struct Secret {
    value: String,
    sentinel: String,
    hosts: BTreeSet<String>,
}

impl Drop for Secret {
    fn drop(&mut self) {
        use zeroize::Zeroize;
        self.value.zeroize();
    }
}

/// A request retains this snapshot through response scrubbing, including rotation.
#[derive(Clone)]
pub(crate) struct Snapshot {
    pub revision: u64,
    hosts: Option<BTreeSet<String>>,
    secrets: BTreeMap<String, Arc<Secret>>,
    // Removed markers must never be reused or silently forwarded to upstream.
    retired: BTreeSet<String>,
}

impl fmt::Debug for Snapshot {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Snapshot")
            .field("revision", &self.revision)
            .finish_non_exhaustive()
    }
}

pub(crate) struct Policy {
    current: RwLock<Option<Arc<Snapshot>>>,
    changes: watch::Sender<u64>,
}

impl From<crate::eip::EgressPolicy> for PolicyInput {
    fn from(input: crate::eip::EgressPolicy) -> Self {
        Self {
            destinations: input.destinations,
            secrets: input.secrets.into_iter().map(SecretInput::from).collect(),
        }
    }
}
impl From<crate::eip::EgressSecret> for SecretInput {
    fn from(input: crate::eip::EgressSecret) -> Self {
        Self {
            env: input.env,
            value: input.value,
            inject_hosts: input.inject_hosts,
        }
    }
}
impl From<crate::eip::EgressUpdateParams> for Update {
    fn from(input: crate::eip::EgressUpdateParams) -> Self {
        Self {
            expected_revision: input.expected_revision,
            destinations: input.destinations,
            set_secrets: input
                .set_secrets
                .into_iter()
                .map(SecretInput::from)
                .collect(),
            remove_secrets: input.remove_secrets,
        }
    }
}

fn destination_hosts(
    input: crate::eip::EgressDestinations,
) -> Result<Option<BTreeSet<String>>, PolicyError> {
    match input {
        crate::eip::EgressDestinations::Public(_) => Ok(None),
        crate::eip::EgressDestinations::Allowlist(input) => {
            hosts(input.hosts, destination).map(Some)
        }
    }
}

pub(crate) fn public_destinations() -> crate::eip::EgressDestinations {
    crate::eip::EgressDestinations::Public(crate::eip::PublicDestinations {
        mode: "public".into(),
    })
}

pub(crate) fn allowlist_destinations(hosts: Vec<String>) -> crate::eip::EgressDestinations {
    crate::eip::EgressDestinations::Allowlist(crate::eip::AllowlistDestinations {
        mode: "allowlist".into(),
        hosts,
    })
}

impl Policy {
    pub(crate) fn from_request(input: crate::eip::EgressPolicy) -> Result<Self, PolicyError> {
        Self::new(input.into())
    }
    pub(crate) fn new(input: PolicyInput) -> Result<Self, PolicyError> {
        let mut snapshot = Snapshot {
            revision: 1,
            hosts: destination_hosts(input.destinations)?,
            secrets: BTreeMap::new(),
            retired: BTreeSet::new(),
        };
        snapshot.set(input.secrets)?;
        snapshot.validate()?;
        let (changes, _) = watch::channel(1);
        Ok(Self {
            current: RwLock::new(Some(Arc::new(snapshot))),
            changes,
        })
    }

    pub(crate) fn snapshot(&self) -> Result<Arc<Snapshot>, PolicyError> {
        self.current
            .read()
            .map_err(|_| PolicyError::Closed)?
            .clone()
            .ok_or(PolicyError::Closed)
    }

    pub(crate) fn subscribe(&self) -> watch::Receiver<u64> {
        self.changes.subscribe()
    }

    pub(crate) fn update(&self, update: Update) -> Result<Arc<Snapshot>, PolicyError> {
        let mut guard = self.current.write().map_err(|_| PolicyError::Closed)?;
        let current = guard.as_ref().ok_or(PolicyError::Closed)?;
        if current.revision != update.expected_revision {
            return Err(PolicyError::Conflict);
        }
        let mut next = (**current).clone();
        next.revision = next.revision.checked_add(1).ok_or(PolicyError::Invalid)?;
        if let Some(destinations) = update.destinations {
            next.hosts = destination_hosts(destinations)?;
        }
        let removed: BTreeSet<_> = update.remove_secrets.iter().collect();
        if removed.len() != update.remove_secrets.len()
            || update
                .set_secrets
                .iter()
                .any(|value| removed.contains(&value.env))
        {
            return Err(PolicyError::Invalid);
        }
        for env in update.remove_secrets {
            if !valid_env(&env) {
                return Err(PolicyError::Invalid);
            }
            if let Some(secret) = next.secrets.remove(&env) {
                next.retired.insert(secret.sentinel.clone());
            }
        }
        // Bound churn for one Session instead of retaining unbounded tombstones.
        if next.retired.len() > 4096 {
            return Err(PolicyError::Invalid);
        }
        next.set(update.set_secrets)?;
        next.validate()?;
        let next = Arc::new(next);
        *guard = Some(next.clone());
        self.changes.send_replace(next.revision);
        Ok(next)
    }

    pub(crate) fn close(&self) {
        if let Ok(mut current) = self.current.write() {
            *current = None;
        }
        self.changes.send_replace(0);
    }
}

impl Snapshot {
    pub fn status(&self) -> crate::eip::EgressStatus {
        crate::eip::EgressStatus {
            revision: self.revision,
            destinations: match &self.hosts {
                Some(hosts) => allowlist_destinations(hosts.iter().cloned().collect()),
                None => public_destinations(),
            },
            secrets: self
                .secrets
                .iter()
                .map(|(env, secret)| crate::eip::EgressSecretStatus {
                    env: env.clone(),
                    sentinel: secret.sentinel.clone(),
                    inject_hosts: secret.hosts.iter().cloned().collect(),
                })
                .collect(),
        }
    }

    pub fn permits(&self, host: &str) -> bool {
        if let Ok(address) = host.parse::<std::net::IpAddr>() {
            return super::address::public(address)
                && self
                    .hosts
                    .as_ref()
                    .is_some_and(|set| set.contains(&address.to_string()));
        }
        hostname(host).is_ok_and(|host| self.hosts.as_ref().is_none_or(|set| set.contains(&host)))
    }

    pub fn preserves(&self, old: &Snapshot, host: &str) -> bool {
        self.permits(host)
            && old.secrets.iter().all(|(env, previous)| {
                !previous.hosts.contains(host)
                    || self.secrets.get(env).is_some_and(|current| {
                        current.sentinel == previous.sentinel && current.hosts.contains(host)
                    })
            })
    }

    pub fn contains_marker(&self, headers: &HeaderMap) -> bool {
        headers.values().any(|value| {
            self.retired
                .iter()
                .any(|marker| contains(value.as_bytes(), marker.as_bytes()))
                || self
                    .secrets
                    .values()
                    .any(|secret| contains(value.as_bytes(), secret.sentinel.as_bytes()))
        })
    }

    pub fn environment(&self) -> BTreeMap<String, String> {
        self.secrets
            .iter()
            .map(|(env, secret)| (env.clone(), secret.sentinel.clone()))
            .collect()
    }

    /// Only header VALUES are substituted. A failed request never sees a partial map.
    pub fn inject(&self, host: &str, headers: &HeaderMap) -> Result<HeaderMap, PolicyError> {
        let host = hostname(host)?;
        if !self.permits(&host) {
            return Err(PolicyError::Denied);
        }
        let mut result = HeaderMap::with_capacity(headers.len());
        for (name, value) in headers {
            let original = value.as_bytes();
            if self
                .retired
                .iter()
                .any(|marker| contains(original, marker.as_bytes()))
            {
                return Err(PolicyError::Denied);
            }
            let mut replacements = Vec::new();
            for secret in self.secrets.values() {
                if contains(original, secret.sentinel.as_bytes()) {
                    if !secret.hosts.contains(&host) {
                        return Err(PolicyError::Denied);
                    }
                    replacements.push((secret.sentinel.as_bytes(), secret.value.as_bytes()));
                }
            }
            let replaced = super::redact::replace(original, &replacements);
            result.append(
                name.clone(),
                HeaderValue::from_bytes(&replaced).map_err(|_| PolicyError::Invalid)?,
            );
        }
        Ok(result)
    }

    pub(super) fn scrubber(&self) -> super::redact::Scrubber {
        super::redact::Scrubber::new(
            self.secrets
                .values()
                .map(|s| (s.value.as_bytes().to_vec(), s.sentinel.as_bytes().to_vec()))
                .collect(),
        )
    }

    fn set(&mut self, inputs: Vec<SecretInput>) -> Result<(), PolicyError> {
        let mut seen = BTreeSet::new();
        for mut input in inputs {
            if !valid_env(&input.env)
                || !seen.insert(input.env.clone())
                || input.value.is_empty()
                || input.value.len() > MAX_SECRET_BYTES
                || input.value.bytes().any(|b| b < 0x20 || b == 0x7f)
            {
                return Err(PolicyError::Invalid);
            }
            let hosts = hosts(std::mem::take(&mut input.inject_hosts), hostname)?;
            if hosts.is_empty() {
                return Err(PolicyError::Invalid);
            }
            let sentinel = match self.secrets.get(&input.env) {
                Some(old) => old.sentinel.clone(),
                None => self.marker(&input.env)?,
            };
            self.secrets.insert(
                std::mem::take(&mut input.env),
                Arc::new(Secret {
                    value: std::mem::take(&mut input.value),
                    sentinel,
                    hosts,
                }),
            );
        }
        Ok(())
    }

    fn marker(&self, env: &str) -> Result<String, PolicyError> {
        for _ in 0..8 {
            let mut bytes = [0u8; 4];
            getrandom::fill(&mut bytes).map_err(|_| PolicyError::Invalid)?;
            let marker = format!("a13n_{:08x}_{env}", u32::from_be_bytes(bytes));
            if !self.retired.contains(&marker)
                && self.secrets.values().all(|s| s.sentinel != marker)
            {
                return Ok(marker);
            }
        }
        Err(PolicyError::Invalid)
    }

    fn validate(&self) -> Result<(), PolicyError> {
        if self.secrets.len() > MAX_SECRETS
            || self.secrets.values().any(|s| {
                self.hosts
                    .as_ref()
                    .is_some_and(|allowed| !s.hosts.is_subset(allowed))
            })
        {
            return Err(PolicyError::Invalid);
        }
        Ok(())
    }
}

fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window == needle)
}

fn valid_env(name: &str) -> bool {
    if matches!(
        name,
        "PATH"
            | "SSL_CERT_FILE"
            | "SSL_CERT_DIR"
            | "REQUESTS_CA_BUNDLE"
            | "CURL_CA_BUNDLE"
            | "NODE_EXTRA_CA_CERTS"
    ) {
        return false;
    }

    !name.is_empty()
        && name.len() <= 128
        && name
            .bytes()
            .enumerate()
            .all(|(i, b)| b == b'_' || b.is_ascii_alphabetic() || (i > 0 && b.is_ascii_digit()))
        && !crate::config::reserved_environment_name(name)
}

fn hosts(
    values: Vec<String>,
    normalize: fn(&str) -> Result<String, PolicyError>,
) -> Result<BTreeSet<String>, PolicyError> {
    if values.len() > MAX_HOSTS {
        return Err(PolicyError::Invalid);
    }
    values.into_iter().map(|value| normalize(&value)).collect()
}

fn destination(value: &str) -> Result<String, PolicyError> {
    if let Ok(address) = value.parse::<std::net::IpAddr>() {
        if !super::address::public(address) {
            return Err(PolicyError::Invalid);
        }
        Ok(address.to_string())
    } else {
        hostname(value)
    }
}

pub(crate) fn hostname(value: &str) -> Result<String, PolicyError> {
    let value = value.strip_suffix('.').unwrap_or(value);
    if value.is_empty()
        || value.len() > 253
        || value.parse::<std::net::IpAddr>().is_ok()
        || value.split('.').any(|part| {
            part.is_empty()
                || part.len() > 63
                || part.starts_with('-')
                || part.ends_with('-')
                || !part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
        })
    {
        return Err(PolicyError::Invalid);
    }
    Ok(value.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn secret(value: &str) -> SecretInput {
        SecretInput {
            env: "GH_TOKEN".into(),
            value: value.into(),
            inject_hosts: vec!["api.github.com".into()],
        }
    }

    fn update(revision: u64) -> Update {
        Update {
            expected_revision: revision,
            destinations: None,
            set_secrets: vec![],
            remove_secrets: vec![],
        }
    }

    #[test]
    fn public_and_empty_allowlists_are_distinct() {
        let public = Policy::new(PolicyInput::default()).unwrap();
        assert!(public.snapshot().unwrap().permits("example.com"));
        let denied = Policy::new(PolicyInput {
            destinations: crate::egress::policy::allowlist_destinations(vec![]),
            secrets: vec![],
        })
        .unwrap();
        assert!(!denied.snapshot().unwrap().permits("example.com"));
        assert!(
            Policy::new(PolicyInput {
                destinations: crate::egress::policy::allowlist_destinations(vec![]),
                secrets: vec![secret("real-token")]
            })
            .is_err()
        );
    }

    #[test]
    fn numeric_destinations_need_an_explicit_public_address_rule() {
        let unrestricted = Policy::new(PolicyInput::default()).unwrap();
        assert!(!unrestricted.snapshot().unwrap().permits("1.1.1.1"));
        let explicit = Policy::new(PolicyInput {
            destinations: crate::egress::policy::allowlist_destinations(vec!["1.1.1.1".into()]),
            secrets: vec![],
        })
        .unwrap();
        assert!(explicit.snapshot().unwrap().permits("1.1.1.1"));
        assert!(!explicit.snapshot().unwrap().permits("1.0.0.1"));
        assert!(
            Policy::new(PolicyInput {
                destinations: crate::egress::policy::allowlist_destinations(vec![
                    "127.0.0.1".into()
                ]),
                secrets: vec![]
            })
            .is_err()
        );
    }

    #[test]
    fn rotation_preserves_marker_and_inflight_response_scrubbing() {
        let policy = Policy::new(PolicyInput {
            secrets: vec![secret("old-token")],
            ..Default::default()
        })
        .unwrap();
        let old = policy.snapshot().unwrap();
        let marker = old.environment()["GH_TOKEN"].clone();
        assert_eq!(marker.len(), "a13n_".len() + 8 + "_GH_TOKEN".len());
        let mut change = update(1);
        change.set_secrets = vec![secret("new-token")];
        let new = policy.update(change).unwrap();
        assert_eq!(new.environment()["GH_TOKEN"], marker);
        let mut headers = HeaderMap::new();
        headers.insert(
            "authorization",
            HeaderValue::from_str(&format!("Bearer {marker}")).unwrap(),
        );
        assert_eq!(
            new.inject("api.github.com", &headers).unwrap()["authorization"],
            "Bearer new-token"
        );
        assert_eq!(
            old.scrubber().push(b"echo old-token", true),
            format!("echo {marker}").as_bytes()
        );
        assert_eq!(
            new.inject("github.com", &headers).unwrap_err(),
            PolicyError::Denied
        );
    }

    #[test]
    fn rejected_update_is_atomic_and_stale_revision_conflicts() {
        let policy = Policy::new(PolicyInput {
            secrets: vec![secret("real-token")],
            ..Default::default()
        })
        .unwrap();
        let mut invalid = update(1);
        invalid.destinations = Some(allowlist_destinations(vec![]));
        assert_eq!(policy.update(invalid).unwrap_err(), PolicyError::Invalid);
        assert_eq!(policy.snapshot().unwrap().revision, 1);
        policy.update(update(1)).unwrap();
        assert_eq!(policy.update(update(1)).unwrap_err(), PolicyError::Conflict);
    }

    #[test]
    fn deleted_markers_are_rejected_and_never_reused() {
        let policy = Policy::new(PolicyInput {
            secrets: vec![secret("real-token")],
            ..Default::default()
        })
        .unwrap();
        let marker = policy.snapshot().unwrap().environment()["GH_TOKEN"].clone();
        let mut change = update(1);
        change.remove_secrets = vec!["GH_TOKEN".into()];
        let deleted = policy.update(change).unwrap();
        let mut headers = HeaderMap::new();
        headers.insert("authorization", HeaderValue::from_str(&marker).unwrap());
        assert_eq!(
            deleted.inject("api.github.com", &headers).unwrap_err(),
            PolicyError::Denied
        );
        let mut change = update(2);
        change.set_secrets = vec![secret("other-token")];
        let fresh = policy.update(change).unwrap();
        assert_ne!(fresh.environment()["GH_TOKEN"], marker);
        assert_eq!(
            fresh.inject("api.github.com", &headers).unwrap_err(),
            PolicyError::Denied
        );
        policy.close();
        assert_eq!(policy.snapshot().unwrap_err(), PolicyError::Closed);
    }

    #[test]
    fn malformed_hosts_and_header_injection_are_rejected() {
        for bad in [
            "*.example.com",
            "user@example.com",
            "127.0.0.1",
            "example.com:443",
            "example.com/path",
            "a..b",
        ] {
            assert!(hostname(bad).is_err(), "{bad}");
        }
        assert_eq!(hostname("API.GitHub.COM.").unwrap(), "api.github.com");
        for bad in ["", "token\r\nx: injected", "token\0"] {
            assert!(
                Policy::new(PolicyInput {
                    secrets: vec![secret(bad)],
                    ..Default::default()
                })
                .is_err()
            );
        }
        assert!(!format!("{:?}", secret("very-sensitive")).contains("very-sensitive"));
    }
}
