# Foundation Service SDKs and Clients

## Design Position

Foundation publishes independent Python, Go, Rust, and TypeScript SDKs for the complete public Native Foundation Service contract. The SDKs map the same resources, trace query views, commands, receipts, errors, pagination, Run SSE, Workspace lifecycle events, and Native notifications without creating language-specific lifecycle or retry semantics.

Foundation Web and the remote `agent-foundation` CLI are first-party clients of that public boundary. Standard AG-UI and A2A clients call their respective Gateway protocols directly and do not need a Foundation SDK.

## Boundaries

| Concern                                                  | Owner                                                                          |
| -------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Native resources and command semantics                   | [Foundation Management API](16-management-api.md) and owning domains           |
| Shared HTTP, errors, concurrency, and idempotency        | [Platform API Conventions](../api-conventions.md)                              |
| Run SSE, lifecycle events, and notification WebSocket    | [Native Streaming and Notifications](21-native-streaming-and-notifications.md) |
| Language transport, public types, and idiomatic lifetime | Each SDK                                                                       |
| CLI command composition and presentation                 | `agent-foundation` CLI                                                         |
| Browser navigation and user experience                   | Foundation Web                                                                 |

An SDK does not own service startup, migrations, Worker control, persistence, internal operator routes, direct Redis/object access, or another HTTP contract.

## SDK Parity

The standalone SDK projects under `sdk/{python,go,rust,typescript}` all belong to the Gateway completion boundary. For every public Native operation included in the OSS distribution, each SDK exposes:

- typed request, resource, receipt, page, and safe error values;
- authentication configuration for supported browser or bearer use where the language environment permits it;
- request ID and upstream correlation access;
- explicit idempotency keys and version or ETag preconditions;
- deterministic collection pagination;
- bounded Trace list and detail reads through the configured query provider;
- streaming Asset upload and content download without whole-body buffering;
- Run SSE consumption and cursor recovery;
- Workspace lifecycle event reconciliation;
- Native WebSocket notification subscription and close; and
- bounded cancellation and transport shutdown.

The SDK source version and the server `/api/v1` version remain independent. An SDK release declares the server compatibility line it supports and tolerates additive response fields and unknown enum values under the shared API contract.

SDK method names follow each language's conventions while preserving the same resource and command meaning. No language renames a Run to Run, maps a RunAttempt to a request retry, or treats stream close as cancellation.

## Transport Lifetime

Each client instance owns its connection pools and long-lived transports and has an explicit close operation. Closing an iterator, stream, WebSocket, SDK client, page traversal, or process stops only local delivery and network resources. It never submits a Run interrupt command implicitly.

Language surfaces use their native asynchronous model:

| SDK        | Long-lived delivery shape                                                                 |
| ---------- | ----------------------------------------------------------------------------------------- |
| Python     | async context manager and async iterator                                                  |
| Go         | caller `context.Context`, explicit iterator/channel ownership, and `Close`                |
| Rust       | asynchronous `Stream`, cancellation token or future context, and explicit client shutdown |
| TypeScript | `AsyncIterable`, `AbortSignal`, and explicit client close                                 |

A language can add a synchronous convenience layer only when it preserves the same operation and does not block an async service path or hide connection ownership.

## Run SSE and Cursor Recovery

Every SDK exposes a Run stream event containing the stable event identity, event type, Run correlation, parsed payload, and received cursor. Cursor persistence is an application decision; the SDK never advances an external checkpoint before yielding the corresponding event successfully.

Reconnection sends the last fully applied cursor through `Last-Event-ID`, uses bounded backoff, honors safe server retry guidance, and stops on authentication, authorization, schema, or replay-gap failures. A replay gap is a typed result that directs the application to current Run, Item, and pending-action reads; it is not silently skipped.

## Notifications and Reconciliation

SDKs expose explicit `thread` and `workspace` subscriptions using the stable Native topic names. A new connection has no subscription. Reconnect creates a new WebSocket, repeats only application-selected subscriptions, and reports an observation gap to the caller.

SDKs never present Native notifications as durable events. Applications reconcile notification gaps through the Workspace event collection and current resource reads. The SDK can provide a helper that performs this sequence, but it returns the owning resources and cursors rather than manufacturing a merged event history.

## Errors and Retries

All SDKs expose one common Foundation API error base carrying HTTP status, stable code, message, safe details, and request ID. Language-specific subclasses or enums exist only for stable actionable distinctions. Transport, protocol, decode, replay-gap, and caller cancellation remain distinguishable from a service-reported resource failure.

Automatic retry is limited to:

