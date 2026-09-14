package a13n

import (
	"context"
	"errors"
)

// WorkspaceClient binds search operations to the authenticated immutable Workspace ID.
// It shares the parent's transport and is closed when the parent Client is closed.
type WorkspaceClient struct {
	client *Client
	scope  WebProviderScope
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
	return &WorkspaceClient{client: client, scope: WebProviderScope{Kind: "workspace", ID: result.Value.WorkspaceID}}, nil
}
func (workspace *WorkspaceClient) WebProviders(ctx context.Context, options WebProviderListOptions) (Representation[Page[WebProvider]], error) {
	return workspace.client.WebProviders(ctx, workspace.scope, options)
}
func (workspace *WorkspaceClient) WebProvider(ctx context.Context, providerID string) (Representation[WebProvider], error) {
	return workspace.client.WebProvider(ctx, workspace.scope, providerID)
}
func (workspace *WorkspaceClient) CreateWebProvider(ctx context.Context, value CreateWebProviderRequest) (Representation[WebProvider], error) {
	return workspace.client.CreateWebProvider(ctx, workspace.scope, value)
}
func (workspace *WorkspaceClient) UpdateWebProvider(ctx context.Context, providerID, etag string, value UpdateWebProviderRequest) (Representation[WebProvider], error) {
	return workspace.client.UpdateWebProvider(ctx, workspace.scope, providerID, etag, value)
}
func (workspace *WorkspaceClient) TestWebProvider(ctx context.Context, providerID string) (Representation[WebProviderTestResult], error) {
	return workspace.client.TestWebProvider(ctx, workspace.scope, providerID)
}
func (workspace *WorkspaceClient) WebProviderReferences(ctx context.Context, providerID string, options WebProviderListOptions) (Representation[Page[WebProviderReference]], error) {
	return workspace.client.WebProviderReferences(ctx, workspace.scope, providerID, options)
}
