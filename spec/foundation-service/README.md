# Foundation Service

## Design Position

This directory defines `foundation-service`, the optional durable Host that embeds `agent-harness`. It is a modular service with independently selectable control and execution process roles, not another Agent loop and not a collection of independently versioned microservices.

Foundation owns managed Secrets, resource authorization, serializable Agent authoring resources, immutable revisions and dependency locks, durable interaction records, Executions and ExecutionAttempts, scheduling, pending actions, Environment management, lifecycle events, raw usage records, and the public management API.

It does not redefine the code-first Harness `AgentDefinition`, Pydantic Agent loop, Harness result and state semantics, Agent Stream Protocol conversion, Environment provider lifecycle types, EIP, or provider-native state. Platform-owned data and APIs follow [Platform Data Conventions](../data-conventions.md) and [Platform API Conventions](../api-conventions.md).

## Accepted Identity Model

The shared [Platform Interaction Model](../interaction-model.md) owns the public concepts `Session`, `Thread`, `Turn`, and `Item`. Foundation additionally owns durable scheduling identities:

```text
Session -> Thread -> Turn -> Execution -> ExecutionAttempt -> Harness Run -> ModelAttempt
```

An interactive Execution records `session_id`, `thread_id`, and `turn_id`. A standalone webhook, schedule, or service request can create an Execution without a Session or Turn and records `thread_id` only when it continues a Harness history.

One Turn can span several process-local Harness Runs when approval, deferred input, or worker recovery creates a boundary. One `ExecutionAttempt` starts at most one Harness Run; internal Harness `ModelAttempt` values are not durable worker generations. None of `execution_id`, `execution_attempt_id`, or `run_id` replaces `thread_id`.

## Specification Catalog

| Document                                                                                      | Owning contract                                                                                                                 |
| --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| [00 Overview](00-overview.md)                                                                 | Service shape, process roles, end-to-end flow, subsystem boundaries, dependency direction, and completion boundaries            |
| [01 Secret Management](01-secret-management.md)                                               | Managed Secret identity, ownership, metadata-only API, encrypted persistence, mutation, deletion, and disclosure controls       |
| [02 Storage](02-storage.md)                                                                   | Relational, Redis-compatible, object, and mounted-filesystem capabilities and deployment-profile equivalence                    |
| [03 Relational Schema](03-relational-schema.md)                                               | Service-wide relational metadata, migration authority, compatibility, application, and failure semantics                        |
| [04 Identity and Access Management](04-identity-and-access-management.md)                     | Organization and Workspace tenancy, User and Service Account identity, credentials, RoleBindings, authorization, and audit      |
| [05 Agent Revisions and Reconstruction](05-agent-revisions-and-reconstruction.md)             | Agent Presets, immutable revisions, model integrations, dependency locks, and trusted process-local reconstruction              |
| [06 Interactions, Executions, and Checkpoints](06-interactions-executions-and-checkpoints.md) | Interaction-to-runtime mapping, Execution state, Attempt fencing, dispatch phase, continuation, idempotency, and cancellation   |
| [07 Scheduling, Workers, and Recovery](07-scheduling-workers-and-recovery.md)                 | Eligibility, queues, claims, leases, stale-worker rejection, dispatch uncertainty, replacement, retry, and shutdown             |
| [08 Deferred Actions and Children](08-deferred-actions-and-children.md)                       | Approval, client tools, user input, suspension, asynchronous child Executions, delivery, and cancellation                       |
| [09 Environment Management](09-environment-management.md)                                     | Host use of canonical provider specifications, resource state, operations, attachments, reconciliation, and envd boundary       |
| [10 Events, Usage, and Delivery](10-events-usage-and-delivery.md)                             | Harness observation, AG-UI and Item projection, durable lifecycle events, outbox, usage ingestion, large content, and telemetry |
| [11 Management API](11-management-api.md)                                                     | Public resource routes, interactive and standalone submission, commands, read models, replay, concurrency, and compatibility    |

Read `00`, `06`, and `07` together before changing the control/execution boundary. Read the shared interaction model before changing Session, Thread, Turn, or Item semantics. Read the Environment Provider and Agent Stream Protocol catalogs before adding provider or event adapters.

## Implementation Orientation

The current package establishes these service-wide roots:

| Path                                                                            | Architectural role                                                                     |
| ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `packages/foundation-service/converge_foundation_service/settings.py`           | Maps process environment into typed provider and migration configuration               |
| `packages/foundation-service/converge_foundation_service/app.py`                | Owns FastAPI lifespan, constructs one storage resource set, and exposes readiness      |
| `packages/foundation-service/converge_foundation_service/storage/`              | Generic backend configuration, construction, lifecycle, and capability semantics       |
| `packages/foundation-service/converge_foundation_service/database/metadata.py`  | Explicit registry of all service-owned relational models                               |
| `packages/foundation-service/converge_foundation_service/database/migration.py` | Programmatic Alembic runner and bounded migration coordination                         |
| `packages/foundation-service/converge_foundation_service/database/migrations/`  | Single ordered revision history                                                        |
| `packages/foundation-service/converge_foundation_service/cli.py`                | Stable `foundation-service serve` and `foundation-service db ...` executable interface |

These roots are boundaries, not a requirement that every capability become a subpackage. Small capabilities remain focused modules; a capability gains a subdirectory only when it owns several cohesive implementations or contracts. Runnable configuration and migration usage live in the [Foundation Service package guide](../../packages/foundation-service/README.md).

## Authority Rules

- The control role accepts resources and commands, commits immutable selections, and owns durable lifecycle authority.
- The execution role claims fenced `ExecutionAttempt` leases and invokes Harness in-process. It does not expose another product API or run migrations.
- PostgreSQL is authoritative for lifecycle and fencing. Redis, queues, and notifications are disposable coordination hints.
- Foundation records contain only Foundation-owned serializable data. They contain no Python class, plugin instance, native Model, Toolset, Capability, callable, client, credential, provider attachment, or live controller.
- The worker verifies exact locks and uses trusted installed adapters to reconstruct a process-local Harness `AgentDefinition` and fresh `RunBindings`.
- Foundation consumes the canonical Environment Provider types and `HarnessAguiObserver`; it does not create parallel provider or Harness-event models.
- A stale Attempt cannot mutate Execution lifecycle, checkpoints, pending work, Items, child delivery, or terminal outcome. A late immutable `UsageRecord` can still be ingested under its original Attempt when record identity and content validate, but it cannot mutate lifecycle.
- Before any external effect, the worker durably advances from `pre_dispatch` to `effects_possible`. Recovery never treats missing acknowledgement as proof that no effect occurred.
- Harness completion, durable Execution completion, Item projection, event delivery, external delivery, usage ingestion, and any external settlement are separate facts.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly declared as API or storage formats.
- A definition revision is immutable; changing materialized content or a dependency lock creates another revision.
- `Ref` values identify entities or revisions and grant no authority.
- Process-local objects are reconstructed and never become durable payloads.
- Domain schemas, repositories, queue messages, and events live in their owning domain rather than the generic storage substrate.
