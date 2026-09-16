# Language Compatibility and First-Party Clients

## Design Position

The four language SDKs have independent source, validation and release boundaries but one shared behavioral contract. Interoperability is defined by Service operation/protocol meaning and the module designs, not by one language's class layout or generated symbol names.

[Repository Model](../repository-model.md) owns package/release separation. [Architecture](00-overview.md) owns module boundaries; [Types and Requests](02-types-and-requests.md) owns wire compatibility and operation identity. This document owns language adaptation, coverage claims, cross-language acceptance, and first-party client consumption.

## Compatibility Axes

| Axis                              | Owner                        | SDK responsibility                                             |
| --------------------------------- | ---------------------------- | -------------------------------------------------------------- |
| SDK package version               | Each language release        | Declare supported server/protocol/helper capabilities          |
| Native API line                   | Service public API           | Preserve exported operation meaning and additive compatibility |
| Stream/notification protocol      | Service protocol owner       | Negotiate and validate the supported profile                   |
| Resource/Revision version         | Owning domain                | Expose as resource/evidence, never package compatibility       |
| Generated tool version and output | SDK code generation boundary | Reproduce bindings and detect stale output                     |
| CLI version                       | Remote CLI release           | Pin a compatible Rust SDK dependency                           |

An SDK release does not imply a matching Service version or identical feature coverage in another language release. Release independence does not authorize different queue, feedback, cancellation, pagination or retry semantics.

## Language Adaptation

| Language   | Natural client/observation shape                                               | Shared behavior                                             |
| ---------- | ------------------------------------------------------------------------------ | ----------------------------------------------------------- |
| Python     | Async calls, context managers and async iterators                              | Explicit ownership, cancellation and applied checkpoints    |
| Go         | Context-bearing calls, typed values/errors, owned iterators/channels and Close | Same deadlines, errors and receipt identities               |
| Rust       | Futures/Streams, typed results and explicit shutdown                           | Same mutation evidence and resource state meaning           |
| TypeScript | Promises, AsyncIterable, AbortSignal and explicit close                        | Same protocol boundaries within browser/server capabilities |

Public names use language-native casing and parameter/error idioms. Generated types can be re-exported or wrapped only where doing so adds stable client behavior; complete duplicated DTO hierarchies are not required. A language need not wait for another language's implementation or depend on its runtime to implement a module.

Browser file access, cookie/CSRF behavior, and connection constraints remain environment-specific capabilities. A package cannot claim an unsupported platform feature by emulating filesystem access or changing authentication. Optional synchronous surfaces preserve explicit lifetime and never conceal blocking work in asynchronous execution paths.

## Coverage Model

Coverage has distinct levels:

1. Exported/generated HTTP operation access.
2. Named L2 resource methods with the common request/metadata/error behavior.
3. Correct long-lived observation protocols and reconciliation.
4. The small module-owned observation helpers.
5. Real Service journeys demonstrating cross-resource behavior.

The shared operation identifier is method plus path; stream/helper identities belong to their owning module contract. A directory, generated type, or named method alone does not prove lifecycle coverage. A module's operation mapping preserves actual required headers, queries, schemas and response branches. A Service-contract/export mismatch blocks a conformance claim for that operation rather than being patched independently in each language.

Implementation matrices and release status belong in SDK documentation and tests, not as progress notes in the accepted specifications. Shared fixtures and language tests provide evidence; this document does not assert that every listed case already has an executable test or passes in every release.

## Shared Acceptance Matrix

