package a13n

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
)

// Secret retains write-only input. Its ordinary JSON and diagnostic forms are redacted.
type Secret struct{ value string }

func NewSecret(value string) Secret         { return Secret{value: value} }
func (Secret) String() string               { return "[REDACTED]" }
func (Secret) GoString() string             { return "a13n.Secret([REDACTED])" }
func (Secret) MarshalJSON() ([]byte, error) { return json.Marshal("[REDACTED]") }

// Optional preserves omission, explicit null, and replacement. Its zero value means omission.
type Optional[T any] struct {
	Value *T
	Set   bool
}

func Some[T any](value T) Optional[T]                  { return Optional[T]{Value: &value, Set: true} }
func Null[T any]() Optional[T]                         { return Optional[T]{Set: true} }
func (value Optional[T]) IsZero() bool                 { return !value.Set }
func (value Optional[T]) MarshalJSON() ([]byte, error) { return json.Marshal(value.Value) }
func (value *Optional[T]) UnmarshalJSON(raw []byte) error {
	value.Set = true
	return json.Unmarshal(raw, &value.Value)
}

type SearchToolConfiguration struct {
	ProviderID   *string  `json:"provider_id,omitempty"`
	MaxResults   int      `json:"max_results,omitempty"`
	AllowDomains []string `json:"allow_domains,omitempty"`
	DenyDomains  []string `json:"deny_domains,omitempty"`
}

type ScrapeToolConfiguration struct {
	ProviderID      *string  `json:"provider_id,omitempty"`
	MaxContentBytes int      `json:"max_content_bytes,omitempty"`
	AllowDomains    []string `json:"allow_domains,omitempty"`
	DenyDomains     []string `json:"deny_domains,omitempty"`
}

type FetchToolConfiguration struct {
	MaxContentBytes int      `json:"max_content_bytes,omitempty"`
	AllowDomains    []string `json:"allow_domains,omitempty"`
	DenyDomains     []string `json:"deny_domains,omitempty"`
}

type DownloadToolConfiguration struct {
	AllowDomains []string `json:"allow_domains,omitempty"`
	DenyDomains  []string `json:"deny_domains,omitempty"`
}

type ToolPermission string

const (
	ToolPermissionAuto   ToolPermission = "auto"
	ToolPermissionAllow  ToolPermission = "allow"
	ToolPermissionAsk    ToolPermission = "ask"
	ToolPermissionDeny   ToolPermission = "deny"
	ToolPermissionReview ToolPermission = "review"
)

type ToolSelection struct {
	Enabled    *bool                      `json:"enabled,omitempty"`
	Permission *ToolPermission            `json:"permission,omitempty"`
	Config     map[string]json.RawMessage `json:"config,omitempty"`
}

type ToolsetSelection struct {
	Enabled *bool                      `json:"enabled,omitempty"`
	Config  map[string]json.RawMessage `json:"config,omitempty"`
	Tools   map[string]ToolSelection   `json:"tools,omitempty"`
}

// AgentConfig types built-in Toolsets and retains other Service-owned fields without interpreting them.
type AgentConfig struct {
	Toolsets Optional[map[string]ToolsetSelection]
	Fields   map[string]json.RawMessage
}

func (value AgentConfig) MarshalJSON() ([]byte, error) {
	fields := make(map[string]json.RawMessage, len(value.Fields)+1)
	for key, raw := range value.Fields {
		if key != "toolsets" {
			fields[key] = raw
		}
	}
	if value.Toolsets.Set {
		raw, err := json.Marshal(value.Toolsets.Value)
		if err != nil {
			return nil, err
		}
		fields["toolsets"] = raw
	}
	return json.Marshal(fields)
}
func (value *AgentConfig) UnmarshalJSON(raw []byte) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return err
	}
	value.Toolsets = Optional[map[string]ToolsetSelection]{}
	if toolsets, ok := fields["toolsets"]; ok {
		if err := json.Unmarshal(toolsets, &value.Toolsets); err != nil {
			return err
		}
		delete(fields, "toolsets")
	}
	value.Fields = fields
	return nil
}

type AgentRunOverride = AgentConfig

