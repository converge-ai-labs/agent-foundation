# Foundation Service SDKs and Clients

## Design Position

Foundation publishes independent Python, Go, Rust, and TypeScript SDKs for the complete public Native Foundation Service contract. The SDKs map the same resources, trace query views, commands, receipts, errors, pagination, Run SSE, Workspace lifecycle events, and Native notifications without creating language-specific lifecycle or retry semantics.

The remote `agent-foundation` CLI is a first-party client of that public boundary. Standard AG-UI and A2A clients call their respective Gateway protocols directly and do not need a Foundation SDK.

## Boundaries

| Concern                                                  | Owner                                                                          |
| -------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Native resources and command semantics                   | [Foundation Management API](16-management-api.md) and owning domains           |
| Shared HTTP, errors, concurrency, and idempotency        | [Platform API Conventions](../api-conventions.md)                              |
| Run SSE, lifecycle events, and notification WebSocket    | [Native Streaming and Notifications](21-native-streaming-and-notifications.md) |
| Language transport, public types, and idiomatic lifetime | Each SDK                                                                       |
| CLI command composition and presentation                 | `agent-foundation` CLI                                                         |

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

Request types and serializers preserve omitted fields separately from explicit `null` whenever the owning operation distinguishes them. For [successor inline Hooks](26-hook-notifications.md#successor-inline-subscriptions), Feedback, waiting Continue, and terminal Retry expose omission for inheritance, `null` for no subscription, and a complete object for replacement. SDK defaults emit omission, and explicit opt-out emits JSON `null`; neither default filling nor null filtering may collapse those states. The server resolves inheritance, so clients do not read and copy a mutable subscription before submitting the command. HookSubscription resource types expose `inline_run_id` and `expired_at` separately from enablement and deletion.

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

Mutation replay preserves the original Hook field presence and value along with the key. It returns the original acceptance receipt, including the same Run and subscription IDs; it does not invoke the terminal Run Retry operation or recreate an expired subscription. An explicitly requested terminal Retry instead accepts a new Run with the server-owned Hook selection semantics.

## Foundation CLI

The `agent-foundation` executable is the remote command-line client. Every network operation calls the Rust SDK; the CLI owns no second HTTP serializer, authentication transport, SSE parser, WebSocket client, retry engine, or service process behavior.

A CLI command exists only when its service operation and Rust SDK method are real. The CLI can offer interactive terminal presentation, follow a Run stream, or watch notifications, but Foundation defines no separate remote terminal product or remote Session model.

## Standard Protocol Clients

Hosted AG-UI and A2A remain independently interoperable standards. Foundation SDKs can add convenience factories or typed references for their URLs, but a client never needs Foundation-specific serialization to use a standard AG-UI Run or A2A Task. Such helpers cannot introduce another event model or hide standard protocol errors.

## Release and Compatibility

The four SDK languages version and release independently under the repository release model. Release independence does not permit behavioral drift: the repository compatibility matrix and end-to-end tests identify the server API line, stream subprotocol, and feature set supported by each SDK release.

The Rust CLI releases independently from the Rust SDK source package but pins a compatible SDK dependency.

## Invariants

1. Python, Go, Rust, and TypeScript expose the same Native service resources and lifecycle meaning.
2. SDK retries require authoritative replay evidence.
3. Stream and client close never cancel a Run.
4. Native notifications remain best-effort wake-ups and are reconciled through durable APIs.
5. The remote CLI performs every network operation through the Rust SDK.
6. Standard AG-UI and A2A clients require no Foundation SDK.
7. Asset helpers preserve one-create-per-publication identity, caller-supplied idempotency keys, and streaming binary transfer; they never expose object-storage keys or emulate in-place replacement.
