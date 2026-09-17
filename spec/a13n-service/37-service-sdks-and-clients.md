# Service SDK Design and Contract Distribution

## Design Position

The Service SDKs provide language-native, typed access to managed Agents and the public Native resources around them. Resource objects are the primary application interface: developers select an Agent, start or continue work, observe a particular Run, and manage existing resources without assembling HTTP paths or repeatedly supplying resource identities. Bounded convenience methods remove repetitive protocol work. The complete low-level protocol surface remains available for advanced control, but raw HTTP bindings are not the default developer experience.

The SDKs expose existing Service concepts rather than introducing another business-interaction, task, or workflow identity and lifecycle. Applications decide which requests belong to one business operation, how to orchestrate multiple Runs, and when their business objective is satisfied. The SDK is neither a local Agent execution engine nor an application workflow runtime.

This document owns the shared SDK product contract, resource-object responsibilities, and client consumption boundary. Each independent SDK repository owns its language-specific specification, concrete public API, implementation, generation policy, tests, and releases. A Service change supplies accurate exported contracts, protocol evidence, and compatibility semantics; its completion does not require building or publishing downstream SDKs. A shared design requirement is not a claim that every released SDK or deployed Service already implements it.

## Authority

| Concern                                                                             | Owner                                                                                                                        |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Native resources, commands, and receipts                                            | [Service Management API](16-management-api.md) and owning domains                                                            |
| Session, Thread, Run, and Item meaning                                              | [Platform Interaction Model](../interaction-model.md)                                                                        |
| Authentication, errors, concurrency, and idempotency                                | [Platform API Conventions](../api-conventions.md) and [Identity and Access Management](33-identity-and-access-management.md) |
| Run SSE, lifecycle events, and notification WebSocket                               | [Native Streaming and Notifications](21-native-streaming-and-notifications.md)                                               |
| Shared SDK experience, resource-object responsibilities, and convenience boundaries | This document                                                                                                                |
| Exported HTTP and wire-model definitions and shared protocol evidence               | Service, under `proto/a13n-service/`                                                                                         |
| Language-specific APIs, types, runtime support, generators, tests, and releases     | Each independent SDK repository                                                                                              |
| Application orchestration, business completion, and durable application checkpoints | The consuming application                                                                                                    |
| Console client, generated types, and application behavior                           | [Console](../frontend/console.md)                                                                                            |
| Remote CLI implementation and release                                               | Rust SDK repository                                                                                                          |

A client consumes public protocols, not Service startup, migrations, Worker control, persistence, internal operator routes, Redis, or object-store credentials. Hosted AG-UI and A2A remain independently interoperable standards; their clients require no a13n SDK. The CLI consumes the Rust SDK rather than implementing a second Service transport.

## Public Experience

The SDK supports three complementary uses through the same public Service boundary:

- **Managed Agent use:** select an Agent and configuration version, start work, continue an existing Thread, observe output, and explicitly respond to deferred requests.
- **Resource management:** discover, read, and manage authorized resources such as Agents and Revisions, Sessions, Environment Providers and Templates, actual Environments, Skills, Assets, and Connections.
- **Precise protocol access:** use the complete supported Native HTTP surface and inspect response evidence when an operation has no convenience method or requires lower-level control.

These uses do not define separate protocols, credentials, or resource hierarchies. The supported protocol surface does not depend on every operation having a handwritten convenience method. Applications can mix resource objects and lower-level calls within the same explicit client configuration and lifetime.

### Language-Native Shape

Resource objects carry typed operations, not just decoded response data. Object-oriented convenience does not require a common inheritance hierarchy, identical method names, or a literal class in every language.

| Language   | Primary usage model                                                                              |
| ---------- | ------------------------------------------------------------------------------------------------ |
| Python     | Asynchronous operations and iteration, typed values, and explicit asynchronous resource lifetime |
| Rust       | Asynchronous operations and streams, typed results, and explicit ownership and cancellation      |
| TypeScript | Promise-based operations, asynchronous iteration, typed values, and explicit cancellation        |
| Go         | Ordinary typed methods and iteration with explicit context, cancellation, and error handling     |

Python, Rust, and TypeScript are async-first. Go uses its native concurrency model rather than an artificial future-based API. Language specifications own concrete types, runtime and browser support, cancellation primitives, and any additional synchronous surface. An existing synchronous API is not implicitly removed by this design. Cross-language consistency means equivalent resource and failure semantics, not syntax parity or lockstep releases.