| Capability block              | Required cases                                                                                                                                                      | Semantic owner                                                     |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Client and request foundation | Credential-derived scope; explicit scope; parent/child close; deadline; presence and structured unions; safe metadata/errors; unknown response values               | [Client](01-client-contract.md), [Types](02-types-and-requests.md) |
| Agent/Model authoring         | Publication versus metadata; unchanged revision result; exact revision failure; Model test/discovery independence; scoped dependencies                              | [Agents and Models](03-agents-and-models.md)                       |
| Interaction                   | Root start; empty Thread first input; accepted/queued receipt; feedback successor; historical continuation; Fork; Retry; steer/interrupt receipt                    | [Interaction](04-interaction.md)                                   |
| Queue                         | Entry/queue/Thread version conflicts; complete-intent editing; reorder set conflict; consumed Run identity; permanent failure versus blocker; deletion/read absence | [Queue](05-queued-submissions.md)                                  |
| Observation                   | Applied-versus-received progress; duplicate replay; protocol/gap failure; atomic subscription rejection; reconnect acknowledgment; durable catch-up                 | [Observation](06-streams-and-notifications.md)                     |
| Environments                  | Allocation versus preparation; presence inheritance; exact logical identity; command acceptance versus target result; rebuild uncertainty                           | [Environments](07-environments.md)                                 |
| Content                       | Streaming upload/download; source ownership; repeatability; separate staging/publication/submission; Skill unchanged publication; deletion/reference behavior       | [Assets and Skills](08-assets-and-skills.md)                       |
| Integrations                  | Save/authorize/check/select separation; provider-specific concurrency; typed Tool selection; explicit Memory subject; unconfirmed external write                    | [Integrations](09-tools-and-integrations.md)                       |
| Administration                | Principal/credential/grant separation; one-time issuance; explicit replacement/revoke; invitation delivery versus acceptance; current scope authorization           | [Identity](10-identity-and-administration.md)                      |
| Operational reads/delivery    | Inline Hook presence; redrive identity; separate lifecycle/telemetry state; provider Trace continuation; safe audit scope                                           | [Hooks and Diagnostics](11-hooks-and-diagnostics.md)               |

Serialization fixtures establish field/union/header correctness. Deterministic transport tests establish uncertainty, ownership and replay behavior. Real Service journeys establish acceptance, concurrency and cross-resource semantics using controlled Agent execution; production-provider verification is a separately stated scope. Formatting and code generation are not substitutes for these tests.

## Small Helpers and No Hidden Runtime

The required helper surface remains the Run and queued-submission read-only waits defined by their owning modules. Their deadline, terminal-state, identity and failure behavior is identical across languages even when APIs differ syntactically. SDKs do not add language-specific automatic approvals, client-tool loops, recursive subagent orchestration or successor traversal under those method names.

A caller can compose accepted receipts and waits without a new SDK task ID. Separate application libraries can build product workflows above L2, but must not claim those policies are part of the Service resource contract.

## Remote Service CLI

`a13n-service-cli` performs every Service network operation through the Rust SDK. It owns argument parsing, configuration presentation, output formatting and explicit user-driven composition. It owns no second HTTP serializer, credential transport, SSE parser, WebSocket client or retry engine.

A command is exposed only when the Service operation and Rust SDK method are real. Interactive follow/watch is local presentation; closing it does not cancel Service work. Any command that interrupts, retries, deletes or redrives must explicitly invoke the corresponding SDK mutation and expose its actual receipt/outcome.

The remote CLI is distinct from the process/operator `a13n-service` executable. It does not start Service roles, run migrations, operate Redis/object storage, or manage Worker lifecycle. Its independent release pins a compatible SDK rather than introducing another server API line.

## Console and Standard Clients

Console consumes the same public SDK and Service boundary while owning browser presentation and product workflow. It does not move Service authorization, generated schemas or retry policy into component-local HTTP clients.

AG-UI and A2A clients remain independently interoperable through their Gateway protocols. Optional typed URL references or factories cannot require Native serialization, replace standard errors, invent another event model, or make use of the standards depend on this SDK.

## Invariants

1. Languages can implement independently but cannot redefine the same operation's meaning.
2. Coverage distinguishes binding existence from behavior and real Service evidence.
3. Package, API, protocol and resource versions remain separate.
4. First-party clients compose the SDK instead of duplicating its transport policy.
5. Standard protocol use does not require Service-specific client serialization.
