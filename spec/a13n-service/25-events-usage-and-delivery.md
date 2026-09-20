# Events, Interaction Projection, Usage, and Delivery

## Design Position

Service owns durable lifecycle publication, continuous retained interaction projection, raw usage ingestion, large-content selection, and external delivery without turning transport or telemetry into Run authority. [Lifecycle and Stream Persistence](24-lifecycle-and-stream-persistence.md) owns the lifecycle-event schema, the stable Run-scoped Redis Stream, Redis replay cursors, retained Items, and the incrementally persisted `RunDisplaySnapshot`. This document owns usage attribution, external destination delivery, large content, and the authority boundary between those records and telemetry. [Observability](38-observability.md) owns trace topology, content and scope policy, OTLP export, backend ownership, and hot-backend mapping.

[Service Hook Notifications](26-hook-notifications.md) owns the public Hook-name registry, durable subscription matching, external channel eligibility, and Webhook flow. Hook routing reuses the records and delivery envelope defined here rather than creating another event log or transport authority. Native Run SSE, lifecycle reads, and best-effort notifications remain owned by [Native Streaming and Notifications](21-native-streaming-and-notifications.md).

Harness observations follow the accepted Agent Stream Protocol path. Service consumes `HarnessAguiObserver` output and does not implement another Harness-to-AG-UI mapping. The separate [Trace Query](39-trace-query.md) contract reads authorized backend telemetry without turning it into a retained interaction or usage source. A live message or delivered envelope becomes authoritative only through the owning Run, lifecycle-event, retained-Item, or usage commit.

## Record Layers

| Layer                | Meaning                                                         | Authority                                           |
| -------------------- | --------------------------------------------------------------- | --------------------------------------------------- |
| Harness source event | Process-local public observation from one Harness Run           | Harness observation only                            |
| AG-UI event          | `HarnessAguiObserver` conversion after optional Host processing | Presentation observation only                       |
| Run Stream entry     | Bounded Run-scoped live or lifecycle projection in Redis        | Transport and bounded replay only                   |
| Item                 | User-visible semantic unit within a Run                         | Retained projection in `RunDisplaySnapshot`         |
| Lifecycle event      | Fact that a Service resource transition committed               | Relational audit and publication fact               |
| Native notification  | Lightweight subscribed resource wake-up                         | Best-effort delivery only                           |
| UsageRecord          | Immutable incurred-usage fact                                   | Durable usage attribution after validated ingestion |
| Destination record   | Delivery progress for one authorized Webhook destination        | Delivery only; never source lifecycle authority     |

Messages, permitted reasoning units, tool calls, commands, file changes, approvals, child activity, plans, errors, and bounded content references can become retained Items. Heartbeats, credentials, private state, arbitrary logs, and unbounded payloads do not automatically become Items.

## Harness Observation and Run Stream

```mermaid
flowchart LR
    subgraph Worker[Worker role]
        subgraph Executor[RunAttemptExecutor root task]
            Harness[HarnessRunStream]
            Driver[HarnessDriver]
            Observer[HarnessAguiObserver]
            Processor[Service visibility processor]
            Publisher[Bounded Run Stream publisher]
            Harness --> Driver --> Observer --> Processor --> Publisher
        end
        Display[Continuous display projector]
    end

    Redis[Run-scoped Redis Stream]
    Snapshot[Display snapshot and committed cursor]

    subgraph Control[Control role]
        Subscriber[Authorized source reader]
        Native[Native SSE or notification projection]
        Agui[Hosted AG-UI projection]
        A2A[A2A Task and Artifact projection]
        Subscriber --> Native & Agui & A2A
    end

    Publisher --> Redis --> Subscriber
    Redis --> Display --> Snapshot
    Snapshot --> Subscriber
    Snapshot -. confirmed trim boundary .-> Redis
```

One observer belongs to one Harness Run. The current executor root runs one `HarnessDriver`, which is the sole Harness-stream consumer and drives that observer; `LeaseMonitor` and `ControlWatcher` never consume Harness events. The Service visibility processor applies authorization, redaction, and stable Item projection policy without changing upstream event meaning. Credentials, private state, arbitrary logs, and unbounded content never enter the Run Stream.

Inline-child presentation IDs are scoped to their Harness Run before joining the parent Run Stream. Tool-call, message, parent-message, and retained Item correlations use that same scope, so repeated model tool-call IDs cannot merge parent or sibling content. Root tool-call IDs retain their native values for deferred-feedback correlation.

