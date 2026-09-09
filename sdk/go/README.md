# a13n for Go

Go SDK module for a13n Service.

## Status

This SDK implements Search Provider management for Native `/api/v1`: the type catalog, Workspace/Organization account create/list/get/update, saved-account tests, and authorized references. Responses preserve ETags, and mutations are never automatically replayed after an uncertain outcome.

Other Service operations are not implemented yet. `AgentConfig` and `AgentRunOverride` type the search selection while preserving other Service-owned configuration fields; they are not complete local validators for Agent configuration. Source compatibility is with the repository's current `/api/v1` Search Provider contract.

## Installation

```bash
go get github.com/converge-ai-labs/agent-foundation/sdk/go@latest
```

```go
import "github.com/converge-ai-labs/agent-foundation/sdk/go"
```

Go module releases use canonical module tags in the form `sdk/go/v<version>`, created by the `release/a13n/go/<version>` release workflow. `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`.

## Search accounts

Create a bearer client with `NewClient(baseURL, NewSecret(token), nil)` and call `Close` when finished. All operations accept `context.Context`. `SearchProviders` returns a `Representation[Page[SearchProvider]]`; pass `SearchListOptions{Cursor: ...}` to continue pagination. `UpdateSearchProvider` requires the current account ETag. `TestSearchProvider` sends one quota-consuming probe only when called.

Credential request fields use `NewSecret(value)`. Their JSON and formatting diagnostics omit or redact credentials; the client serializes the write-only field for the request. `AgentRunOverride.Search` uses `Optional[SearchSelection]{}` to inherit, `Null[SearchSelection]()` to disable, and `Some(SearchSelection{ProviderID: accountID})` to replace. Configuration JSON round-trips preserve other fields in `Fields`.

## Development

Run the Go SDK checks from the repository root:

```bash
make sdk-go-check
```

## License

Licensed under the Apache License 2.0.
