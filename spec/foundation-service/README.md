# Foundation Service

## Design Position

This directory defines `foundation-service`, the optional hosted control and execution service that embeds `agent-harness`.

Foundation owns durable managed Secrets, Agent authoring schemas, typed Presets,
immutable definition revisions and dependency locks, process-local
reconstruction adapters, Environment provider registry integration and desired
topology, durable Turn scheduling and state, worker `TurnAttempt` generations,
service APIs, durable events, and usage records.

It does not redefine the code-first Harness `AgentDefinition`, plugin lifecycle, Pydantic Agent loop, Harness result/state semantics, or provider-native Environment state. Platform-owned data and service APIs follow [Platform Data Conventions](../data-conventions.md) and [Platform API Conventions](../api-conventions.md).

## Specification Index

| Document                                                                   | Owns                                                                                                                                                                      |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [Secret Management](01-secret-management.md)                               | Managed Secret identity, ownership, APIs, versioning, encrypted persistence, mutation, and disclosure boundaries                                                          |
| [Foundation Storage Capabilities](02-storage.md)                           | Internal relational, Redis-compatible, object, and mounted-filesystem capability boundaries; local and network backend equivalence                                        |
| [Relational Schema Lifecycle](03-relational-schema.md)                     | Service-wide relational metadata, migration authority, compatibility, application, and failure semantics                                                                  |
| [Durable Turn State](04-turn-persistence.md)                               | Turn identity, scheduling, parent DAG, one conditionally replaced state key, child initialization, checkpoint resume, waiting, outcome sealing, and relational invariants |
| [Durable Turn Attempt Persistence](05-turn-attempt-persistence.md)         | `turn_attempts` schema, worker-generation lifecycle, leases, fences, effect disposition, dispatch, and immutable attempt audit                                            |
| [Lifecycle and Stream Persistence](06-lifecycle-and-stream-persistence.md) | One lifecycle-event table plus Turn Redis Stream and retained Item/replay object shapes                                                                                   |
| [Agent Interaction History Retrieval](07-agent-interaction-retrieval.md)   | Service-owned process-local read Capability, progressive model tools, run authorization, bounded history projection, and failure semantics                                |

Read this overview first. Read the storage contract before adding a persistence,
cache, coordination, object, or shared-filesystem dependency. Read the
relational schema contract before changing a durable relational model. Read the
Turn contract before changing Agent-work acceptance, scheduling, state
initialization, checkpoint resume, continuation, fork, waiting, or outcome
commit. Read the Turn Attempt contract before changing worker generations,
leases, fences, dispatch, or effect disposition. Domain schemas, repositories,
queues, and event models remain in their owning domain specifications rather
than the generic storage substrate.

## Implementation Orientation

The accepted ownership boundary is reflected by two stable internal package roots:

| Path                                                                            | Architectural role                                                                     |
| ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `packages/foundation-service/converge_foundation_service/settings.py`           | Maps the process environment into typed provider and migration configuration           |
| `packages/foundation-service/converge_foundation_service/app.py`                | Owns FastAPI lifespan, constructs one storage resource set, and exposes readiness      |
| `packages/foundation-service/converge_foundation_service/storage/`              | Generic backend configuration, construction, lifecycle, and capability semantics       |
| `packages/foundation-service/converge_foundation_service/database/metadata.py`  | Explicit registry of all service-owned relational models                               |
| `packages/foundation-service/converge_foundation_service/database/migration.py` | Programmatic Alembic runner and bounded migration coordination                         |
| `packages/foundation-service/converge_foundation_service/database/migrations/`  | Single ordered revision history                                                        |
| `packages/foundation-service/converge_foundation_service/cli.py`                | Stable `foundation-service serve` and `foundation-service db ...` executable interface |

These roots are architectural boundaries, not a requirement that every capability become a subpackage. Small capabilities remain focused modules; a capability gains a subdirectory only when it owns several cohesive implementations or contracts. Runnable configuration, migration commands, and complete usage examples live in the [Foundation Service package guide](../../packages/foundation-service/README.md).

## Authority Rules

- The control plane owns source acceptance, typed Presets, model-integration revisions, immutable definition revisions, dependency locks, and durable Turns.
- The Secret management plane accepts opaque values under enum-typed polymorphic owners, persists only AES-256-GCM ciphertext encrypted by one configured master key, and never returns a configured value through its public API.
- Foundation definition records contain only Foundation-owned serializable data. They contain no Python class, plugin instance, Model, Toolset, Capability, callable, client, or credential.
- The worker verifies Host locks and uses trusted installed adapters to reconstruct a process-local Harness `AgentDefinition`.
- A definition-selected interaction-read policy causes trusted Foundation reconstruction to install a Service-owned process-local plugin, Capability, and Toolset. Its tools call a narrow Foundation application reader in process and reauthorize every page; no model selector or persisted state grants history access.
- Every logical run receives fresh `RunBindings`, including an Environment aggregate materialized from current desired topology and an explicit `ModelRunBinding` when hosted model aliases must fail closed rather than delegate to native inference.
- The current worker retains the Environment controller only while its Harness run is entered; dynamic desired acceptance and effective topology publication are separately fenced facts.
- One Foundation `TurnAttempt` starts at most one process-local Harness Run and may contain several Harness `ModelAttempt` values; `ModelAttempt` values are not durable worker generations.
- A stale `TurnAttempt` cannot replace Turn state or commit a lifecycle event, client feedback, child result, usage record, or terminal outcome.
- Native deferred external calls and approvals remain distinct; Foundation freezes the exact pending value in the waiting Turn state and authenticates child-Turn feedback, while the external client owns its side effects.
- Foundation uses one lifecycle-event table. Pending calls, approvals, Items, Redis replay, and generic provider receipts have no separate relational tables.
- Process-local Harness completion, durable Host completion, event projection, usage recording, billing, and payment are independent facts.

