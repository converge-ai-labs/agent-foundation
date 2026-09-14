# a13n for Go

Go SDK module for a13n Service.

## Status

This SDK implements Web Provider management for Native `/api/v1`: the type catalog, Workspace/Organization account create/list/get/update, saved-account tests, and authorized references. Responses preserve ETags, and mutations are never automatically replayed after an uncertain outcome.

The generated low-level API covers every ordinary Native `/api/v1` HTTP operation in the shared Service OpenAPI contract. The Web facade provides typed `AgentConfig` and `AgentRunOverride` wrappers, while complete request/resource models live in `generated`. Generated HTTP bindings do not implement Run SSE or notification WebSocket recovery.

## Installation

```bash
go get github.com/converge-ai-labs/agent-foundation/sdk/go@latest
```

```go
import "github.com/converge-ai-labs/agent-foundation/sdk/go"
```

Go module releases use canonical module tags in the form `sdk/go/v<version>`, created by the `release/a13n/go/<version>` release workflow. `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`.

## Web Provider accounts

Bind API Key operations with `client.Workspace(ctx)`. This reads `/api/v1/auth/context` once and returns Web Provider operations without a Workspace argument. The binding uses the immutable Workspace ID and shares the parent transport and shutdown. The parent client retains explicit `WebProviderScope` operations; Service always enforces the credential boundary.

Create a bearer client with `NewClient(baseURL, NewSecret(token), nil)` and call `Close` when finished. All operations accept `context.Context`. `WebProviders` returns a `Representation[Page[WebProvider]]`; pass `WebProviderListOptions{Cursor: ...}` to continue pagination. `UpdateWebProvider` requires the current account ETag. `TestWebProvider` sends one quota-consuming probe only when called.

Credential request fields use a `map[string]Secret`, such as `map[string]a13n.Secret{"api_key": a13n.NewSecret(value)}`. Their JSON and formatting diagnostics omit or redact credential values; the client serializes the write-only object for the request. `AgentRunOverride.Toolsets` uses its zero value to inherit; a supplied `web` entry replaces that complete Toolset, and `Enabled: &false` disables it. `ToolPermissionInherit` preserves the authored `inherit` setting. Configuration JSON round-trips preserve other fields in `Fields`.

## Generated HTTP operations

Call `api, err := client.API()` once, check the error, then use `api.GetAuthContextWithResponse(ctx)` or any other generated operation. The generated view shares the parent HTTP pool, authentication, context cancellation, and `Close`; it does not create a second transport.

Import full request/resource types from `github.com/converge-ai-labs/agent-foundation/sdk/go/generated`. Nullable fields use `nullable.Nullable[T]`, preserving omitted/null/value. Union helpers expose typed `As...` and `From...` branches. `WithResponse` methods expose typed status-specific bodies and HTTP headers; callers handle those results rather than the Web facade's `ApiError` mapping. These low-level methods do not impose the Web facade's 1 MiB response bound.

For binary transfer, use generated raw methods (without `WithResponse`) with `io.Reader` request bodies and the returned `*http.Response`; always close the response body. `WithResponse` helpers buffer the response. Go 1.25 or newer is required.

## Development

Run the Go SDK checks from the repository root:

```bash
make sdk-go-check
```

## License

Licensed under the Apache License 2.0.
