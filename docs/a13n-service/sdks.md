# Service SDKs

Service SDKs call a running Service. They do not run a Harness Agent in your process and are not interchangeable with `a13n-harness`. Language packages have independent versions and validation boundaries; **implemented API coverage differs by language**.

## Choose a client

| Client        | Distribution                                          | Implemented surface                                                              |
| ------------- | ----------------------------------------------------- | -------------------------------------------------------------------------------- |
| TypeScript    | `@converge.ai/a13n`                                   | Complete checked Native HTTP contract, Workspace binding, Run SSE, notifications |
| Python        | `a13n`                                                | Generated Native HTTP plus Web Provider facade and Workspace binding             |
| Go            | `github.com/converge-ai-labs/agent-foundation/sdk/go` | Generated Native HTTP plus Web Provider facade and Workspace binding             |
| Rust          | `a13n`                                                | Generated Native HTTP plus Web Provider facade and Workspace binding             |
| Companion CLI | `a13n-service-cli`                                    | Help/version only; no resource or network commands yet                           |

Python, Go, and Rust implement the Web Provider type catalog and scoped create/list/get/update/test/reference operations. Their `AgentConfig` and `AgentRunOverride` wrappers type the independent **Web selection** for search, scrape, fetch, and download, preserve other Service-owned fields, and are not complete Agent configuration validators. Their generated low-level bindings also cover Agent CRUD, Run submission, and binary HTTP operations. Run SSE recovery and notification WebSocket helpers are still TypeScript-only.

The TypeScript generated `paths`, `components`, and `operations` types follow the [Native OpenAPI contract](../assets/reference/service-openapi.json). The generator does not create an alternative Service implementation. AG-UI, A2A, and provider ingress are outside this Native client.

## TypeScript: read an Agent

Install the client in your application:

```bash
npm install @converge.ai/a13n
```

The following function requires a reachable Service and a Workspace application key. It reads an existing Agent without invoking a model:

```typescript
import { createClient, data } from "@converge.ai/a13n";

export async function readAgent(baseUrl: string, token: string, agent: string) {
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token },
  });
  try {
    const http = await client.workspaceHttp();
    const result = await http.GET("/agents/{agent}", {
      params: { path: { agent } },
      signal: AbortSignal.timeout(30_000),
    });
    return data(result);
  } finally {
    client.close();
  }
}
```

`workspaceHttp()` reads `/api/v1/auth/context` and binds the key's immutable Workspace ID. Recreate the binding if you change credentials to another Workspace. The explicit `client.http` surface remains available for Organization/personal operations and full paths beginning with `/api/v1`. Neither binding nor an explicit path broadens credential authority.

### Options, authentication, and responses

`createClient()` accepts `baseUrl`, `auth`, optional `fetch`, and `maxReadRetries`. The base URL can include a reverse-proxy prefix but not credentials, query, or fragment. Bearer authentication accepts a token string or a synchronous/asynchronous token callback.

For same-origin browsers, use `auth: { type: "session" }`. Restore the CSRF token from login or `GET /api/v1/auth/csrf` using `setCsrfToken()` before authenticated mutations. Tokens stay in memory. Session requests use same-origin cookies; Bearer requests omit cookies. Redirects and requests outside the configured Native API boundary are rejected.

Typed operation options carry path/query parameters, headers, and `AbortSignal`. Pass `If-Match`, `Idempotency-Key`, and Workspace selection explicitly where required. Preserve the actual response headers for ETags and request IDs. `data()` unwraps a representation; do not use it for a successful `204` with no body.

`ApiError` contains status, code, safe details, request ID, and retry guidance. `ReplayGapError` identifies stream replay loss; `ProtocolError` identifies malformed stream protocol data. Network and abort failures can remain native errors. Handle invalid local arguments separately from a Service rejection.

### Retries and binary transfer

`maxReadRetries` defaults to 2 and accepts integers 0–5. Only GET and HEAD retry automatically. Retryable status codes are 429, 502, 503, and 504; bounded `Retry-After` up to 30 seconds is honored, otherwise eligible retries use exponential backoff starting at 250 ms. Network read failures are also eligible. There is no SDK-wide timeout knob: pass `AbortSignal` or configure your injected fetch.