## Interaction and Attempt Mapping

The shared [platform interaction model](../interaction-model.md) owns Session, Thread, Turn, and Item meaning. Its four concepts form three containment relationships:

```text
Session -> Thread -> Turn -> Item
```

Foundation maps a durable Turn to process-local Agent work through this chain:

```text
Turn -> TurnAttempt -> Harness Run -> ModelAttempt
```

Every Agent invocation selects or creates a Session and Thread and accepts a
Turn. Webhook, scheduled, and asynchronous-child invocations record their cause
through the Turn's trigger and lineage fields; none can omit the Turn. Worker
recovery creates a new `TurnAttempt` and Harness Run while preserving the Turn
and loading the same deterministic state key. A `turn_attempt_id` or `run_id`
never replaces `thread_id`. Non-Agent reconciliation and maintenance use their
owning domain's work model rather than this chain.

Foundation persists the interactive Agent state on the Turn as defined by
[Durable Turn State](04-turn-persistence.md). The Turn parent edge is the
Git-like semantic history. Each Turn owns one deterministic state key: child
acceptance initializes it from a root or frozen parent state, the current
fenced attempt conditionally replaces it at complete checkpoints, and sealing
freezes it. The Turn also owns scheduling, current-attempt selection, and the
finite recovery budget. `TurnAttempt` fences each worker generation without
owning a competing input, state, or outcome. Its complete relational
schema is defined by
[Durable Turn Attempt Persistence](05-turn-attempt-persistence.md). Lifecycle
facts, Redis observation, and retained Item replay are defined by
[Lifecycle and Stream Persistence](06-lifecycle-and-stream-persistence.md).

## Turn and Attempt Persistence Inventory

The relational table set owned by the Turn, attempt, and lifecycle
persistence contracts is:

| Table              | Owning contract                                                                                    | Purpose                                                                                                  |
| ------------------ | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `turns`            | [Durable Turn State](04-turn-persistence.md)                                                       | Agent-work identity, lineage, scheduling, recovery budget, lifecycle, sealed-state identity, and outcome |
| `turn_audit_usage` | [Durable Turn State](04-turn-persistence.md#turn-audit-and-usage-separation)                       | One Turn-scoped audit, usage, pricing, recovery-usage projection, and settlement aggregate               |
| `turn_attempts`    | [Durable Turn Attempt Persistence](05-turn-attempt-persistence.md#turn_attempts-relational-schema) | Worker generations, leases, fences, Harness Run correlation, usage, failure, and effect disposition      |
| `lifecycle_events` | [Lifecycle and Stream Persistence](06-lifecycle-and-stream-persistence.md#lifecycle-event-model)   | Ordered lifecycle facts and realtime projection bookkeeping                                              |

Pending calls, approvals, Items, Redis stream entries, retained replay, and
tool/provider receipts add no other relational tables.

Secret, definition, provider-specific ledger, and other Foundation domains keep
the tables declared by their own owning contracts; they are not part of this
inventory.

The corresponding object-storage catalog is also closed:

| Object envelope                       | Owning contract                                                                                             | Purpose                                                                  |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `TurnStateEnvelope`                   | [Turn state object](04-turn-persistence.md#turn-state-object)                                               | Complete current Harness and Host state at one deterministic Turn key    |
| `TurnPayloadEnvelope`                 | [Turn payload object](04-turn-persistence.md#turn-payload-object)                                           | Oversized immutable Turn input or output                                 |
| `ProviderContinuationPayloadEnvelope` | [Provider continuation payload](04-turn-persistence.md#provider-continuation-payload-object)                | Object-backed provider continuation payload                              |
| `TurnReplaySnapshot`                  | [Lifecycle and stream persistence](06-lifecycle-and-stream-persistence.md#items-and-retained-replay-object) | Retained stream events and user-visible Items after Redis replay expires |

Artifact and provider-specific object types remain owned by their own domains;
Turn and attempt persistence introduce no additional opaque object bodies.

## Reading Paths

- Read the shared [platform interaction model](../interaction-model.md) before defining Foundation Session, Thread, Turn, or Item mappings.
- Read [Secret Management](01-secret-management.md) for enum-typed ownership, metadata-only APIs, value CAS, relational persistence, configured-key encryption, deletion, and disclosure rules.
- Read [Durable Turn State](04-turn-persistence.md) for Turn identity, DAG lineage, state initialization and replacement, waiting, recovery, and relational invariants.
- Read [Durable Turn Attempt Persistence](05-turn-attempt-persistence.md) for worker-generation identity, lease loss, fencing, dispatch, and immutable attempt audit.
- Read [Lifecycle and Stream Persistence](06-lifecycle-and-stream-persistence.md) for lifecycle facts, Redis Agent messages, and retained Items/replay.
- Read [Agent Interaction History Retrieval](07-agent-interaction-retrieval.md) for the optional Service-owned model Capability, progressive history tools, process-local query interface, and current authorization boundary.
- Read the platform [API conventions](../api-conventions.md) and [data conventions](../data-conventions.md) before defining another Foundation-owned resource or route.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly declared as API or storage formats.
- A definition revision is immutable; changing materialized content or a dependency lock creates another revision.
- `Ref` values identify entities or revisions and grant no authority.
- Process-local Python objects are reconstructed and never become durable payloads.
