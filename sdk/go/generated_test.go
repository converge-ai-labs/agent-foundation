package a13n_test

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	a13n "github.com/converge-ai-labs/agent-foundation/sdk/go"
	"github.com/converge-ai-labs/agent-foundation/sdk/go/generated"
)

func wireFixtures(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	data, err := os.ReadFile("../fixtures/wire.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixtures map[string]json.RawMessage
	if err := json.Unmarshal(data, &fixtures); err != nil {
		t.Fatal(err)
	}
	return fixtures
}

func roundtripFixtures[T any](t *testing.T, fixtures map[string]json.RawMessage, key string) {
	t.Helper()
	var cases []json.RawMessage
	if err := json.Unmarshal(fixtures[key], &cases); err != nil {
		t.Fatal(err)
	}
	for _, data := range cases {
		var model T
		if err := json.Unmarshal(data, &model); err != nil {
			t.Fatal(err)
		}
		encoded, err := json.Marshal(model)
		if err != nil {
			t.Fatal(err)
		}
		var want, got any
		if err := json.Unmarshal(data, &want); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal(encoded, &got); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("%s: got %s, want %s", key, encoded, data)
		}
	}
}

func TestGeneratedWireFixtures(t *testing.T) {
	fixtures := wireFixtures(t)
	roundtripFixtures[generated.UpdateAgentRequest](t, fixtures, "patch")
	roundtripFixtures[generated.ActorRef](t, fixtures, "actor")
	roundtripFixtures[generated.EnvironmentSelection](t, fixtures, "environment")
	roundtripFixtures[generated.UserMessage](t, fixtures, "user_message")
	roundtripFixtures[generated.RunStatus](t, fixtures, "run_status")
	roundtripFixtures[generated.ConnectorCollection](t, fixtures, "null_cursor")
	var omitted, null generated.UpdateAgentRequest
	if err := json.Unmarshal([]byte(`{"name":null}`), &null); err != nil {
		t.Fatal(err)
	}
	if omitted.Name.IsSpecified() || !null.Name.IsNull() {
		t.Fatal("omission and null collapsed")
	}
	actor := generated.ActorRef{}
	if err := actor.FromPrincipalRef(generated.PrincipalRef{PrincipalId: "usr_test", PrincipalType: generated.PrincipalTypeUser}); err != nil {
		t.Fatal(err)
	}
	decoded, err := actor.AsPrincipalRef()
	if err != nil || decoded.PrincipalId != "usr_test" {
		t.Fatalf("typed union branch: %v %v", decoded, err)
	}
	credential := "do-not-print"
	secret := generated.CreateSearchProviderRequest{Credential: &credential}
	if strings.Contains(fmt.Sprintf("%+v %#v", secret, secret), "do-not-print") {
		t.Fatal("credential leaked")
	}
}

func TestGeneratedHTTPAndStreamShareTransport(t *testing.T) {
	fixtures := wireFixtures(t)
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Error("missing authentication")
		}
		w.Header().Set("X-Request-ID", "req_test")
		w.Header().Set("ETag", `"v1"`)
		switch r.URL.Path {
		case "/prefix/api/v1/auth/context":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write(fixtures["credential_context"])
		case "/prefix/api/v1/assets/ast_test/content":
			_, _ = w.Write([]byte("streamed"))
		default:
			t.Errorf("unexpected path %s", r.URL.Path)
		}
	}))
	defer server.Close()
	client, err := a13n.NewClient(server.URL+"/prefix", a13n.NewSecret("test-token"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	api, err := client.API()
	if err != nil {
		t.Fatal(err)
	}
	result, err := api.GetAuthContextWithResponse(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if result.JSON200 == nil || result.HTTPResponse.Header.Get("X-Request-ID") != "req_test" {
		t.Fatal("missing result metadata")
	}
	response, err := api.GetAssetsAssetIdContent(context.Background(), "ast_test")
	if err != nil {
		t.Fatal(err)
	}
	data, err := io.ReadAll(response.Body)
	_ = response.Body.Close()
	if err != nil || string(data) != "streamed" {
		t.Fatalf("stream failed: %v", err)
	}
	_ = client.Close()
	if _, err := api.GetAuthContext(context.Background()); err == nil {
		t.Fatal("closed client accepted request")
	}
	if calls != 2 {
		t.Fatalf("unexpected retry or request: %d", calls)
	}
}

func TestGeneratedRejectsWrongFieldTypes(t *testing.T) {
	source := filepath.Join(t.TempDir(), "invalid.go")
	code := `package invalid
import "github.com/converge-ai-labs/agent-foundation/sdk/go/generated"
var _ = generated.UpdateAgentRequest{Name: 42}
var _ = generated.UserMessage{Content: 42}
`
	if err := os.WriteFile(source, []byte(code), 0600); err != nil {
		t.Fatal(err)
	}
	command := exec.Command("go", "test", source)
	output, err := command.CombinedOutput()
	if err == nil || strings.Count(string(output), "cannot use 42") != 2 {
		t.Fatalf("expected two type errors: %v: %s", err, output)
	}
}
