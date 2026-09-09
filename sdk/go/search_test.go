package a13n_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"

	a13n "github.com/converge-ai-labs/agent-foundation/sdk/go"
	. "github.com/smartystreets/goconvey/convey"
)

type roundTrip func(*http.Request) (*http.Response, error)

func (f roundTrip) RoundTrip(req *http.Request) (*http.Response, error) { return f(req) }

const providerJSON = `{"id":"sp_test","organization_id":"org_test","workspace_id":"ws_test","type":"brave","name":"Research","configuration":{},"enabled":true,"credential_configured":true,"created_at":"2026-09-09T00:00:00Z","updated_at":"2026-09-09T00:00:00Z","created_by":{"principal_type":"user","principal_id":"user_test"},"updated_by":{"principal_type":"user","principal_id":"user_test"},"credential":"unexpected-secret"}`

func response(status int, body string) *http.Response {
	return &http.Response{StatusCode: status, Header: http.Header{"Etag": {`"v1"`}, "X-Request-Id": {"req_test"}}, Body: io.NopCloser(strings.NewReader(body))}
}

var scope = a13n.SearchScope{Kind: "workspace", ID: "ws_test"}

func TestSearchConfiguration(t *testing.T) {
	Convey("Search overrides preserve omission, null, replacement, and unrelated fields", t, func() {
		for _, raw := range []string{`{}`, `{"search":null}`, `{"model":{"model_key":"research"},"search":{"provider_id":"sp_test"}}`} {
			var value a13n.AgentRunOverride
			So(json.Unmarshal([]byte(raw), &value), ShouldBeNil)
			encoded, err := json.Marshal(value)
			So(err, ShouldBeNil)
			So(string(encoded), ShouldEqual, raw)
		}
		value := a13n.AgentRunOverride{Search: a13n.Null[a13n.SearchSelection]()}
		raw, err := json.Marshal(value)
		So(err, ShouldBeNil)
		So(string(raw), ShouldEqual, `{"search":null}`)
		t.Log("Search omission/null/object serialization verified")
	})
}
func TestSearchAccounts(t *testing.T) {
	Convey("Scoped account writes keep credentials wire-only and preserve ETags", t, func() {
		var paths []string
		var bodies []map[string]any
		transport := roundTrip(func(req *http.Request) (*http.Response, error) {
			paths = append(paths, req.URL.Path)
			So(req.Header.Get("Authorization"), ShouldEqual, "Bearer service-token")
			if req.Method != "GET" {
				raw, err := io.ReadAll(req.Body)
				So(err, ShouldBeNil)
				var body map[string]any
				So(json.Unmarshal(raw, &body), ShouldBeNil)
				bodies = append(bodies, body)
			}
			if req.Method == "PATCH" {
				So(req.Header.Get("If-Match"), ShouldEqual, `"v1"`)
			}
			return response(200, providerJSON), nil
		})
		client, err := a13n.NewClient("https://service.example/prefix", a13n.NewSecret("service-token"), transport)
		So(err, ShouldBeNil)
		defer client.Close()
		request := a13n.CreateSearchProviderRequest{Type: "brave", Name: "Research", Credential: a13n.NewSecret("test-secret")}
		raw, err := json.Marshal(request)
		So(err, ShouldBeNil)
		So(string(raw)+fmt.Sprintf("%+v %#v", request, request), ShouldNotContainSubstring, "test-secret")
		result, err := client.CreateSearchProvider(context.Background(), scope, request)
		So(err, ShouldBeNil)
		So(result.ETag, ShouldEqual, `"v1"`)
		So(result.RequestID, ShouldEqual, "req_test")
		So(result.Value.ID, ShouldEqual, "sp_test")
		So(bodies[0]["credential"], ShouldEqual, "test-secret")
		raw, err = json.Marshal(result)
		So(err, ShouldBeNil)
		So(string(raw), ShouldNotContainSubstring, "unexpected-secret")
		enabled := false
		_, err = client.UpdateSearchProvider(context.Background(), scope, "sp_test", result.ETag, a13n.UpdateSearchProviderRequest{Enabled: &enabled})
		So(err, ShouldBeNil)
		So(bodies[1], ShouldResemble, map[string]any{"enabled": false})
		_, err = client.SearchProvider(context.Background(), a13n.SearchScope{Kind: "organization", ID: "org_test"}, "sp_test")
		So(err, ShouldBeNil)
		So(paths[2], ShouldEqual, "/prefix/api/v1/organizations/org_test/search-providers/sp_test")
		t.Log("Create, update, organization routing, ETag, and redaction verified")
	})
}
func TestSearchCollections(t *testing.T) {
	Convey("Catalog, account pages, references, and probes retain Native meaning", t, func() {
		calls := 0
		transport := roundTrip(func(req *http.Request) (*http.Response, error) {
			calls++
			switch {
			case strings.HasSuffix(req.URL.Path, "/test"):
				raw, err := io.ReadAll(req.Body)
				So(err, ShouldBeNil)
				So(string(raw), ShouldEqual, "{}")
				return response(200, `{"success":true,"code":null,"checked_at":"2026-09-09T00:00:00Z"}`), nil
			case strings.HasSuffix(req.URL.Path, "/references"):
				return response(200, `{"items":[{"agent_id":"agent_test","agent_revision_id":"rev_test","version":1,"is_current":true}]}`), nil
			case strings.Contains(req.URL.Path, "search-provider-types"):
				definition := `{"type":"brave","display_name":"Brave","credential_required":true,"configuration_schema":{},"credential_schema":{"writeOnly":true},"setup_url":"https://example.com"}`
				if strings.HasSuffix(req.URL.Path, "/brave") {
					return response(200, definition), nil
				}
				return response(200, `{"items":[`+definition+`]}`), nil
			default:
				So(req.URL.Query().Get("cursor"), ShouldEqual, "next")
				return response(200, `{"items":[`+providerJSON+`],"next_cursor":"later"}`), nil
			}
		})
		client, err := a13n.NewClient("https://service.example", a13n.NewSecret("token"), transport)
		So(err, ShouldBeNil)
		defer client.Close()
		ctx := context.Background()
		types, err := client.SearchProviderTypes(ctx)
		So(err, ShouldBeNil)
		So(types.Value.Items[0].Type, ShouldEqual, "brave")
		kind, err := client.SearchProviderType(ctx, "brave")
		So(err, ShouldBeNil)
		So(kind.Value.Type, ShouldEqual, "brave")
		page, err := client.SearchProviders(ctx, scope, a13n.SearchListOptions{Cursor: "next"})
		So(err, ShouldBeNil)
		So(*page.Value.NextCursor, ShouldEqual, "later")
		refs, err := client.SearchProviderReferences(ctx, scope, "sp_test", a13n.SearchListOptions{})
		So(err, ShouldBeNil)
		So(refs.Value.Items[0].IsCurrent, ShouldBeTrue)
		probe, err := client.TestSearchProvider(ctx, scope, "sp_test")
		So(err, ShouldBeNil)
		So(probe.Value.Success, ShouldBeTrue)
		So(calls, ShouldEqual, 5)
	})
}
func TestUncertainSearchMutations(t *testing.T) {
	Convey("Create, rotation, and probes are never replayed after a transport failure", t, func() {
		calls := 0
		client, err := a13n.NewClient("https://service.example", a13n.NewSecret("token"), roundTrip(func(req *http.Request) (*http.Response, error) {
			calls++
			return nil, errors.New("sensitive transport detail")
		}))
		So(err, ShouldBeNil)
		defer client.Close()
		ctx := context.Background()
		secret := a13n.NewSecret("test-secret")
		_, err = client.CreateSearchProvider(ctx, scope, a13n.CreateSearchProviderRequest{Type: "exa", Name: "Research", Credential: secret})
		So(err, ShouldEqual, a13n.ErrTransport)
		_, err = client.UpdateSearchProvider(ctx, scope, "sp_test", `"v1"`, a13n.UpdateSearchProviderRequest{Credential: &secret})
		So(err, ShouldEqual, a13n.ErrTransport)
		_, err = client.TestSearchProvider(ctx, scope, "sp_test")
		So(err, ShouldEqual, a13n.ErrTransport)
		So(calls, ShouldEqual, 3)
		So(err.Error(), ShouldNotContainSubstring, "sensitive")
	})
}
func TestSearchErrorsAndClose(t *testing.T) {
	Convey("Errors are bounded and client shutdown cancels in-flight delivery", t, func() {
		client, err := a13n.NewClient("https://service.example", a13n.NewSecret("token"), roundTrip(func(req *http.Request) (*http.Response, error) {
			return response(412, `{"error":{"code":"precondition_failed","message":"Changed","request_id":"req_test"}}`), nil
		}))
		So(err, ShouldBeNil)
		_, err = client.SearchProvider(context.Background(), scope, "sp_test")
		var api *a13n.ApiError
		So(errors.As(err, &api), ShouldBeTrue)
		So(api.Status, ShouldEqual, 412)
		So(api.RequestID, ShouldEqual, "req_test")
		client.Close()
		_, err = client.SearchProvider(context.Background(), scope, "sp_test")
		So(err, ShouldEqual, a13n.ErrClosed)
		entered := make(chan struct{})
		finished := make(chan error, 1)
		client, err = a13n.NewClient("https://service.example", a13n.NewSecret("token"), roundTrip(func(req *http.Request) (*http.Response, error) {
			close(entered)
			<-req.Context().Done()
			return nil, req.Context().Err()
		}))
		So(err, ShouldBeNil)
		go func() { _, err := client.TestSearchProvider(context.Background(), scope, "sp_test"); finished <- err }()
		<-entered
		client.Close()
		So(<-finished, ShouldEqual, context.Canceled)
		t.Log("Service errors and local cancellation verified")
	})
}