- bounded reads whose owning contract permits replay;
- reconnecting a delivery attachment from explicit cursor evidence; and
- mutations carrying the same retained `Idempotency-Key` and canonical request when the owning command permits it.

The SDK honors `Retry-After` and the caller's deadline. It never changes an idempotency key after lost acknowledgement and never labels an unknown mutation outcome successful or failed without authoritative reconciliation.

## Foundation CLI

The `agent-foundation` executable is the remote command-line client. Every network operation calls the Rust SDK; the CLI owns no second HTTP serializer, authentication transport, SSE parser, WebSocket client, retry engine, or service process behavior.

A CLI command exists only when its service operation and Rust SDK method are real. The CLI can offer interactive terminal presentation, follow a Run stream, or watch notifications, but Foundation defines no separate Remote TUI product or remote Session model.

## Foundation Web

Foundation Web uses only public Native API, including Trace Query, Run SSE, Workspace event, and notification WebSocket contracts. It does not query Foundation tables, Redis, object storage, trace backends, Worker endpoints, or internal operator routes.

Foundation Web implements the product's supported browser workflows; it is not required to provide a page for every administrative resource in the Management API. An absent browser page does not remove the corresponding public API or SDK contract. Browser cookie authentication, Origin, CSRF, and same-origin behavior follow the shared ingress and IAM contracts.

### Model setup and configuration

Foundation Web implements the [Model Management](30-model-management.md) setup flow: choose a Provider type, enter credentials and necessary configuration, save, discover candidates when supported, and explicitly add selected Models. It provides search and traversal of discovery results and an always-available manual-add entry. Discovery errors, unsupported discovery, and successful empty results remain distinguishable; none removes the saved Provider or manual entry. Adding several candidates reports success or failure per Model using the ordinary create operation.

Discovered and manually entered model IDs use the same configuration editor and public description operation. The editor prefills the suggested API, settings, and available metadata and lets users adjust them before saving. It displays the canonical API key, including `openrouter.chat_completions`, and allows selection only among that Provider's supported APIs. Changing the API refreshes its parameter description without silently deleting existing input; incompatible values require correction before save. A missing catalog entry or unknown capability does not make a manually entered ID invalid.

Parameter forms use the returned settings schema and support information rather than a client-maintained model catalog. Common fields have ordinary controls; advanced settings include JSON editing and the supported extra-body escape hatch over the same settings object. Known unsupported parameters are not recommended by ordinary forms, while unknown support is identified without blocking manual configuration. API-schema type errors and upstream invocation failures are presented distinctly. Metadata is displayed as text and inert form data, never executed or rendered as trusted markup.

Editing a saved Model starts from saved values, not newly suggested defaults. Settings absent from a new recommended form remain visible and editable in advanced configuration; refreshing descriptions never removes them. Applying suggestions is an explicit user edit. Displayed profile/limit information is distinguished from request settings, so editing a capability label is not presented as enabling an upstream feature.

Agent configuration selects only a Model key and optionally edits settings overrides using that Model's description. SDKs and the CLI expose the same single-API Model, description, discovery, manual-create, and test contracts without requiring the browser or a bundled catalog. Model Management remains the sole owner of their fields, validation, and precedence rules.

## Standard Protocol Clients

Hosted AG-UI and A2A remain independently interoperable standards. Foundation SDKs can add convenience factories or typed references for their URLs, but a client never needs Foundation-specific serialization to use a standard AG-UI Run or A2A Task. Such helpers cannot introduce another event model or hide standard protocol errors.

## Release and Compatibility

The four SDK languages version and release independently under the repository release model. Release independence does not permit behavioral drift: the repository compatibility matrix and end-to-end tests identify the server API line, stream subprotocol, and feature set supported by each SDK release.

The Rust CLI releases independently from the Rust SDK source package but pins a compatible SDK dependency. Foundation Web ships as private application assets inside the Foundation Service image and is validated against the same public contract.

## Invariants

1. Python, Go, Rust, and TypeScript expose the same Native service resources and lifecycle meaning.
2. SDK retries require authoritative replay evidence.
3. Stream and client close never cancel a Run.
4. Native notifications remain best-effort wake-ups and are reconciled through durable APIs.
5. The remote CLI performs every network operation through the Rust SDK.
6. Foundation Web uses only public Native surfaces and need not mirror every management resource as a page.
7. Standard AG-UI and A2A clients require no Foundation SDK.
8. Asset helpers preserve one-create-per-publication identity, caller-supplied idempotency keys, and streaming binary transfer; they never expose object-storage keys or emulate in-place replacement.
