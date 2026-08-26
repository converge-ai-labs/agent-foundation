# Foundation Management API

## Design Position

Foundation exposes one resource-oriented `/api/v1` management contract for hosted identity scope, Agent authoring, interaction, durable execution, deferred work, Environment resources, events, usage, and artifacts. The API follows [Platform API Conventions](../api-conventions.md) and [Platform Data Conventions](../data-conventions.md); this document owns Foundation resource routes, command boundaries, read models, and cross-resource mutation behavior.

The API is the public boundary consumed by Foundation SDKs and the remote `agent-foundation` CLI. SDKs map this contract and do not invent another lifecycle, retry policy, or HTTP client semantics. Harness Python APIs, Agent Stream Protocol, EIP, provider APIs, and external webhook payloads retain their own contracts.

## Scope and Authorization

Every route authenticates one Principal and authorizes an explicit action against the selected Organization, Workspace, or resource. Identifiers, parent paths, cursors, Item references, Attempt IDs, artifact URLs, and idempotency keys grant no authority.

Workspace collections return only resources visible under current policy. A concealed resource can return `404`. Mutation authorization is re-evaluated at acceptance even when a caller can read the current resource. Execution workers use internal application capabilities rather than calling public HTTP routes to mutate lifecycle state.

## Resource Route Catalog

The following paths are relative to `/api/v1` and are the owning collection and command surfaces. Child reads can also return canonical links to their top-level resource representation; aliases do not create another identity.

| Resource                        | Core routes                                                                                  | Notes                                                              |
| ------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Organizations                   | `/organizations`, `/organizations/{organization_id}`                                         | Organization creation, read, update, and lifecycle                 |
| Organization memberships        | `/organizations/{organization_id}/memberships`                                               | Fixed-role membership management                                   |
| Workspaces                      | `/workspaces`, `/workspaces/{workspace_id}`                                                  | Organization-scoped collaboration and resource-isolation boundary  |
| Workspace memberships           | `/workspaces/{workspace_id}/memberships`                                                     | Workspace role bindings                                            |
| Principals and service accounts | `/organizations/{organization_id}/principals`, `/workspaces/{workspace_id}/service-accounts` | Identity metadata and lifecycle; credentials use separate commands |
| Presets and Agents              | `/workspaces/{workspace_id}/presets`, `/workspaces/{workspace_id}/agents`                    | Mutable authoring heads and immutable selected revisions           |
| Agent revisions                 | `/agents/{agent_id}/revisions`                                                               | Immutable create/read collection; no in-place revision mutation    |
| Model integrations              | `/workspaces/{workspace_id}/model-integrations`                                              | Mutable heads with immutable integration revisions                 |
| Sessions                        | `/workspaces/{workspace_id}/sessions`                                                        | Hosted interaction tree and product/presentation scope             |
| Threads                         | `/sessions/{session_id}/threads`                                                             | Independently advancing histories within one Session               |
| Turns                           | `/threads/{thread_id}/turns`                                                                 | Host-accepted advancement and Item scope                           |
| Items                           | `/turns/{turn_id}/items`                                                                     | Ordered user-visible semantic records                              |
| Executions                      | `/workspaces/{workspace_id}/executions`, `/executions/{execution_id}`                        | Interactive and standalone durable work                            |
| ExecutionAttempts               | `/executions/{execution_id}/attempts`                                                        | Read-only operational history; workers mutate internally           |
| Pending actions                 | `/executions/{execution_id}/pending-actions`                                                 | Read and authorized response commands                              |
| Environment resources           | `/workspaces/{workspace_id}/environments`                                                    | Desired provider spec, lifecycle, and safe state metadata          |
| Secrets                         | Routes owned by [Secret Management](01-secret-management.md)                                 | Metadata-only reads and write-only value mutations                 |
| Lifecycle events                | `/workspaces/{workspace_id}/events` and resource-scoped event collections                    | Durable replay, not ordinary pagination                            |
| Usage                           | `/workspaces/{workspace_id}/usage-records` and aggregate reads                               | Immutable records and explicitly derived views                     |
| Artifacts                       | `/workspaces/{workspace_id}/artifacts`                                                       | Metadata, authorization, retention, and signed delivery commands   |

Collection fields, filters, order, and payload limits are defined by the owning resource document. All ordinary collections use the shared cursor shape. Lifecycle replay uses its own monotonic cursor and explicit retention-gap response.

## Interactive Submission

```http
POST /api/v1/threads/{thread_id}/turns
Idempotency-Key: opaque-caller-key
```

The request carries `expected_thread_version`, bounded input, selected Agent authoring reference when permitted, and optional policy-supported metadata. Acceptance atomically creates the Turn, first user Item, associated Execution, lifecycle events, and outbox intents.

The `202` response is an acceptance receipt containing the Turn and Execution references plus current versions. It does not wait for a Worker or Harness result. Repeating the same key and canonical request returns the original receipt; different content conflicts.

## Standalone Execution Submission

```http
POST /api/v1/workspaces/{workspace_id}/executions
Idempotency-Key: opaque-caller-key
```

Standalone submission selects an immutable Agent revision, bounded input, optional authoritative source checkpoint, and declared trigger metadata. It omits Session and Turn references. The response returns the durable Execution acceptance receipt.