## Resource Objects

The following table defines responsibilities, not mandatory class names or wire schemas. A language may distinguish lightweight references, loaded representations, and collection accessors while keeping the same resource identity and observable behavior.

| Object role                            | Responsibility                                                                                    | Boundary                                                                        |
| -------------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Client                                 | Explicit connection configuration, authentication, scope, resource access, and local lifetime     | Creates no implicit Agent, Thread, or business workflow                         |
| Agent                                  | Reference a managed Agent, select a version, read or manage its configuration, and start work     | Does not own a hidden current Thread or a second execution engine               |
| Session                                | Read and manage one Service-owned interaction scope and navigate its root and child Threads       | Is not an SDK workflow or a single continuation state                           |
| Thread                                 | Access one continuing history, submit input, and inspect its Runs and queued submissions          | Does not assign one business result to all past and future work                 |
| Run                                    | Read, wait for, observe, and explicitly control one exact accepted Run; submit permitted feedback | Its identity never changes to a feedback successor, retry, or later current Run |
| Queued submission                      | Inspect, edit, withdraw, or wait for one queued input's disposition                               | Is not a Run before consumption; waiting does not itself consume the queue      |
| RunAttempt                             | Read one Run's operational attempt history and diagnostics                                        | Is not another application Run and exposes no Worker control authority          |
| Item                                   | Read retained semantic output with its exact Run and Item identity                                | Is neither a transport event nor complete resumable Harness state               |
| Other resource objects and collections | Expose authorized operations and typed representations of their existing Service resources        | Add no parallel SDK-owned resource identity or lifecycle                        |

A resource object binds the caller's explicit client context and resource selector or identity. IDs and local bindings grant no authority. Workspace binding reduces repeated arguments without broadening credential scope. Workspace configuration collections can include Organization-owned resources; handles preserve actual ownership separately from the consuming scope, and mutations address the owning scope under current authority. Retrieval through a Workspace never implies local ownership, a same-name override, or permission to manage parent configuration. One Agent reference can be used across multiple Threads and concurrent calls; reusing it never silently shares conversation state.

### Service Concept Coverage

