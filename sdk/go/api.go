package a13n

import (
	"context"
	"io"
	"net/http"
	"strings"

	"github.com/converge-ai-labs/agent-foundation/sdk/go/generated"
)

// API exposes the generated Native operations on this client's HTTP pool and
// lifetime. Use raw operations (without WithResponse) to stream binary bodies;
// always close the returned response body. JSON helpers retain HTTP headers.
func (client *Client) API() (*generated.ClientWithResponses, error) {
	return generated.NewClientWithResponses(strings.TrimSuffix(client.baseURL, "/api/v1"), generated.WithHTTPClient(apiTransport{client}))
}

type apiTransport struct{ client *Client }

func (transport apiTransport) Do(req *http.Request) (*http.Response, error) {
	client := transport.client
	if client.lifetime.Err() != nil {
		return nil, ErrClosed
	}
	ctx, cancel := context.WithCancel(req.Context())
	stop := context.AfterFunc(client.lifetime, cancel)
	cleanup := func() { stop(); cancel() }
	req = req.Clone(ctx)
	client.tokenMu.RLock()
	req.Header.Set("Authorization", "Bearer "+client.token.value)
	client.tokenMu.RUnlock()
	response, err := client.http.Do(req)
	if err != nil {
		cleanup()
		return nil, err
	}
	response.Body = &apiBody{ReadCloser: response.Body, cleanup: cleanup}
	return response, nil
}

type apiBody struct {
	io.ReadCloser
	cleanup func()
}

func (body *apiBody) Close() error {
	defer body.cleanup()
	return body.ReadCloser.Close()
}