Mutations have zero automatic retries. After a lost acknowledgement, reconcile the original command using its idempotency contract rather than constructing a fresh intent. Omitted fields and explicit null remain different.

Binary upload bodies can be `Blob` or `ReadableStream<Uint8Array>` with an explicit content type. Use `parseAs: "stream"` for streamed downloads. The SDK does not eagerly buffer these bodies; Node streaming requests need the runtime's `duplex: "half"` option. API size and content restrictions still apply.

### Stream and notification lifetimes

`streamRun(runId, { after, workspaceId, signal })` yields `{ cursor, event }`. `after` becomes `Last-Event-ID`; `workspaceId` becomes `X-A13N-Workspace-ID`. Apply each event before requesting the next one. The iterator advances its reconnect cursor only when iteration resumes after a yield.

The helper permits two outer transport-failure reconnects in addition to HTTP read retries. Clean EOF, API errors, and protocol errors do not trigger that reconnect path. A frame is bounded to 1,048,576 JavaScript string characters, not bytes. A replay gap requires Run/Items/pending-action reconciliation, not blind reconnect.

`notifications({ subscriptions, onNotification, onState, onError, signal?, socketFactory? })` opens the notification attachment. It reports `connecting`, `connected`, `gap`, and `closed`, acknowledges heartbeats, and allows up to three reconnects. Protocol/policy close codes 1002, 1003, 1008, and 1009 stop reconnection. Close the returned handle to change subscriptions.

Browser notifications use session cookies. Application keys require a `socketFactory` that can attach authorization headers; credentials never belong in a URL or subprotocol. `gap` requires durable reconciliation. `client.close()` cancels local HTTP, SSE, and notification delivery, clears CSRF state, and does **not** cancel server Runs.

## Python: read Web Providers

```bash
uv add a13n
```

```python
from a13n import Client


async def search_accounts(base_url: str, token: str):
    async with Client(base_url, token, timeout=30) as client:
        workspace = await client.workspace()
        page = await workspace.web_providers(limit=50)
        return page.items, page.next_cursor
```

`Client(base_url, token, *, timeout=30, transport=None)` owns its supplied `httpx2.AsyncBaseTransport`. It does not follow redirects or trust environment proxy configuration. Web facade responses are bounded to 1 MiB. Lists default to 100 unless you supply a limit; follow returned cursors explicitly.

Detail/create/update return `Representation(value, etag, request_id)`. Collection/probe operations return their own values. Updates require a non-weak ETag. `ApiError` carries status/code/message/details/request ID/retry guidance; `ProtocolError` covers invalid, oversized, or schema-invalid responses; `TransportError` can leave mutation outcome unknown. No automatic SDK retries occur.

Use `aclose()` or an async context manager. Closing cancels local requests, clears authentication headers, and releases the owned transport. Workspace clients share that lifetime. [Web Providers](web.md) shows credential inputs, `WebProviderScope`, and omitted/null/value selection.

## Go and Rust

| Behavior           | Go                                                                  | Rust                                             |
| ------------------ | ------------------------------------------------------------------- | ------------------------------------------------ |
| Constructor        | `NewClient(baseURL, Secret, http.RoundTripper)`                     | `Client::new(base_url, Secret)`                  |
| Credentials        | `NewWebProviderCredential(map[string]any{...})`                     | `WebProviderCredential::new(...)`                |
| Injected transport | Owned supplied RoundTripper; nil clones the default                 | No public injection/transport builder            |
| Timeout            | 30 seconds; per-call context also applies                           | 30 seconds; dropping a request future cancels it |
| Retry policy       | No SDK retry loop; underlying transport behavior is separate        | Explicit retry-never policy                      |
| Redirects          | Not followed                                                        | Not followed                                     |
| Proxy behavior     | Cloned Go default transport retains its normal environment handling | Explicit `no_proxy()`                            |
| Web response bound | 1 MiB                                                               | 1 MiB                                            |
| List default       | Zero option omits limit; Service default 50                         | `None` omits limit; Service default 50           |
| Close              | `Close()` cancels lifetime and closes idle connections              | `close()` cancels requests and releases the pool |