func TestWorkspaceBinding(t *testing.T) {
	Convey("Workspace binding resolves once, uses immutable ID, and shares shutdown", t, func() {
		var paths []string
		client, err := a13n.NewClient("https://service.example/prefix", a13n.NewSecret("token"), roundTrip(func(req *http.Request) (*http.Response, error) {
			paths = append(paths, req.URL.Path)
			if strings.HasSuffix(req.URL.Path, "/auth/context") {
				return response(200, `{"workspace_id":"ws_test","workspace_key":"renamed"}`), nil
			}
			return response(200, `{"items":[`+providerJSON+`]}`), nil
		}))
		So(err, ShouldBeNil)
		defer client.Close()
		workspace, err := client.Workspace(context.Background())
		So(err, ShouldBeNil)
		for range 2 {
			result, err := workspace.SearchProviders(context.Background(), a13n.SearchListOptions{})
			So(err, ShouldBeNil)
			So(result.Value.Items[0].ID, ShouldEqual, "sp_test")
		}
		So(paths, ShouldResemble, []string{"/prefix/api/v1/auth/context", "/prefix/api/v1/workspaces/ws_test/search-providers", "/prefix/api/v1/workspaces/ws_test/search-providers"})
		client.Close()
		_, err = workspace.SearchProviders(context.Background(), a13n.SearchListOptions{})
		So(err, ShouldEqual, a13n.ErrClosed)
		t.Log("Workspace context is resolved once; search uses immutable ID and shares shutdown")
	})
	Convey("Organization-bound credentials cannot create a Workspace binding", t, func() {
		for _, body := range []string{`{}`, `{"workspace_id":null}`, `{"workspace_id":""}`} {
			client, err := a13n.NewClient("https://service.example", a13n.NewSecret("token"), roundTrip(func(*http.Request) (*http.Response, error) { return response(200, body), nil }))
			So(err, ShouldBeNil)
			_, err = client.Workspace(context.Background())
			So(err, ShouldNotBeNil)
			client.Close()
		}
	})
}
