# a13n

Rust SDK crate for a13n Service.

## Status

This SDK implements Search Provider management for Native `/api/v1`: the type catalog, Workspace/Organization account create/list/get/update, saved-account tests, and authorized references. Responses preserve ETags, and mutations are never automatically replayed after an uncertain outcome.

The generated low-level API covers every ordinary Native `/api/v1` HTTP operation in the shared Service OpenAPI contract. The existing Search facade stays compatible; its `AgentConfig` and `AgentRunOverride` remain search-focused wrappers, while complete request/resource models live in `generated`. Generated HTTP bindings do not implement Run SSE or notification WebSocket recovery.

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

## Generated HTTP operations

```rust
use a13n::generated::apis::identity_api::get_auth_context;

// Inside an async function, with an existing a13n::Client:
let response = client.execute(async |api| get_auth_context(api).await).await?;
let workspace_id = response.data.workspace_id;
let request_id = response.headers.get("X-Request-ID");
```

`generated::models` contains complete typed HTTP schemas. Optional nullable fields use `Option<Option<T>>`: `None` omits, `Some(None)` sends null. Typed enums preserve real unions; boolean constants remain JSON booleans. Generated errors retain status, headers, raw content, and a typed error entity. Handle `CallError::Operation` separately from owner shutdown. JSON operations expose generated `Response<T>` rather than the Search facade's error mapping or 1 MiB response bound.

`execute` borrows a configuration over the parent's reqwest pool and cancellation token. Binary downloads return a streaming reqwest response; consume it inside the async closure so `close()` covers delivery. File uploads stream from a caller-supplied path. Generated standalone configurations have independent ownership; the facade never silently allocates one as a parallel transport.

## Development

Run the Rust workspace checks from the repository root:

```bash
make sdk-rust-check
```

## License

Licensed under the Apache License 2.0.
