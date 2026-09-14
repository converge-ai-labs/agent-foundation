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

type SearchSelection struct {
	ProviderID     string   `json:"provider_id"`
	MaxResults     int      `json:"max_results,omitempty"`
	IncludeDomains []string `json:"include_domains,omitempty"`
}

// AgentConfig types search and retains other Service-owned fields without interpreting them.
// Only the search surface currently has SDK-provided types and validation.
type AgentConfig struct {
	Search Optional[SearchSelection]
	Fields map[string]json.RawMessage
}

func (value AgentConfig) MarshalJSON() ([]byte, error) {
	fields := make(map[string]json.RawMessage, len(value.Fields)+1)
	for key, raw := range value.Fields {
		if key != "search" {
			fields[key] = raw
		}
	}
	if value.Search.Set {
		raw, err := json.Marshal(value.Search.Value)
		if err != nil {
			return nil, err
		}
		fields["search"] = raw
	}
	return json.Marshal(fields)
}
func (value *AgentConfig) UnmarshalJSON(raw []byte) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return err
	}
	value.Search = Optional[SearchSelection]{}
	if search, ok := fields["search"]; ok {
		if err := json.Unmarshal(search, &value.Search); err != nil {
			return err
		}
		delete(fields, "search")
	}
	value.Fields = fields
	return nil
}

type AgentRunOverride = AgentConfig

type PrincipalRef struct {
	PrincipalType string `json:"principal_type"`
	PrincipalID   string `json:"principal_id"`
}
type SearchProvider struct {
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
type SearchProviderDefinition struct {
	Type                string                     `json:"type"`
	DisplayName         string                     `json:"display_name"`
	ConfigurationSchema map[string]json.RawMessage `json:"configuration_schema"`
	CredentialSchema    map[string]json.RawMessage `json:"credential_schema"`
	CredentialRequired  bool                       `json:"credential_required"`
	SetupURL            string                     `json:"setup_url"`
}
type SearchProviderReference struct {
	AgentID         string `json:"agent_id"`
	AgentRevisionID string `json:"agent_revision_id"`
	Version         int    `json:"version"`
	IsCurrent       bool   `json:"is_current"`
}
type SearchProviderTestResult struct {
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
type CreateSearchProviderRequest struct {
	Type          string                     `json:"type"`
	Name          string                     `json:"name"`
	Credential    Secret                     `json:"-"`
	Configuration map[string]json.RawMessage `json:"configuration,omitempty"`
	Enabled       *bool                      `json:"enabled,omitempty"`
}
type UpdateSearchProviderRequest struct {
	Name          *string                    `json:"name,omitempty"`
	Credential    *Secret                    `json:"-"`
	Configuration map[string]json.RawMessage `json:"configuration,omitempty"`
	Enabled       *bool                      `json:"enabled,omitempty"`
}
type SearchScope struct {
	Kind string
	ID   string
}

func (scope SearchScope) path() (string, error) {
	if scope.Kind != "workspace" && scope.Kind != "organization" {
		return "", errors.New("invalid search scope")
	}
	id, err := segment(scope.ID)
	if err != nil {
		return "", err
	}
	return "/" + scope.Kind + "s/" + id + "/search-providers", nil
}
func segment(value string) (string, error) {
	if value == "" || value == "." || value == ".." {
		return "", errors.New("a nonblank resource identifier is required")
	}
	return url.PathEscape(value), nil
}
func searchPath(scope SearchScope, providerID string, suffix string) (string, error) {
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

// SearchListOptions are exact filters and the server-owned cursor.
type SearchListOptions struct {
	Cursor  string
	Limit   int
	Type    string
	Enabled *bool
}

func (options SearchListOptions) query() url.Values {
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
