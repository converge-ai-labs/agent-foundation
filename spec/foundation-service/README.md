# Foundation Service

## Design Position

This directory defines `foundation-service`, the optional hosted control and execution service that embeds `agent-harness`.

Foundation owns durable managed Secrets, Agent authoring schemas, typed Presets, immutable definition revisions and dependency locks, process-local reconstruction adapters, Environment provider registry integration and desired topology, durable root and asynchronous child Execution lifecycles, worker `ExecutionAttempt` generations, scheduling, continuation selection, client-tool delivery, service APIs, durable events, and usage records.

It does not redefine the code-first Harness `AgentDefinition`, plugin lifecycle, Pydantic Agent loop, Harness result/state semantics, or provider-native Environment state. Platform-owned data and service APIs follow [Platform Data Conventions](../data-conventions.md) and [Platform API Conventions](../api-conventions.md).

## Specification Index

| Document                                               | Owns                                                                                                                               |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| [Foundation Storage Capabilities](01-storage.md)       | Internal relational, Redis-compatible, object, and mounted-filesystem capability boundaries; local and network backend equivalence |
| [Relational Schema Lifecycle](02-relational-schema.md) | Service-wide relational metadata, migration authority, compatibility, application, and failure semantics                           |

Read this overview first. Read the storage contract before adding a persistence, cache, coordination, object, or shared-filesystem dependency to Foundation Service. Read the relational schema contract before adding or changing a durable relational model. Domain schemas, repositories, queues, and event models remain in their owning domain specifications rather than the generic storage substrate.

## Implementation Orientation

The accepted ownership boundary is reflected by two stable internal package roots:

| Package root                                                        | Architectural role                                                               |
| ------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `packages/foundation-service/converge_foundation_service/storage/`  | Generic backend configuration, construction, lifecycle, and capability semantics |
| `packages/foundation-service/converge_foundation_service/database/` | Combined service metadata and the single ordered relational migration history    |

These roots are architectural boundaries, not a requirement that every capability become a subpackage. Small capabilities remain focused modules; a capability gains a subdirectory only when it owns several cohesive implementations or contracts. Runnable configuration, migration commands, and complete usage examples live in the [Foundation Service package guide](../../packages/foundation-service/README.md).

## Authority Rules

- The control plane owns source acceptance, typed Presets, model-integration revisions, immutable definition revisions, dependency locks, and durable Executions.
- The Secret management plane accepts opaque values under enum-typed polymorphic owners, persists only AES-256-GCM ciphertext encrypted by one configured master key, and never returns a configured value through its public API.
- Foundation definition records contain only Foundation-owned serializable data. They contain no Python class, plugin instance, Model, Toolset, Capability, callable, client, or credential.
- The worker verifies Host locks and uses trusted installed adapters to reconstruct a process-local Harness `AgentDefinition`.
- Every logical run receives fresh `RunBindings`, including an Environment aggregate materialized from current desired topology and an explicit `ModelRunBinding` when hosted model aliases must fail closed rather than delegate to native inference.
- The current worker retains the Environment controller only while its Harness run is entered; dynamic desired acceptance and effective topology publication are separately fenced facts.
- One Foundation `ExecutionAttempt` starts one process-local Harness Run and may contain several Harness `ModelAttempt` values; `ModelAttempt` values are not durable worker generations.
- A stale `ExecutionAttempt` cannot commit a checkpoint, lifecycle event, client feedback, child result, usage record, or terminal outcome.
- Native deferred external calls and approvals remain distinct; Foundation owns durable pending state and authenticated feedback, while the external client owns its side effects.
- Process-local Harness completion, durable Host completion, event delivery, external delivery, usage recording, billing, and payment are independent facts.

## Interaction and Execution Mapping

The shared [platform interaction model](../interaction-model.md) owns Session, Thread, Turn, and Item meaning. Foundation maps interactive work without making every durable Execution interactive:

```text
Session -> Thread -> Turn -> Execution -> ExecutionAttempt -> Harness Run -> ModelAttempt
```

An interactive Execution records `session_id`, `thread_id`, and `turn_id`. A standalone webhook or scheduled Execution can omit Session and Turn; it records `thread_id` only when Harness continuation is required. Worker recovery creates a new `ExecutionAttempt` and Harness Run while preserving the selected Thread identity. An Execution ID, `execution_attempt_id`, or `run_id` never replaces `thread_id`.

## Reading Paths

- Read the shared [platform interaction model](../interaction-model.md) before defining Foundation Session, Thread, Turn, or Item mappings.
- Read [Secret Management](01-secret-management.md) for enum-typed ownership, metadata-only APIs, value CAS, relational persistence, configured-key encryption, deletion, and disclosure rules.
- Read the platform [API conventions](../api-conventions.md) and [data conventions](../data-conventions.md) before defining another Foundation-owned resource or route.

## Specification Catalog

| Document                                     | Owns                                                                                                                                                     |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Secret Management](01-secret-management.md) | Managed Secret identity, polymorphic ownership, API, versioning, durable storage, encryption, mutation flows, failure semantics, and disclosure controls |

## Specification Conventions

- Python-like schemas are conceptual unless explicitly declared as API or storage formats.
- A definition revision is immutable; changing materialized content or a dependency lock creates another revision.
- `Ref` values identify entities or revisions and grant no authority.
- Process-local Python objects are reconstructed and never become durable payloads.