type PrincipalRef struct {
	PrincipalType string `json:"principal_type"`
	PrincipalID   string `json:"principal_id"`
}
type WebProvider struct {
	ID                   string                     `json:"id"`
	OrganizationID       string                     `json:"organization_id"`
	WorkspaceID          *string                    `json:"workspace_id"`
	Type                 string                     `json:"type"`
	Name                 string                     `json:"name"`
	Configuration        map[string]json.RawMessage `json:"configuration"`
	Enabled              bool                       `json:"enabled"`
	CredentialConfigured bool                       `json:"credential_configured"`
	CreatedAt            string                     `json:"created_at"`
	UpdatedAt            string                     `json:"updated_at"`
	CreatedBy            PrincipalRef               `json:"created_by"`
	UpdatedBy            PrincipalRef               `json:"updated_by"`
}
type WebProviderDefinition struct {
	Type                     string                     `json:"type"`
	DisplayName              string                     `json:"display_name"`
	ConfigurationSchema      map[string]json.RawMessage `json:"configuration_schema"`
	CredentialSchema         map[string]json.RawMessage `json:"credential_schema"`
	CredentialRequired       bool                       `json:"credential_required"`
	SetupURL                 string                     `json:"setup_url"`
	Operations               []string                   `json:"operations"`
	SupportsRestrictedScrape bool                       `json:"supports_restricted_scrape"`
}
type WebProviderReference struct {
	AgentID         string `json:"agent_id"`
	AgentRevisionID string `json:"agent_revision_id"`
	Version         int    `json:"version"`
	IsCurrent       bool   `json:"is_current"`
}
type WebProviderTestResult struct {
	Success   bool    `json:"success"`
	Code      *string `json:"code"`
	CheckedAt string  `json:"checked_at"`
}
type Page[T any] struct {
	Items      []T     `json:"items"`
	NextCursor *string `json:"next_cursor"`
}
type Representation[T any] struct {
	Value     T
	ETag      string
	RequestID string
}
type CreateWebProviderRequest struct {
	Type          string                     `json:"type"`
	Name          string                     `json:"name"`
	Credential    Secret                     `json:"-"`
	Configuration map[string]json.RawMessage `json:"configuration,omitempty"`
	Enabled       *bool                      `json:"enabled,omitempty"`
}
type UpdateWebProviderRequest struct {
	Name          *string                    `json:"name,omitempty"`
	Credential    *Secret                    `json:"-"`
	Configuration map[string]json.RawMessage `json:"configuration,omitempty"`
	Enabled       *bool                      `json:"enabled,omitempty"`
}
type WebProviderScope struct {
	Kind string
	ID   string
}

func (scope WebProviderScope) path() (string, error) {
	if scope.Kind != "workspace" && scope.Kind != "organization" {
		return "", errors.New("invalid Web Provider scope")
	}
	id, err := segment(scope.ID)
	if err != nil {
		return "", err
	}
	return "/" + scope.Kind + "s/" + id + "/web-providers", nil
}
func segment(value string) (string, error) {
	if value == "" || value == "." || value == ".." {
		return "", errors.New("a nonblank resource identifier is required")
	}
	return url.PathEscape(value), nil
}
func webProviderPath(scope WebProviderScope, providerID string, suffix string) (string, error) {
	base, err := scope.path()
	if err != nil {
		return "", err
	}
	id, err := segment(providerID)
	if err != nil {
		return "", err
	}
	return base + "/" + id + suffix, nil
}

// WebProviderListOptions are exact filters and the server-owned cursor.
type WebProviderListOptions struct {
	Cursor  string
	Limit   int
	Type    string
	Enabled *bool
}

func (options WebProviderListOptions) query() url.Values {
	query := url.Values{}
	if options.Cursor != "" {
		query.Set("cursor", options.Cursor)
	}
	if options.Limit != 0 {
		query.Set("limit", fmt.Sprint(options.Limit))
	}
	if options.Type != "" {
		query.Set("type", options.Type)
	}
	if options.Enabled != nil {
		query.Set("enabled", fmt.Sprint(*options.Enabled))
	}
	return query
}
