# Foundation Service Specifications

## Overview

This directory defines `foundation-service`, the optional durable Host that embeds `agent-harness`. Foundation Service is one modular service, product authorization boundary, schema, and container image whose `all`, `control`, and `execution` roles divide process ownership and scaling without creating separate products or lifecycle authorities.

Foundation owns product resource scope and authorization, durable Agent authoring and immutable executable revisions, Threads, Turns, Items, scheduling, worker lease generations, checkpoint selection, deferred feedback, asynchronous child Threads, Environment provider orchestration, service APIs, durable events, and usage records. It reconstructs process-local Harness objects from exact trusted inputs and supplies fresh authority for every logical run.

Foundation does not redefine the code-first Harness `AgentDefinition`, plugin lifecycle, Pydantic Agent loop, `HarnessState`, Harness result semantics, Agent Stream Protocol, Environment Interaction Protocol, provider-native state, or external side effects. Platform-owned data and service APIs follow [Platform Data Conventions](../data-conventions.md) and [Platform API Conventions](../api-conventions.md).

## Document Catalog

| Document                                                                               | Owning contract                                                                                                        |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| [00-overview.md](00-overview.md)                                                       | Service architecture, roles, dependency direction, major components, and completion boundaries                         |
| [01-resource-scope-and-authorization.md](01-resource-scope-and-authorization.md)       | Organization and Workspace scope, principals, credentials, fixed roles, authorization, and run grants                  |
| [02-agent-revisions-and-reconstruction.md](02-agent-revisions-and-reconstruction.md)   | Agent authoring, Presets, immutable Agent and model-integration revisions, dependency locks, and worker reconstruction |
| [03-threads-turns-items-and-checkpoints.md](03-threads-turns-items-and-checkpoints.md) | Threads, Turns, Items, internal worker leases, checkpoints, cancellation, and continuation selection                   |
| [04-scheduling-workers-and-recovery.md](04-scheduling-workers-and-recovery.md)         | Scheduling, leases, fencing, worker ownership, retries, reconciliation, and process roles                              |
| [05-deferred-actions-and-children.md](05-deferred-actions-and-children.md)             | Durable approvals, external client tools, authenticated feedback, asynchronous child Threads, and result delivery      |
| [06-environment-provider-lifecycle.md](06-environment-provider-lifecycle.md)           | Environment provider registry, desired topology, launch envelopes, effective bindings, and envd boundary               |
| [07-events-usage-and-delivery.md](07-events-usage-and-delivery.md)                     | Durable lifecycle events, outbox publication, stream replay, usage records, artifacts, and observability projections   |

## Reading Paths

### Understand Foundation Service

Read `00`, then `03` and `04` for the durable Turn lifecycle. Read the [Harness Hosting Contract](../agent-harness/13-hosting-contract.md) for the process-local boundary embedded by an execution worker.

### Implement product resources or authorization

Read `01` and `02`, then [Platform Data Conventions](../data-conventions.md) and [Platform API Conventions](../api-conventions.md). Every product operation authenticates a principal and authorizes an action against an explicit Organization or Workspace resource scope.

### Implement Turn processing or recovery

Read `02` through `05`, then [Harness Run Context and Lifecycle](../agent-harness/06-execution-context-and-lifecycle.md), [Snapshot and Resume](../agent-harness/10-snapshot-and-resume.md), and [Harness Hosting Contract](../agent-harness/13-hosting-contract.md).

### Integrate Environment providers

Read `06`, [Harness Environment Integration](../agent-harness/08-environment-integration.md), and the [agent-envd catalog](../agent-envd/README.md). Foundation owns provisioning and durable launch state; the Harness owns the entered run resource; envd owns EIP operations and daemon-generation evidence.

### Implement event, usage, or client delivery

Read `03`, `04`, `05`, and `07`. Durable lifecycle commitment, event publication, external result delivery, usage recording, pricing, billing, and payment remain independent facts.

## Authority Rules

- Foundation product resources have an explicit Organization or Workspace scope; identifiers and references grant no authority.
- The control plane owns authenticated Turn acceptance, immutable version selection, Thread/Turn/Item APIs, scheduling intent, feedback acceptance, and public APIs.
- An execution worker owns one worker lease generation at a time, verifies exact locks, reconstructs process-local Harness values, supplies fresh `RunBindings`, and publishes only generation-fenced candidates.
- One worker lease generation starts at most one logical Harness run. Internal Harness model attempts do not change the worker lease generation.
- PostgreSQL is the distributed durable lifecycle authority. Redis, queues, streams, and notifications coordinate work but do not define whether work exists or completed.
- A stale worker cannot commit an Item, checkpoint, lifecycle event, pending action, child result, usage record, or terminal outcome.
- Native deferred external calls and approvals remain distinct. Foundation owns durable pending state and authenticated feedback; the external client owns its effects.
- `HarnessState` restores portable process-local continuation data only. Foundation separately stores durable lifecycle, exact revisions, policy decisions, provider launch state, pending actions, child delivery, and reconciliation evidence.
- Product authorization stays in Foundation Service. The Harness receives fresh run authority and envd enforces EIP grants without querying product membership.
- Process-local Harness completion, durable completion, event delivery, external delivery, usage recording, billing, and payment are independent facts.

## Specification Conventions

- Python-like schemas are conceptual unless explicitly declared as API or storage formats.
- `Thread` means one durable, independently advancing, multi-turn Agent conversation. It is not a login session, EIP session, provider session, or transport connection.
- `Turn` means one user request and all Agent work caused by that request.
- `Item` means one ordered durable semantic unit within a Turn, not every transient runtime event.
- A worker lease generation is an internal fencing value, not a public resource or another level in the interaction model.
- A `Checkpoint` is an immutable complete continuation candidate selected by a fenced Foundation transaction.
- `Ref` values identify entities or revisions and grant no authority.
- A definition or integration revision is immutable. Changing its materialized content or dependency lock creates another revision.
- Process-local Python objects are reconstructed and never become durable payloads.
