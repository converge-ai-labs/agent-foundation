package a13n

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

type ApiError struct {
	Status     int
	Code       string
	Message    string
	Details    map[string]json.RawMessage
	RequestID  string
	RetryAfter string
}

func (err *ApiError) Error() string { return err.Code + ": " + err.Message }

var ErrTransport = errors.New("service transport failed; mutation outcome may be unknown")
var ErrProtocol = errors.New("invalid or oversized Service response")
var ErrClosed = errors.New("client is closed")

// Client owns its HTTP transport. Close cancels local requests, never server Runs.
// The current client implements the Native Web Provider management surface.
type Client struct {
	baseURL  string
	token    Secret
	http     *http.Client
	lifetime context.Context
	cancel   context.CancelFunc
	once     sync.Once
	tokenMu  sync.RWMutex
}

// NewClient creates a bearer client. A supplied transport is owned and closed by the client.
func NewClient(baseURL string, token Secret, transport http.RoundTripper) (*Client, error) {
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed.Scheme != "http" && parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("baseURL must be an HTTP(S) URL without credentials, query, or fragment")
	}
	if transport == nil {
		transport = http.DefaultTransport.(*http.Transport).Clone()
	}
	lifetime, cancel := context.WithCancel(context.Background())
	return &Client{baseURL: strings.TrimRight(baseURL, "/") + "/api/v1", token: token, http: &http.Client{Transport: transport, Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}, lifetime: lifetime, cancel: cancel}, nil
}
func (client *Client) Close() error {
	client.once.Do(func() {
		client.cancel()
		client.tokenMu.Lock()
		client.token = Secret{}
		client.tokenMu.Unlock()
		client.http.CloseIdleConnections()
	})
	return nil
}
func request[T any](ctx context.Context, client *Client, method, path, etag string, query url.Values, body any) (Representation[T], error) {
	var result Representation[T]
	if client.lifetime.Err() != nil {
		return result, ErrClosed
	}
	local, cancel := context.WithCancel(ctx)
	stop := context.AfterFunc(client.lifetime, cancel)
	defer stop()
	defer cancel()
	var encoded []byte
	var err error
	if body != nil {
		encoded, err = json.Marshal(body)
		if err != nil {
			return result, ErrProtocol
		}
	}
	target := client.baseURL + path
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	req, err := http.NewRequestWithContext(local, method, target, bytes.NewReader(encoded))
	if err != nil {
		return result, ErrProtocol
	}
	client.tokenMu.RLock()
	req.Header.Set("Authorization", "Bearer "+client.token.value)
	client.tokenMu.RUnlock()
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if etag != "" {
		req.Header.Set("If-Match", etag)
	}
	response, err := client.http.Do(req)
	if err != nil {
		if local.Err() != nil {
			return result, local.Err()
		}
		return result, ErrTransport
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 1_048_577))
	if err != nil {
		if local.Err() != nil {
			return result, local.Err()
		}
		return result, ErrTransport
	}
	if len(raw) > 1_048_576 {
		return result, ErrProtocol
	}
	result.ETag = response.Header.Get("ETag")
	result.RequestID = response.Header.Get("X-Request-ID")
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		var envelope struct {
			Error struct {
				Code      string                     `json:"code"`
				Message   string                     `json:"message"`
				Details   map[string]json.RawMessage `json:"details"`
				RequestID string                     `json:"request_id"`
			} `json:"error"`
		}
		if json.Unmarshal(raw, &envelope) != nil {
			return result, ErrProtocol
		}
		code, message := envelope.Error.Code, envelope.Error.Message
		if code == "" {
			code = "http_error"
		}
		if message == "" {
			message = "Service request failed"
		}
		requestID := envelope.Error.RequestID
		if requestID == "" {
			requestID = result.RequestID
		}
		return result, &ApiError{Status: response.StatusCode, Code: code, Message: message, Details: envelope.Error.Details, RequestID: requestID, RetryAfter: response.Header.Get("Retry-After")}
	}
	raw = bytes.TrimSpace(raw)
	if len(raw) == 0 || raw[0] != '{' || json.Unmarshal(raw, &result.Value) != nil {
		return result, ErrProtocol
	}
	return result, nil
}
func (client *Client) WebProviderTypes(ctx context.Context) (Representation[Page[WebProviderDefinition]], error) {
	return request[Page[WebProviderDefinition]](ctx, client, "GET", "/web-provider-types", "", nil, nil)
}
func (client *Client) WebProviderType(ctx context.Context, providerType string) (Representation[WebProviderDefinition], error) {
	id, err := segment(providerType)
	if err != nil {
		return Representation[WebProviderDefinition]{}, err
	}
	return request[WebProviderDefinition](ctx, client, "GET", "/web-provider-types/"+id, "", nil, nil)
}
func (client *Client) WebProviders(ctx context.Context, scope WebProviderScope, options WebProviderListOptions) (Representation[Page[WebProvider]], error) {
	path, err := scope.path()
	if err != nil {
		return Representation[Page[WebProvider]]{}, err
	}
	return request[Page[WebProvider]](ctx, client, "GET", path, "", options.query(), nil)
}
func (client *Client) WebProvider(ctx context.Context, scope WebProviderScope, providerID string) (Representation[WebProvider], error) {
	path, err := webProviderPath(scope, providerID, "")
	if err != nil {
		return Representation[WebProvider]{}, err
	}
	return request[WebProvider](ctx, client, "GET", path, "", nil, nil)
}
func (client *Client) CreateWebProvider(ctx context.Context, scope WebProviderScope, value CreateWebProviderRequest) (Representation[WebProvider], error) {
	path, err := scope.path()
	if err != nil {
		return Representation[WebProvider]{}, err
	}
	body := map[string]any{"type": value.Type, "name": value.Name, "credential": value.Credential.value}
	if value.Configuration != nil {
		body["configuration"] = value.Configuration
	}
	if value.Enabled != nil {
		body["enabled"] = *value.Enabled
	}
	return request[WebProvider](ctx, client, "POST", path, "", nil, body)
}
func (client *Client) UpdateWebProvider(ctx context.Context, scope WebProviderScope, providerID, etag string, value UpdateWebProviderRequest) (Representation[WebProvider], error) {
	if etag == "" || strings.HasPrefix(etag, "W/") {
		return Representation[WebProvider]{}, errors.New("a strong account ETag is required")
	}
	path, err := webProviderPath(scope, providerID, "")
	if err != nil {
		return Representation[WebProvider]{}, err
	}
	body := map[string]any{}
	if value.Name != nil {
		body["name"] = *value.Name
	}
	if value.Configuration != nil {
		body["configuration"] = value.Configuration
	}
	if value.Enabled != nil {
		body["enabled"] = *value.Enabled
	}
	if value.Credential != nil {
		body["credential"] = value.Credential.value
	}
	return request[WebProvider](ctx, client, "PATCH", path, etag, nil, body)
}
func (client *Client) TestWebProvider(ctx context.Context, scope WebProviderScope, providerID string) (Representation[WebProviderTestResult], error) {
	path, err := webProviderPath(scope, providerID, "/test")
	if err != nil {
		return Representation[WebProviderTestResult]{}, err
	}
	return request[WebProviderTestResult](ctx, client, "POST", path, "", nil, struct{}{})
}
func (client *Client) WebProviderReferences(ctx context.Context, scope WebProviderScope, providerID string, options WebProviderListOptions) (Representation[Page[WebProviderReference]], error) {
	path, err := webProviderPath(scope, providerID, "/references")
	if err != nil {
		return Representation[Page[WebProviderReference]]{}, err
	}
	query := options.query()
	query.Del("type")
	query.Del("enabled")
	return request[Page[WebProviderReference]](ctx, client, "GET", path, "", query, nil)
}