The [Management API catalog](16-management-api.md#resource-route-catalog) and the domain owners define the public surface. The following coverage map identifies SDK-facing responsibilities, not another route catalog or a requirement for one class per row. Resource operations remain typed and discoverable; read-only projections, configuration values, and command receipts need not become mutable objects. Coverage is evaluated against the SDK's declared Service contract and distribution, not inferred from a method's presence.

| Concept family and owner                                                                                                                                                                                                                           | SDK-facing responsibility and boundary                                                                                                                                                                                                                                               |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [IAM](33-identity-and-access-management.md): Organizations, Workspaces, Users, Service Accounts, RoleBindings, invitations, credentials, authentication sessions, and security audit                                                               | Preserve explicit scope, supported authentication flows, self-service restrictions, and safe credential projections; a bound Workspace or cached role is not authorization. Authentication sessions are not interaction Sessions.                                                    |
| [Agents and Revisions](28-agent-management.md), [Models and Model Providers](30-model-management.md), [Web Providers](41-web-provider-management.md), [Secrets](27-secret-management.md), and [installed plugins](36-installed-harness-plugins.md) | Expose authorized configuration, selection, lifecycle, discovery, and supported tests. Discovery values are not automatically managed resources; trusted provider/plugin types are not SDK installation APIs. Secret reads remain metadata-only and provider credentials write-only. |
| [Skills and revisions](31-skill-management.md), [Assets](32-asset-management.md), and [Agent input](17-agent-input.md)                                                                                                                             | Preserve staging versus publication, immutable package revisions versus immutable Asset bodies, exact references, binary transfer, and content authorization. An Asset has no replacement or revision operation.                                                                     |
| [Sessions and Threads](11-thread-persistence.md), [Runs](12-run-persistence.md), [RunAttempts](13-run-attempt-scheduling-and-recovery.md), Items, pending actions, and [interaction retrieval](35-agent-interaction-retrieval.md)                  | Provide authorized navigation, history, lineage, and semantic projections without exposing private persisted state or Worker scheduling controls. Thread current Run, selected continuation head, and complete ancestry are distinct.                                                |
| [Queued submissions](20-agent-control-queued-submissions.md) and [active control](19-agent-control-active-execution.md)                                                                                                                            | Expose ordered queued intent and supported edits, reordering, withdrawal, explicit consumption, steering receipts, and interruption without hiding acceptance or consumption boundaries.                                                                                             |
| [Environment Providers, templates/revisions, and actual Environments](29-environment-management.md), including [live mounts](29a-websocket-environments-and-live-mounts.md)                                                                        | Preserve reusable configuration, fixed Run selection, backing generation, connection status, lifecycle operations, and per-Run mount application as separate facts. Service SDK access is not direct EIP or native process ownership.                                                |
| [Memory Providers, scopes, documents/revisions/changes, and native records](42-memory.md)                                                                                                                                                          | Bind reads and mutations to the authorized subject and exact Provider/storage scope. Do not infer a corpus from the latest Run, merge sandboxes, or emulate unsupported backend revision semantics. Committed writes and indexing readiness remain distinct.                         |
| [Connectivity](40-connectivity/README.md): Application Accounts, AccountTargets, Connector Providers, discovery values, Connections and Authorizations; [Schedules](16-management-api.md#resource-route-catalog)                                   | Expose Service-owned configuration, setup/status, and scheduling resources, not provider-native SDKs or a local scheduler. Account reception, remote authorization, Run acceptance, and external reply delivery remain separate outcomes.                                            |
| [Configuration assistant](43-agent-configuration-assistant.md): readiness, configuration conversations, drafts, and application receipts                                                                                                           | Reuse Service Session/Thread/Run identities through the dedicated authoring entry. A reviewed draft is applied by an explicit user command; assistant output does not publish a business Agent Revision.                                                                             |
| [Native streams and events](21-native-streaming-and-notifications.md), [Hook subscriptions](26-hook-notifications.md), [raw usage](25-events-usage-and-delivery.md), and [Trace queries](39-trace-query.md)                                        | Keep transport observation, durable Hook delivery, usage attribution, and provider-backed telemetry distinct. Traces are read projections, not durable execution authority, a billing ledger, or a replacement for Run results.                                                      |

[Connection authorization](40-connectivity/03-connectors-and-connections.md#authorization) helpers preserve the operation status, `next_action`, current Connection readiness, and bounded check observations separately. Browser return is not authorization completion; the application validates its state and explicitly completes the authenticated handoff. Completed authorization does not imply a ready Connection, and a successful check does not guarantee a later tool invocation.

Internal inboxes, leases, state objects, outbox records, and provider credentials do not become public SDK resources because Service persists them. Hosted AG-UI, A2A, external provider protocols, and EIP retain their own contracts rather than being relabeled as Native resource methods.

### References, Representations, and Lifetime

Constructing a lightweight reference performs no network request. Loading or refreshing a representation is an explicit operation. Ordinary property access reads locally available values and never hides network I/O. A loaded representation is a snapshot, not a live mirror of server state; its freshness and mutation preconditions remain visible to the caller.

A reference selected by key or current version is not proof of which immutable configuration executed. Accepted Run evidence retains the exact Agent and configuration provenance selected by Service: the ordinary AgentRevision or the protected configuration assistant's frozen definition with a null `agent_revision_id`. The SDK neither fabricates a Revision for that exception nor exposes the hidden assistant through ordinary Agent invocation. The SDK preserves the distinction between a convenient selection and a resolved execution identity. Agent configuration, version selection, and invocation overrides follow [Agent Management](28-agent-management.md); native callables and model classes are not serialized as server-executable configuration.

Resource objects share their parent client's configured transport lifetime. Obtaining another handle does not require another independently managed connection pool. Closing the client stops owned local work and makes its bound objects unavailable for further network use; it does not delete resources, interrupt durable Runs, or destroy Environments. Reacquiring a resource through another client is explicit. Independently constructed low-level clients have their own documented lifetime.

Resource changes use explicit operations, not local field assignment with implicit remote persistence. Typed mutation inputs retain omission, explicit null, and value distinctions. ETags, version preconditions, request IDs, command receipts, and relevant response headers remain accessible rather than being discarded for a shorter success path. A convenience method does not resolve a concurrency conflict by silently fetching a newer version and overwriting it.

## Submission, Observation, and Control

### Input and Content

Input conveniences produce the versioned [AgentInput](17-agent-input.md) contract, not a second chat-message protocol. They preserve ordered text/binary blocks, the independent structured-content channel, source and delivery choices, and supported empty input. Strings can provide a concise text path without making text the only input shape. New SDK submissions use the owner's default schema version; retained older inputs keep their original interpretation. Control options, Agent overrides, and Environment selection remain outside semantic content.

A local file or byte stream is not a remote Environment path. A transfer convenience explicitly publishes content through a supported Asset or Environment operation and then submits its reference; it does not embed unsupported inline binary or silently reinterpret paths. Publication and Run acceptance are separate effects. Partial failure retains any successful publication identity and does not claim that submission rolled it back. Content reads preserve authorization, media metadata, and delivery restrictions rather than exposing storage credentials or treating references as public URLs.

### Starting and Continuing Work

Common entry paths can combine supported Service operations, such as starting a root Thread with its first Run, without requiring applications to construct every enclosing object first. Creating an empty Thread remains a distinct supported operation. The selected entry path and its creation effects are explicit, and the resulting Session, Thread, and Run identities remain available.

Existing-Thread submission preserves the actual receipt from [Queued Submissions](20-agent-control-queued-submissions.md): the input was accepted as a Run or stored as a queued submission. An ergonomic common submission method does not manufacture a Run ID for the queued branch. Editing or withdrawing queued input is distinct from controlling an accepted Run. If a caller chooses to wait for queue consumption, the helper reports the resulting Run or the queue entry's actual non-consumption outcome.

Continuation follows the selected source under [Input and Continuation](18-agent-control-input-and-continuation.md), not a guess from the most recently observed Run. Thread current Run and selected continuation head remain distinct. Historical continuation keeps the Thread; Run-scoped fork creates a new child Thread and its first Run in the existing Session. A separate Session fork creates a new Session and root Thread and is never implied by Run-scoped fork. RunAttempt recovery stays within one Run, whereas explicit Retry creates another Run. A flattened history convenience identifies its lineage and retained scope rather than presenting every Thread Run as one linear conversation.

Primary Environment selection remains separate from Agent behavior overrides. A Thread carries a mutable default; an accepted Run fixes its own selection. Omitted, explicit no-environment, existing-Environment, and new-from-template choices preserve [Environment Management](29-environment-management.md#thread-defaults-and-run-selection). Resource wrappers do not freeze an Environment for the entire Thread or redirect an existing Run through a later default.

[Live Environment mounts](29a-websocket-environments-and-live-mounts.md) are explicit additions to one Run, not replacements for its fixed primary selection. A mount receipt preserves acceptance separately from current-Attempt application status; an online connection alone proves neither authorization nor successful loading. Reconnection preserves the logical Environment identity, and closing an observer does not unmount or delete it.

### Waiting and Feedback

Waiting observes one specified Run. It reports completion, failure, cancellation, or the sealed waiting state requiring feedback; it does not follow the Thread's latest Run or infer success from a closed stream. Waiting has caller-visible timeout and cancellation behavior. A local wait timeout or cancellation does not imply a remote interrupt, rollback, or failed acceptance.

Under [Input and Continuation](18-agent-control-input-and-continuation.md#deferred-interaction), feedback is an explicit authenticated mutation against a sealed waiting Run and accepts a new successor Run. A wrapper preserves that successor identity rather than replacing the original Run object's identity. Retry and fork likewise return their actual resulting resources. Steer preserves its own acceptance and delivery receipt under [Active Execution](19-agent-control-active-execution.md); acceptance is not proof of incorporation by the originally observed Run.

Reading a resource, waiting for a Run, or subscribing to its events never implicitly executes a client tool, approves an action, submits feedback, or advances a Thread. An application can explicitly invoke a bounded helper that uses registered local handlers and constructs or submits feedback under the complete pending-set contract. The helper makes its effects and returned successor visible. Missing handlers, rejected approval, and tool failures are not silently converted into approval or success. Local tool side effects are not guaranteed exactly once by server feedback idempotency.

Explicit waiting Continue with defaults remains distinct from Feedback: it supplies the Service-defined defaults and new input under its own authorization and receipt contract. A generic submit helper does not silently choose it to bypass pending requests. Remote Interrupt targets an eligible exact Run and preserves outcome-race behavior; local cancellation does not become Interrupt, and interrupting a parent does not imply that independent asynchronous children were cancelled.

Local handler bindings belong to the application process, not persisted Agent or Thread state. Another process must explicitly supply the bindings it needs. The application owns any cross-Run loop, response policy, retry decision, and business completion boundary; the SDK does not hide that loop in a result accessor or an all-purpose workflow helper.

### Events, Items, and Results

Typed events, Item projections, and result access serve different consumption needs. The SDK can make each convenient without declaring a new common transport envelope or manufacturing an authoritative result from partial output. Text extraction and structured-output decoding are bounded conveniences; they do not redefine Run completion or application success.

Run events remain scoped to their exact Run. Durable lifecycle collections and best-effort notification attachments retain their distinct cursors, replay guarantees, and failure behavior. Checkpoints retain the resource scope required by the owning protocol. A client advances a consumption checkpoint only after the corresponding event has been applied, not merely received. Reconnect and reconciliation remain bounded and expose replay gaps or unavailable retained data rather than treating a surviving suffix as complete history.

Where supported by the pinned runtime and exports, display-snapshot reconciliation restores the covered Item projection rather than replaying missing original events. Run termination and display finalization remain separate facts. A helper cannot claim transparent recovery using metadata that the supported Service does not expose. [Native Streaming and Notifications](21-native-streaming-and-notifications.md) remains the sole owner of these semantics.

Inline delegation and [durable asynchronous subagents](34-async-subagents.md) remain distinct. Observing a parent Run does not automatically wait for every child or collect unrelated future successors. An optional composed view identifies its actual sources and recovery limitations; it does not invent a globally ordered, resumable Run-tree stream. Deterministic application orchestration uses language-native control flow rather than another Team, Network, or workflow runtime.

[Hook-subscription management](26-hook-notifications.md#sdk-notification-surface) is distinct from attaching a local event observer. SDK callbacks execute in the caller process and cannot become blocking Service hooks. Creating a durable Webhook subscription is an explicit remote mutation; stopping a local callback does not delete it. Webhook acceptance, delivery attempts, and receiver-side business effects retain the owner's duplicate and gap semantics.

[Trace reads](39-trace-query.md) preserve provider-backed availability, nullable values, supported search targets, and pagination limits. Telemetry gaps, child span errors, or a trace end time do not establish Run success or failure. Raw usage and RunAttempt outcome retain their durable owners; a convenience does not manufacture a complete trace tree or total cost from partial pages.

## Convenience Methods and Helpers

Convenience belongs on the resource object when its meaning is naturally scoped to that resource: waiting for a Run, iterating a Thread's history, or waiting for a queued submission's disposition. Independent helpers serve reusable operations that do not naturally belong to one resource. Neither placement changes the underlying protocol meaning.

A convenience operation has a bounded, explicit responsibility and observable stopping conditions. It can maintain temporary iterator, connection, or cursor state, but has no separate business identity, durable lifecycle, or hidden orchestration ownership. Inputs and outputs remain tied to existing resource identities, receipts, and typed values. Callers can leave the convenience path for lower-level control without losing those identities or constructing a competing client stack.

Common conveniences include lazy pagination, single-Run waiting, typed stream consumption, bounded protocol recovery, streaming file transfer, and output decoding. Enumeration preserves filters, scope, server order, and opaque cursors, and terminates on the protocol's end marker rather than a short or empty page; it does not promise snapshot isolation or eagerly fetch every page. File convenience does not require buffering a complete large body. Cancellation and early termination release owned local work, with caller-owned inputs and resource lifetime made explicit by the language specification.

Application examples can compose those operations into approval flows, client-tool feedback loops, multi-Agent work, and restart recovery. Such examples do not make an entire business flow a new SDK resource or a universal helper whose policies grow to replace application code. Restoring access to existing work uses real resource identities and application-owned checkpoints, not serialization of a live SDK object or reconstruction from a Thread's latest Run.

## Failure and Compatibility

Errors preserve actionable protocol evidence, including available status, Service error code, safe details, request identity, and retry guidance. The language specification owns how exceptions or typed errors represent that evidence. Authentication and credential-bearing diagnostics do not expose secret values.

Safe-read retries, reconnection, mutation replay, and resource reconciliation follow their owning protocol contracts and explicit caller policy. A transport failure after possible dispatch has an uncertain outcome; the SDK does not assert that no resource was created or no side effect occurred. A convenience operation never blindly replays a mutation under a new idempotency key, conflates response loss with rollback, or suppresses a concurrency conflict to continue a business workflow.

The Service API version, streaming subprotocol version, source commit identity, and SDK package versions are distinct. Generated HTTP coverage does not establish non-HTTP recovery support, and accepting new specification text does not prove the corresponding runtime or wire export exists. SDK repositories declare their supported API and runtime scope and verify features against actual protocol evidence. Shared SDK requirements do not authorize a silent breaking change to an existing public language API; each repository owns compatibility review, migration, and release decisions.

## Exported Contract

Service exports one OpenAPI 3.1 document for its public Native HTTP operations to `proto/a13n-service/openapi.json`. Stable operation identities derive from HTTP method and path, not handler names. The export describes actual authentication choices, validation and error envelopes, response media types, binary bodies, and precondition and correlation headers. Exporting the contract does not start Service resources.

Existing Service wire models produce separate JSON Schema exports for notification client frames and Run stream events. These exports do not claim that OpenAPI describes WebSocket or SSE recovery. The owning streaming contract defines delivery, cursor, replay-gap, subscription, heartbeat, and reconciliation semantics. Shared wire fixtures are Service-owned evidence, not an alternative schema or an SDK-specific translation layer.

The exported files and their owning protocol semantics are reviewed together. A change to non-HTTP delivery behavior remains a client-relevant contract change even when the OpenAPI document is unchanged.

## Consumer Independence

The independent repositories are `converge-ai-labs/a13n-sdk-python`, `converge-ai-labs/a13n-sdk-go`, `converge-ai-labs/a13n-sdk-rust`, and `converge-ai-labs/a13n-sdk-typescript`. Each owns its supported API line, concrete public surface and language specification, generator versions and templates, tests, and release decisions. Resource-object convenience and complete generated coverage belong to that same repository rather than a second shared client package. The remote `a13n-service-cli` remains in the Rust SDK repository with independent SDK and CLI release decisions.

SDK repositories consume committed snapshots identified by the complete Service source commit SHA. Contract updates preserve that identity and enter downstream review; a moving branch name is not sufficient provenance. Snapshots include the shared evidence and owning non-HTTP semantics needed to review a protocol change. Updating a snapshot does not authorize an SDK release or imply that all clients already support the changed Service behavior.

Console owns an internal client generated and tested within the main repository. Its build, runtime, and release do not depend on an external SDK package, local SDK checkout, or published shared-client package. The main repository's build, tests, and documentation also work without a local `sdk/` directory.

## Invariants and Acceptance Evidence

The independent SDK repositories own executable evidence for the shared experience, using language-native examples and tests rather than requiring identical source code or API spelling:

01. Resource objects expose existing Service resources and preserve their identities across convenience operations; no SDK business workflow lifecycle is added.
02. Resource references and local property reads perform no hidden I/O, and bound objects obey the parent client's local lifetime without changing durable Service state on close.
03. A queued submission is never represented as an accepted Run; failure or disappearance of the queue entry is not evidence of Run acceptance.
04. Waiting and event observation cannot approve actions, execute client tools, or advance a Thread; explicit feedback returns the actual successor Run.
05. Typed requests preserve omission, null, unions, concurrency preconditions, and idempotency evidence; errors and results retain relevant protocol diagnostics.
06. Pagination, file transfer, waiting, and streams have verifiable cancellation, stopping, and local cleanup behavior.
07. Stream recovery preserves resource-scoped consumption checkpoints, reports gaps, and never substitutes display reconstruction for exact event replay.
08. Exported HTTP operations describe actual Service routes; wire definitions reuse Service models, and non-HTTP changes remain visible to downstream review.
09. SDK snapshots have immutable source provenance, and package compatibility and release decisions remain independent from Service and other SDKs.
10. Main repository validation and Console operation require neither local SDK checkouts nor a published SDK package.
11. SDK coverage distinguishes durable resources, read projections, configuration values, and command receipts; read-only or unsupported concepts do not acquire fabricated mutations or identities.
12. Input and transfer conveniences preserve versioned content semantics, local-versus-remote paths, and separate publication and submission outcomes.
13. Session membership, Thread current Run and selected head, Run lineage, and RunAttempt recovery remain distinct; a history helper does not invent linear ancestry.
14. Environment connection, mount acceptance, and Attempt application are separate; memory reads retain exact storage scope, and configuration drafts require explicit application.
15. Local callbacks, durable Hook subscriptions, raw usage, and telemetry retain separate lifetime, delivery, and authority boundaries.