Before publishing its first Harness observation, the executor durably binds the immutable Harness Run identity to the current RunAttempt. It appends bounded messages under the [Run Stream publication contract](24-lifecycle-and-stream-persistence.md#publication-activation-and-fencing). Replacement RunAttempts create fresh Harness Runs but continue the same Run Stream; each event preserves its source Attempt and Harness Run identities when present.

Expected planned handoff is a Service Attempt transition, not a Harness Run outcome. Closing the old process-local stream for `run_attempt.yielded` emits no AG-UI `RUN_FINISHED`, `RUN_ERROR`, or synthetic cancelled result, does not close the Run Stream, and does not repeat `run.running`. The successor's fresh Harness Run continues observations in the same Run Stream under new Attempt and Run provenance.

Redis Stream entry IDs are bounded live replay cursors, not product authority. Stream possession and cursor knowledge grant no access. Control authenticates and authorizes the caller against current Service state before reading or subscribing, and it releases all database sessions before streaming.

The [Protocol Gateway](15-protocol-gateway.md) owns each public wire projection. Native Run SSE preserves the Run Stream cursor; Hosted AG-UI assigns its own retained delivery cursor; A2A exposes current Task state rather than a Native cursor. The best-effort Native notification WebSocket carries only wake-up metadata and has no retained delivery source. None of these projections changes the source event or Item.

Bounded queues and explicit overflow handling prevent a slow client from blocking Harness work. When the retained Redis prefix is unavailable, control returns the explicit replay-gap semantics defined by the stream owner. A Worker display projector continuously merges events and atomically persists Items, resumable merge state, and its cursor. Redis may trim only durably covered events; storage stalls apply bounded persistence backpressure independently of slow clients. Item reads use the verified display snapshot during execution and after finalization. Safely trimmed raw history can cause an exact-replay gap while complete display history remains available. Missing, incomplete, or oversized projection is explicit and is never reconstructed from relational rows, object listings, telemetry, or Harness state.

## Lifecycle Publication and External Destinations

Every authoritative Run and RunAttempt transition writes the bounded typed lifecycle event required by [Lifecycle and Stream Persistence](24-lifecycle-and-stream-persistence.md). Waiting pending data remains owned by its sealed Run and state, while child relationships and Ingress, Route, ConnectorProvider, Connection, or Connection operations retain their owning domain records and outbox intents without extending the lifecycle entity registry implicitly. Event publication follows the atomicity, retry, and duplicate-delivery rules in [Durable Operations and Outbox](06-durable-operations-and-outbox.md).

Each lifecycle resource has one contiguous `resource_seq`; the Workspace feed has a separate database-assigned cursor that is monotonic but not a causal order. Duplicate publication preserves one event identity. Event content references owning resources and retained Items rather than copying differently retained payloads. Redis presence, subscriber receipt, and telemetry never manufacture a lifecycle fact.

External Webhook delivery specializes the shared Outbox. Background [Hook dispatch](26-hook-notifications.md#asynchronous-hook-dispatch) matches current subscriptions and atomically creates one Outbox row per selected HookSubscription Revision with dispatch completion. Each delivery completes independently from source commitment and from other subscriptions. A bounded destination policy can exhaust retries and dead-letter its own row without changing the source lifecycle event, Run Stream, retained snapshot, or Run outcome.

Durable Hook subscription delivery is limited to committed lifecycle events. Live-only Run Stream entries and Items use authorized Run SSE and never create Outbox rows merely because a subscriber selected their Hook names. Native notification WebSocket frames remain coarse, best-effort wake-ups rather than Hook payload delivery.

The delivery envelope is conceptually:

```python
class DeliveryEnvelope:
    delivery_id: DeliveryId
    hook_subscription_id: HookSubscriptionId
    hook_name: str
    hook_schema_version: str
    source_kind: Literal["lifecycle_event"]
    source_id: str
    workspace_id: WorkspaceId
    resource_type: Literal["run", "run_attempt"]
    resource_id: str
    resource_seq: int
    resource_version: int
    session_id: SessionId | None
    thread_id: ThreadId | None
    run_id: RunId | None
    run_attempt_id: RunAttemptId | None
    harness_run_id: HarnessRunId | None
    occurred_at: datetime
    payload: BoundedSafePayload
```

The schema is conceptual. `hook_name` is one exact name from the Hook registry, and `hook_schema_version` versions that Hook payload independently from the source storage and Native transport schemas. A lifecycle event uses its stable relational identity. `resource_type`, `resource_id`, `resource_seq`, and `resource_version` are the delivery names for the source lifecycle event's `entity_type`, `entity_id`, `resource_seq`, and `entity_version`. Optional correlation is absent rather than inferred when the source does not own it. Run SSE and Native notification frames never use this envelope.

`resource_seq` is contiguous only within one `(workspace_id, resource_type, resource_id)` lifecycle stream and is the value used to identify stale, duplicate, and missing resource events. `resource_version` identifies the source resource's version produced by the mutation; it can skip and is not used for gap detection.

Re-delivery of the same source to the same subscription preserves `delivery_id`. Webhook delivery is not ordered, including between consecutive events for the same resource and subscription. Parallel publishers, retry backoff, response loss, and redrive can all deliver a later `resource_seq` before an earlier one. Source commitment, Run Stream append, retained-snapshot publication, destination acknowledgement, and client receipt are separate facts.

Streaming routes follow the [HTTP streaming contract](05-http-ingress-and-request-contract.md#streaming-connections). Disconnect never cancels or seals a Run.

The old combined Workspace SSE/WebSocket delivery surface does not exist. Durable Workspace lifecycle reads, detailed Run SSE, and best-effort Native notifications use the distinct contracts in [Native Streaming and Notifications](21-native-streaming-and-notifications.md).

## Durable Usage Ingestion

The Harness owns native `RunUsage`, the run-local attribution ledger, immutable `UsageRecord` values, and bounded `usage_report` delivery. Service ingests immutable `UsageRecord` values idempotently by `record_id` and adds durable attribution:

Public Workspace UsageRecord List authorizes `usage.read` from the IAM [stable action registry](33-identity-and-access-management.md#stable-action-registry). Internal ingestion is fenced RunAttempt authority and does not require or grant that public read action.

- Organization, Workspace, Session, Thread, and Run;
- originating RunAttempt and Harness Run;
- stable Agent, exact AgentRevision where required (null for protected configuration-assistant execution), and effective-config digest; and
- accepted `model_id`, provider type, and model name from the RunAttempt observation, plus model/provider identity and measures from the record.

A `usage_report` ID is a delivery identity, not another usage fact. Reports can overlap through retries or chunk delivery. `HarnessRunResult.usage_records` is a complete detached run-local snapshot and can overlap records already delivered incrementally. Service deduplicates all paths by immutable `record_id` and rejects conflicting content for the same identity.

Service persists each receipt in `run_usage_records`, attributed to its original RunAttempt. The incremental Harness report and terminal result both use that ingestion path. For a current running Attempt, newly inserted model receipts add their input and output tokens to its operational `RunUsage`; model-request admission already owns the request counter, so ingesting a receipt does not increment it again. A token usage limit cannot discard already incurred usage: the receipt commits even if its tokens exceed the limit, and subsequent request admission enforces the exhausted budget. Provider receipts retain their native measures without inventing a conversion into model tokens.

Terminal `RunUsage` is an aggregate process-local snapshot used for operational limits and summary display. Service does not sum it with UsageRecords, inline-child snapshots, or later RunAttempt snapshots. Durable attribution operates from immutable records. Harness model records already carry the applied build-time pricing revision, rule, status, and cost source; Service may add negotiated or settlement projections without changing record identity or content.

### Late Usage from a Terminal or Stale RunAttempt

RunAttempt fencing prevents a stale worker from changing lifecycle state. It does not erase usage already incurred before lease loss or a committed planned handoff. Service can ingest a late immutable UsageRecord under the original failed, yielded, or otherwise terminal RunAttempt identity when its stable record identity and canonical content validate.

Late ingestion:

- cannot publish Run state, a retained Item, waiting pending data, or lifecycle transition;
- cannot change Run or RunAttempt outcome;
- preserves the original RunAttempt attribution and ingestion timestamp;
- remains idempotent by `record_id`; and
- can be supplemented by separately identified provider evidence under an owning reconciliation contract.

Late receipts remain queryable attribution evidence and do not rewrite the sealed operational usage snapshot or an already published lifecycle event.

This exception prevents lease loss from silently dropping attributable usage without weakening lifecycle fencing.

## Large Content

Large model, tool, command, file, or child outputs use object storage only after bounded staging, digest verification, authorization through the owning Run or Item, and durable selection. They remain content of that owning record and have no independent product identity. Object keys, file paths, digests, and signed URLs grant no product authority by possession.

An explicitly published [Asset](32-asset-management.md) is the distinct exception: its accepted publication creates an independent immutable `asset_id`, and Run output or an Item can retain the resulting `AssetRef`. Automatic spill, output compaction, replay retention, command capture, or object staging never upgrades content into an Asset. Asset deletion and retention remain independent from the referencing Run or Item.

For example, a command result that exceeds the inline Item limit is staged as an object and selected before a committed display snapshot references it. An unselected upload is a cleanup candidate. A selected missing object produces an explicit content-read failure; Service does not reinterpret it as a missing Item or use it as continuation state.

## Observability

OpenTelemetry traces and metrics correlate safe service role, `worker_build_id`, worker generation, Session, Thread, Run, RunAttempt, Harness Run, and provider identities. They omit credentials, authorization headers, plaintext Secrets, raw prompts, model output, tool payloads, and uploaded content by default.

Telemetry is best effort. Its loss cannot erase durable audit, lifecycle, retained Item, state, or usage facts, and its presence cannot prove commitment.

## Failure Semantics

| Failure                                            | Outcome                                                                                              |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Run Stream append outcome is unknown               | Reconcile by stable event identity; never infer Run commitment from Redis                            |
| Redis retention no longer covers requested cursor  | Return an exact-replay gap when no raw source covers it; recover display through the snapshot cursor |
| Subscriber or client buffer overflows              | End the affected attachment explicitly; Harness work and Run authority continue                      |
| Replay snapshot publication fails                  | Sealed Run remains authoritative; retry deterministic create-only publication                        |
| Webhook delivery exhausts retries                  | Destination record is dead-lettered; source remains authoritative                                    |
| Webhook events arrive out of resource order        | Receiver persists the delivery and reconciles the resource-scoped lifecycle API before applying it   |
| Client disconnects                                 | Run continues according to durable state                                                             |
| Usage report repeats or overlaps terminal snapshot | `record_id` deduplication prevents double counting                                                   |
| Stale RunAttempt supplies valid late usage         | Usage is attributed and retained without lifecycle mutation                                          |
| Yielded Run cleanup reaches its protocol observer  | No public terminal AG-UI event is fabricated; the running Run Stream remains open                    |
| Same usage identity has different content          | Ingestion fails closed and emits a security diagnostic                                               |
| Object upload and owning-record commit diverge     | Cleanup or an explicit content-read failure preserves owning-record authority                        |
| Harness pricing is disabled, declined, or fails    | Raw usage remains durable and ordinary Service operation is unaffected                               |

## Invariants

01. Harness observations, AG-UI events, Run Stream entries, Items, lifecycle events, and delivery envelopes remain distinct.
02. Service uses `HarnessAguiObserver` and does not own a second Harness-to-AG-UI vocabulary.
03. One Run uses one stable Redis Stream across all its RunAttempts.
04. A lifecycle event represents a committed transition and is never inferred from transport delivery.
05. Run Stream entries, Items, replay snapshots, and lifecycle events never become Harness continuation state.
06. Source commitment, stream append, retained-snapshot publication, external delivery, and client receipt are separate facts.
07. Usage is ingested idempotently by immutable UsageRecord identity, not report or aggregate identity.
08. Late stale-RunAttempt usage cannot change lifecycle state.
09. Object references and signed delivery URLs grant no product authority.
10. Telemetry observes the system and never acts as durable lifecycle authority.
11. Live-only Run Stream entries and Items never create durable Hook-delivery intents; durable subscriptions select committed lifecycle events only.
12. Native, Hosted AG-UI, and A2A delivery are independent projections over shared Service facts and never translate through one another.
13. A Webhook envelope identifies its resource sequence and version, but the Webhook transport makes no ordering or exactly-once-processing guarantee.
14. Planned handoff has an internal `run_attempt.yielded` lifecycle fact without closing the Run Stream or emitting an AG-UI Run terminal event.
15. Only explicit Asset publication creates independent binary identity; automatic large-content handling remains owned by its Run or Item.
