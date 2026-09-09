# a13n

Rust SDK crate for a13n Service.

## Status

This SDK implements Search Provider management for Native `/api/v1`: the type catalog, Workspace/Organization account create/list/get/update, saved-account tests, and authorized references. Responses preserve ETags, and mutations are never automatically replayed after an uncertain outcome.

Other Service operations are not implemented yet. `AgentConfig` and `AgentRunOverride` type the search selection while preserving other Service-owned configuration fields; they are not complete local validators for Agent configuration. Source compatibility is with the repository's current `/api/v1` Search Provider contract.

## Installation

```toml
[dependencies]
a13n = "0.0"
```

```rust
use a13n as service_client;
```

## Search accounts

Bind API Key operations with `client.workspace().await?`. This reads `/api/v1/auth/context` once and returns search operations without a Workspace argument. The binding uses the immutable Workspace ID and shares the parent transport and shutdown. The parent client retains explicit `SearchScope` operations; Service always enforces the credential boundary.

Create an async bearer client with `Client::new(base_url, Secret::new(token))`. `search_providers` returns a `Representation<Page<SearchProvider>>`; use `SearchListOptions` for cursor pagination and exact filters. `update_search_provider` requires the current account ETag. `test_search_provider` sends one quota-consuming probe only when called. Drop a request future to cancel that request; call `close()` to cancel all local delivery and release the pool.

Credential request fields use `Secret`, whose Debug, Display, and ordinary serialization forms are redacted. `AgentRunOverride.search` uses `Optional::Omitted` to inherit, `Optional::Null` to disable, and `Optional::Value(SearchSelection::new(account_id))` to replace. Configuration JSON round-trips preserve other fields in `fields`.

## Development

Run the Rust workspace checks from the repository root:

```bash
make sdk-rust-check
```

## License

Licensed under the Apache License 2.0.
