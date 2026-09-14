use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::{Map, Value};
use std::collections::BTreeMap;
use std::fmt;

/// Write-only input. Debug, Display, and ordinary serialization are redacted.
#[derive(Clone)]
pub struct Secret(pub(crate) String);
impl Secret {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }
}
impl fmt::Debug for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secret([REDACTED])")
    }
}
impl fmt::Display for Secret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("[REDACTED]")
    }
}
impl Serialize for Secret {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str("[REDACTED]")
    }
}

/// Three-state request field; its default omits the field on its owning request.
#[derive(Clone, Debug, Default, PartialEq)]
pub enum Optional<T> {
    #[default]
    Omitted,
    Null,
    Value(T),
}
impl<T> Optional<T> {
    pub fn is_omitted(&self) -> bool {
        matches!(self, Self::Omitted)
    }
}
impl<T: Serialize> Serialize for Optional<T> {
    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        match self {
            Self::Value(value) => value.serialize(s),
            _ => s.serialize_none(),
        }
    }
}
impl<'de, T: Deserialize<'de>> Deserialize<'de> for Optional<T> {
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        Ok(match Option::<T>::deserialize(d)? {
            Some(value) => Self::Value(value),
            None => Self::Null,
        })
    }
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct SearchToolConfiguration {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_results: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allow_domains: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deny_domains: Option<Vec<String>>,
}
impl SearchToolConfiguration {
    pub fn new(provider_id: impl Into<String>) -> Self {
        Self {
            provider_id: Some(provider_id.into()),
            max_results: None,
            allow_domains: None,
            deny_domains: None,
        }
    }
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct ScrapeToolConfiguration {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_content_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allow_domains: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deny_domains: Option<Vec<String>>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct FetchToolConfiguration {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_content_bytes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allow_domains: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deny_domains: Option<Vec<String>>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct DownloadToolConfiguration {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allow_domains: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deny_domains: Option<Vec<String>>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub enum ToolPermission {
    #[serde(rename = "inherit")]
    #[default]
    Inherit,
    #[serde(rename = "allow")]
    Allow,
    #[serde(rename = "ask")]
    Ask,
    #[serde(rename = "deny")]
    Deny,
    #[serde(rename = "review")]
    Review,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct ToolSelection {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub permission: Option<ToolPermission>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub config: Option<Map<String, Value>>,
}
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct ToolsetSelection {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub config: Option<Map<String, Value>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tools: Option<BTreeMap<String, ToolSelection>>,
}
/// Types built-in Toolsets and preserves other Service-owned configuration fields verbatim.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct AgentConfig {
    #[serde(default, skip_serializing_if = "Optional::is_omitted")]
    pub toolsets: Optional<BTreeMap<String, ToolsetSelection>>,
    #[serde(flatten)]
    pub fields: Map<String, Value>,
}
pub type AgentRunOverride = AgentConfig;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PrincipalRef {
    pub principal_type: String,
    pub principal_id: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WebProvider {
    pub id: String,
    pub organization_id: String,
    pub workspace_id: Option<String>,
    pub name: String,
    #[serde(rename = "type")]
    pub provider_type: String,
    pub configuration: Map<String, Value>,
    pub enabled: bool,
    pub credential_configured: bool,
    pub created_at: String,
    pub updated_at: String,
    pub created_by: PrincipalRef,
    pub updated_by: PrincipalRef,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WebProviderDefinition {
    #[serde(rename = "type")]
    pub provider_type: String,
    pub display_name: String,
    pub configuration_schema: Map<String, Value>,
    pub credential_schema: Map<String, Value>,
    pub credential_required: bool,
    pub setup_url: String,
    pub operations: Vec<String>,
    pub supports_restricted_scrape: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WebProviderReference {
    pub agent_id: String,
    pub agent_revision_id: String,
    pub version: u64,
    pub is_current: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WebProviderTestResult {
    pub success: bool,
    pub code: Option<String>,
    pub checked_at: String,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Page<T> {
    pub items: Vec<T>,
    pub next_cursor: Option<String>,
}
#[derive(Clone, Debug)]
pub struct Representation<T> {
    pub value: T,
    pub etag: Option<String>,
    pub request_id: Option<String>,
}
#[derive(Clone, Debug, Serialize)]
pub struct CreateWebProviderRequest {
    #[serde(rename = "type")]
    pub provider_type: String,
    pub name: String,
    #[serde(skip)]
    pub credential: Secret,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub configuration: Option<Map<String, Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
}
impl CreateWebProviderRequest {
    pub fn new(
        provider_type: impl Into<String>,
        name: impl Into<String>,
        credential: Secret,
    ) -> Self {
        Self {
            provider_type: provider_type.into(),
            name: name.into(),
            credential,
            configuration: None,
            enabled: None,
        }
    }
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct UpdateWebProviderRequest {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip)]
    pub credential: Option<Secret>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub configuration: Option<Map<String, Value>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
}
#[derive(Clone, Debug)]
pub enum WebProviderScope {
    Workspace(String),
    Organization(String),
}
impl WebProviderScope {
    pub(crate) fn segments(&self) -> [&str; 3] {
        match self {
            Self::Workspace(id) => ["workspaces", id, "web-providers"],
            Self::Organization(id) => ["organizations", id, "web-providers"],
        }
    }
}
#[derive(Clone, Debug, Default, Serialize)]
pub struct WebProviderListOptions {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cursor: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub limit: Option<u16>,
    #[serde(rename = "type", skip_serializing_if = "Option::is_none")]
    pub provider_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub enabled: Option<bool>,
}
