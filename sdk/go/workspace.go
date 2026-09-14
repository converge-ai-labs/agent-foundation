package a13n

import (
	"context"
	"errors"
)

// WorkspaceClient binds search operations to the authenticated immutable Workspace ID.
// It shares the parent's transport and is closed when the parent Client is closed.
type WorkspaceClient struct {
	client *Client
	scope  SearchScope
}

// Workspace resolves the API key's Workspace once for this binding.
func (client *Client) Workspace(ctx context.Context) (*WorkspaceClient, error) {
	result, err := request[struct {
		WorkspaceID string `json:"workspace_id"`
	}](ctx, client, "GET", "/auth/context", "", nil, nil)
	if err != nil {
		return nil, err
	}
	if result.Value.WorkspaceID == "" {
		return nil, errors.New("workspace operations require a Workspace-bound credential")
	}
	return &WorkspaceClient{client: client, scope: SearchScope{Kind: "workspace", ID: result.Value.WorkspaceID}}, nil
}
func (workspace *WorkspaceClient) SearchProviders(ctx context.Context, options SearchListOptions) (Representation[Page[SearchProvider]], error) {
	return workspace.client.SearchProviders(ctx, workspace.scope, options)
}
func (workspace *WorkspaceClient) SearchProvider(ctx context.Context, providerID string) (Representation[SearchProvider], error) {
	return workspace.client.SearchProvider(ctx, workspace.scope, providerID)
}
func (workspace *WorkspaceClient) CreateSearchProvider(ctx context.Context, value CreateSearchProviderRequest) (Representation[SearchProvider], error) {
	return workspace.client.CreateSearchProvider(ctx, workspace.scope, value)
}
func (workspace *WorkspaceClient) UpdateSearchProvider(ctx context.Context, providerID, etag string, value UpdateSearchProviderRequest) (Representation[SearchProvider], error) {
	return workspace.client.UpdateSearchProvider(ctx, workspace.scope, providerID, etag, value)
}
func (workspace *WorkspaceClient) TestSearchProvider(ctx context.Context, providerID string) (Representation[SearchProviderTestResult], error) {
	return workspace.client.TestSearchProvider(ctx, workspace.scope, providerID)
}
func (workspace *WorkspaceClient) SearchProviderReferences(ctx context.Context, providerID string, options SearchListOptions) (Representation[Page[SearchProviderReference]], error) {
	return workspace.client.SearchProviderReferences(ctx, workspace.scope, providerID, options)
}