An API never creates a hidden Session or Turn merely to reuse interactive storage. If the execution later needs visible independently advancing interaction history, an explicit Host operation creates or associates the required Session and Thread under its owning policy.

## Commands

Commands are subordinate to the resource whose state they mutate:

| Command                          | Route                                                | Required mutation contract                                                     |
| -------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------ |
| Cancel Execution                 | `POST /executions/{execution_id}/cancel`             | Idempotency key and current authorization                                      |
| Retry terminal Execution         | `POST /executions/{execution_id}/retry`              | Creates a successor Execution; interactive retry also creates a successor Turn |
| Approve pending action           | `POST /pending-actions/{pending_action_id}/approve`  | Expected pending version and idempotency key                                   |
| Reject pending action            | `POST /pending-actions/{pending_action_id}/reject`   | Expected pending version and idempotency key                                   |
| Submit client-tool result        | `POST /pending-actions/{pending_action_id}/complete` | Exact native result envelope and idempotency key                               |
| Supply structured user input     | `POST /pending-actions/{pending_action_id}/respond`  | Schema-valid bounded response and idempotency key                              |
| Resume/pause/destroy Environment | `POST /environments/{environment_id}/{action}`       | Expected Environment version and idempotency key                               |
| Reconcile Environment operation  | `POST /environments/{environment_id}/reconcile`      | Targets the exact unresolved operation identity                                |
| Create artifact upload           | `POST /artifacts/uploads`                            | Bounded upload intent; completion is a separate command                        |

A command returns the mutated resource or a durable receipt. `202` means accepted, not completed. Unknown outcome after possible dispatch is reconciled by repeating the same idempotency key or reading the returned resource; clients never generate a new key merely because acknowledgement was lost.

## Read Models

Public resources expose stable product fields and safe references, not ORM objects or provider-private state. Read models follow these boundaries:

- Session, Thread, Turn, and Item use the shared interaction meanings;
- Execution exposes lifecycle, wait reason, selected revisions, interaction correlation, parent/retry references, cancellation intent, and timestamps;
- ExecutionAttempt exposes generation, worker-safe status, dispatch phase, lease timing, Harness correlation, and bounded failure evidence, but no credential or process-private value;
- Environment exposes desired spec revision, desired phase, safe lifecycle status, current operation, and effective observations, but never provider resource-state ciphertext or attachment material;
- UsageRecord reads preserve immutable identity and attribution, while aggregate endpoints label derivation window and pricing revision;
- Artifact reads expose authorized metadata and issue short-lived delivery URLs through an explicit command.

An Item read never substitutes for lifecycle event replay, and an event read never expands private Item or artifact content without separate authorization.

## Pagination, Filtering, and Replay

Ordinary collections use `limit` and opaque `cursor` exactly as defined by Platform API Conventions. Each resource defines deterministic default ordering and explicit filters. Cursors are bound to principal scope, filter, order, and retention.

Lifecycle and interaction replay use opaque replay cursors scoped to Session, Thread, Turn, Execution, or Workspace delivery views. A replay response labels each envelope source kind and reports `replay_gap` when retained data no longer covers the requested cursor. The client then reads current resource state and an authorized semantic snapshot; it never treats the newest event as a complete missing history.

SSE and WebSocket endpoints use the same envelope and cursor semantics. Authentication and initial database reads finish before stream construction. Transport disconnect does not cancel work.

## Concurrency and Idempotency

Mutable resources expose one monotonically increasing `version`. `PATCH` and state-sensitive commands use `expected_version`; mismatch returns `409`. Immutable revisions and usage records reject mutation rather than carrying artificial versions.

Creates and commands that can be retried accept `Idempotency-Key`. Evidence is scoped to principal, resource, operation kind, and canonical request for a finite documented lifetime. Idempotent replay resolves before current-version comparison. Expired evidence and absent receipts do not prove that an earlier operation was never dispatched.

## Errors and Compatibility

The API uses shared bounded errors and stable codes. Owning domains add safe details such as `current_version`, `wait_reason`, `pending_kind`, `dispatch_phase`, or `replay_gap`; they never expose traceback, SQL, provider payload, Secret value, credential, attachment, private path, or raw model/tool content.

`/api/v1` evolves additively. Removing or repurposing a field, changing a command side-effect boundary, weakening authorization, changing idempotency scope, or changing resource identity requires an incompatible API version. First-party SDK releases can add idiomatic convenience methods but preserve the same resources, receipts, errors, and retry boundaries.

## Invariants

1. Every public Foundation operation authenticates a Principal and authorizes an explicit resource action.
2. Interactive Turn submission and standalone Execution submission are distinct accepted operations.
3. Public API acceptance never waits for Harness completion.
4. ExecutionAttempt mutation is internal; public Attempt routes are bounded operational reads.
5. Commands use resource-scoped paths, idempotency evidence, and version checks where lost updates are possible.
6. Replay cursors, identifiers, receipts, and signed URLs grant no authority by possession.
7. API read models contain no process-local object, provider resource-state data, attachment, credential, or Secret value.
8. SDKs and the CLI consume this API rather than defining parallel lifecycle or retry semantics.
