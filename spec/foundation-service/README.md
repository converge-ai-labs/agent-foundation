# Foundation Service

## Design Position

This directory defines `foundation-service`, the optional durable Host that embeds `agent-harness`. It is a modular service with independently selectable control and worker process roles, not another Agent loop and not a collection of independently versioned microservices.

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
| [00 Overview](00-overview.md)                                                                 | Service shape, end-to-end flow, subsystem boundaries, dependency direction, and completion boundaries                           |
| [01 Runtime Configuration and Deployment](01-runtime-configuration-and-deployment.md)         | Configuration precedence, deployment profiles, control and worker roles, startup, readiness, supervision, drain, and shutdown   |
| [02 Distribution Composition and Extensions](02-distribution-composition-and-extensions.md)   | OSS, EE, and Cloud composition, dependency direction, contribution conflicts, configuration, and final schema assembly          |
| [03 Storage](03-storage.md)                                                                   | Relational, Redis-compatible, object, and mounted-filesystem capabilities and local/network semantics                           |
| [04 Relational Schema](04-relational-schema.md)                                               | Final distribution metadata, migration authority, compatibility, application, and failure semantics                             |
| [05 HTTP Ingress and Request Contract](05-http-ingress-and-request-contract.md)               | Role surfaces, request context, proxy and browser trust, authentication boundaries, errors, streaming, and drain                |
| [06 Durable Operations and Outbox](06-durable-operations-and-outbox.md)                       | Versioned mutation, idempotency evidence, atomic durable commits, outbox publication, retries, and unknown outcomes             |
| [10 Identity and Access Management](10-identity-and-access-management.md)                     | Organization and Workspace tenancy, User and Service Account identity, credentials, RoleBindings, authorization, and audit      |
| [11 Secret Management](11-secret-management.md)                                               | Managed Secret identity, ownership, metadata-only API, encrypted persistence, mutation, deletion, and disclosure controls       |
| [12 Agent Revisions and Reconstruction](12-agent-revisions-and-reconstruction.md)             | Agent Presets, immutable revisions, model integrations, dependency locks, and trusted process-local reconstruction              |
| [13 Interactions, Executions, and Checkpoints](13-interactions-executions-and-checkpoints.md) | Interaction-to-runtime mapping, Execution state, Attempt fencing, dispatch phase, continuation, and cancellation                |
| [14 Scheduling, Workers, and Recovery](14-scheduling-workers-and-recovery.md)                 | Eligibility, Redis coordination, claims, leases, stale-worker rejection, dispatch uncertainty, replacement, retry, and shutdown |
| [15 Deferred Actions and Children](15-deferred-actions-and-children.md)                       | Approval, client tools, user input, suspension, asynchronous child Executions, delivery, and cancellation                       |
| [16 Environment Management](16-environment-management.md)                                     | Host use of provider specifications, resource state, operations, attachments, reconciliation, and envd boundary                 |
| [17 Events, Usage, and Delivery](17-events-usage-and-delivery.md)                             | Harness observation, AG-UI and Item projection, lifecycle events, delivery, raw usage, large content, and telemetry             |
| [18 Management API](18-management-api.md)                                                     | Public resource routes, interactive and standalone submission, commands, read models, replay, and compatibility                 |

Read `00`, `01`, and `02` before changing process startup, roles, or distribution contents. Read `03`, `04`, and `06` before introducing a durable capability. Read `05`, `10`, and `18` before changing public ingress. Read `13` and `14` together before changing the control and worker boundary. Read the shared interaction model before changing Session, Thread, Turn, or Item semantics. Read the Environment Provider and Agent Stream Protocol catalogs before adding provider or event adapters.

## Implementation Orientation

The current package establishes these service-wide roots:

| Path                                                             | Architectural role                                                                     |
| ---------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `packages/foundation-service/a13n_service/settings.py`           | Maps process environment into typed provider and migration configuration               |
| `packages/foundation-service/a13n_service/app.py`                | Owns FastAPI lifespan, constructs one storage resource set, and exposes readiness      |
| `packages/foundation-service/a13n_service/storage/`              | Generic backend configuration, construction, lifecycle, and capability semantics       |
| `packages/foundation-service/a13n_service/database/metadata.py`  | Explicit registry of all service-owned relational models                               |
| `packages/foundation-service/a13n_service/database/migration.py` | Programmatic Alembic runner and bounded migration coordination                         |
| `packages/foundation-service/a13n_service/database/migrations/`  | Single ordered revision history                                                        |
| `packages/foundation-service/a13n_service/cli.py`                | Stable `foundation-service serve` and `foundation-service db ...` executable interface |

These roots are boundaries, not a requirement that every capability become a subpackage. Small capabilities remain focused modules; a capability gains a subdirectory only when it owns several cohesive implementations or contracts. Runnable configuration and migration usage live in the [Foundation Service package guide](../../packages/foundation-service/README.md).

## Authority Rules

- The control role accepts resources and commands, commits immutable selections, and owns durable lifecycle authority.
- The worker role claims fenced `ExecutionAttempt` leases and invokes Harness in-process. It does not expose another product API or run migrations.
- PostgreSQL is authoritative for accepted lifecycle state and fencing. Real Redis is required for distributed data flow and coordination; each owning domain defines its Redis retention and replay semantics, and Redis delivery alone never proves a relational transition.
- The selected distribution explicitly composes the complete configuration, routers, role components, authorization contributions, metadata, and migration graph; installed packages never change the service implicitly.
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