Go operations take `context.Context`; Workspace binding shares the parent lifetime. `Representation[T]` preserves `ETag` and `RequestID`. Handle `ApiError`, `ErrTransport`, `ErrProtocol`, and `ErrClosed`; caller cancellation propagates its context error.

Rust returns typed representations and `Error::{Api, Transport, Protocol, InvalidInput, Closed}`. `ApiError` preserves safe status/code/details/request/retry information. A Workspace client borrows its parent. In both languages, optional request helpers distinguish omission, explicit null, and replacement; do not collapse them when serializing Run overrides. `WebProviderCredential` retains an arbitrary nested JSON object and redacts ordinary diagnostics and serialization; only the Web request boundary reveals it.

See the source package READMEs for language-native examples: [Python](https://github.com/converge-ai-labs/agent-foundation/tree/main/sdk/python), [Go](https://github.com/converge-ai-labs/agent-foundation/tree/main/sdk/go), [Rust](https://github.com/converge-ai-labs/agent-foundation/tree/main/sdk/rust), and [TypeScript](https://github.com/converge-ai-labs/agent-foundation/tree/main/sdk/typescript).

## Generated low-level HTTP

Python imports models and operation modules from `a13n.generated`. Run `await client.execute(lambda api: operation.asyncio_detailed(..., client=api))`. The returned response retains status, headers, raw content and the typed success/error union. Request models are attrs classes, separate from the stable Pydantic Web facade. Use generated enums in constructors; `UNSET` omits a field and `None` sends null. For binary upload, `File(payload=binary_file)` is sent in bounded async chunks. For download, use `async with client.stream(operation.build_request(...))` and consume `aiter_bytes()` inside the context.

Go imports full schemas from the module's `generated` package. `client.API()` returns the generated operations over the same transport. `WithResponse` methods decode typed status-specific bodies and retain the underlying response headers. Raw methods return `*http.Response` for streaming; callers close its body. Nullable fields use `nullable.Nullable[T]`, and generated union methods expose typed branches.

Rust imports `a13n::generated::{apis, models}` and uses `client.execute(async |api| operation(api, ...).await).await`. Ordinary JSON results contain `data`, `status` and `headers`; generated operation errors preserve their response evidence. Optional nullable fields use `Option<Option<T>>`. Binary response bodies must be consumed inside the closure to stay within owner cancellation.

These entry points share the Web client's authentication, pool and lifetime, but retain low-level result types rather than the Web facade's error mapping or 1 MiB JSON response bound. They perform no automatic mutation retries. Low-level generated bindings are not complete JSON Schema validators, pagination workflows, SSE reconnectors, or WebSocket clients. `RunStatus` preserves unknown strings in Python/Rust without opening closed union discriminator tags. Use the actual returned status and the documented schema's error union rather than assuming that completion implies success.

## Compatibility and development

Use an SDK built for the Service contract you deploy. Documentation describes the checked source contract, not a claim about the newest package on a registry. SDK directories have standalone manifests and lockfiles outside the root Python/Rust workspaces. Release versions are independent.

From the repository root, `make sdk-check` runs fast checks and `make sdk-check-all` runs full SDK gates. `make sdk-generate` exports the shared Service schema and regenerates all four language bindings. The pre-commit hook reruns it on relevant source changes without staging files. `make sdk-generated-check` verifies the live schema and regenerates into temporary directories to detect changed or stale files without modifying committed output. SDK CI runs this consistency check and every affected language gate. The companion CLI has its own `make a13n-service-cli-check` and `make a13n-service-cli-check-all` targets.

The process/operator executable is **`a13n-service`**. Its serve, database, identity, and config commands are not remote SDK operations. The **`a13n-service-cli`** currently supports remote label reads and replacements through the Rust SDK; use Native HTTP or an implemented SDK for other remote work.
