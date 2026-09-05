# Event Reception and Routing

## Design Position

An [Application Account](01a-application-accounts.md) receives authenticated provider events when reception is enabled. Trusted adapters normalize input and choose exact provider references. Foundation admits that input durably, then uses ordinary Run acceptance or Steer. Connectivity owns no Agent runtime or second Agent inbox.

## Exact Account Targets

An AccountTarget identifies one provider object by unique `(account_id, target_kind, external_target_id)`. Supported kinds and canonical identifiers belong to the provider adapter: for example a Slack channel, Lark chat, or GitHub repository ID. Names, content, wildcards, expression matchers, priorities, and semantic similarity cannot select targets.

Each target has immutable identity, mutable `receive_enabled`, optional `agent_id`, optional `config_override`, optional `input_batching`, optional `provider_policy`, and a management version. A missing target inherits Account defaults. A disabled target blocks its exact object and does not fall through to defaults. An omitted Agent inherits the Account default. Target configuration is a complete replacement under an exact version precondition; omission removes the corresponding target override.

`config_override` accepts only `model`, `skills`, `connector_tools`, and `mcp_tools`. It uses the canonical [Agent Run override](../28-agent-management.md) merge: omitted categories inherit, explicit empty collections clear, and explicitly selected entries replace the category. Model fields use the canonical Model override. Instructions, input schema, environment, plugins, Agent identity, hooks, and delegation settings cannot enter this boundary. Both the configuring actor and execution Service Account require current authority for referenced resources; ordinary Run preparation and freezing validate the resulting configuration again.

Management uses `/api/v1/application-accounts/{account_id}/targets` for create/list and its `/{target_id}` child for get, full replacement, and delete. Independent Ingress and Route resources, Agent allowlists, and mapping configuration do not exist.

## Normalized Event and Input

An adapter authenticates the bounded request, verifies the configured installation identity, extracts a stable external event identity and correlation references, and produces a versioned bounded `InboundEvent`. Provider-specific actor, context, data, content, and timestamp semantics remain owned by the adapter. An external actor is input context, never a Foundation Principal.

The fixed input projection contains an ordered `events` array inside canonical [AgentInput](../17-agent-input.md) structured content. Each event exposes only `type`, `occurred_at`, `text`, `actor`, `context`, and `data`. It preserves normalized task content and event order for single events and batches alike. Protected Account, target, binding, deduplication identity, credentials, and dispatch authority stay outside this projection. The projection and final selected Agent input schema are validated before acceptance; invalid deterministic input is rejected.

There is no configurable input expression language and no raw webhook archive. Raw bytes exist only for bounded authentication and parsing. Provider normalization must retain the useful bounded task data; it cannot depend on a raw-object fallback. Provider data cannot select an Agent or grant capabilities.

## Binding, Event, and Batch Authority

Three durable facts have separate responsibilities:

| Fact    | Authority                                                                                                                                                    |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Event   | Account-scoped external identity and request digest, safe normalized payload, order, size, and deduplication horizon; one direct nullable Batch reference    |
| Binding | Unique `(account_id, external_ref.kind, external_ref.id)` correlation to a nullable Foundation Thread, submission sequence, and next allowed submission time |
| Batch   | Ordered members, one provider/input configuration snapshot, scheduling and claim fence, and exact accepted Run/Steer receipt or terminal rejection           |

A Binding reservation can exist before its first Run. It fixes no Agent. The first successful acceptance atomically binds the Thread; concurrent deliveries reuse the same reservation. Later events retain that Thread even when the Account default or exact target Agent changes. A reference requiring an established discussion needs a non-null Thread, not merely a reservation.

Events assigned to a Batch have no independent delivery status, configuration copy, lease, retry schedule, or receipt. The Batch owns those facts. Deterministically rejected events may retain bounded rejection and deduplication evidence without a Batch. Terminal evidence expires after the provider replay horizon; pending payloads are never removed by retention.

## Input Batching and Frequency

`InputBatchingPolicy` contains positive `min_interval_ms` and `max_batch_events`, constrained by deployment bounds. Exact targets inherit the Account policy when absent; absent Account policy uses the adapter default. Serialized input and Workspace/Account pending-event and byte limits remain deployment safety controls.

The first event is eligible immediately. After each accepted Run or Steer, the Binding's next submission time advances by the accepted Batch interval. Events can append to the latest compatible, never-claimed pending Batch until its fixed deadline or size/count bound. Appending never slides the deadline. Overflow and configuration-generation changes create later ordered Batches; they do not bypass the Binding frequency limit.

A claim permanently freezes membership even when that claim expires or delivery retries. Claim owner, generation, and expiry fence completion. Claiming locks the Binding before the Batch and only selects its earliest pending Batch. An earlier locked, deferred, or in-flight Batch prevents later sequence numbers from overtaking it. Different Bindings can progress independently.

Compatibility covers Account/target configuration generations and the provider normalization/policy/input contract. The snapshot owns admitted provider context, policy, native reply authority, and batching. It does not freeze an Agent Revision for pending ordinary input.

## Canonical Run and Steer Acceptance

Before a new submission, current Account and target reception, execution Service Account, Workspace authority, and destination eligibility are checked. For an idle Thread, the current exact-target Agent or Account default and current narrow override enter ordinary canonical preparation and freezing. Existing history does not permanently select the next Agent.

- An unbound reservation uses root Run acceptance.
- An empty existing Thread uses ordinary empty-Thread continuation.
- An idle existing Thread uses ordinary continuation from its head.
- A current accepted/running Run or selected current/head waiting Run receives Steer through Foundation's [Thread inbox](../19-agent-control-active-execution.md#steer-command).

Steer does not replace the current Run's Agent, configuration, tools, or native context. Configuration edits do not create a queue waiting for an active Run to finish. A transition race is reread and retried through the same canonical commands.

The canonical transaction writes Run or Steer acceptance, the first Thread binding when needed, the Batch's exact receipt, and the Binding submission clock together. Configuration and claim generations are rechecked within that transaction. A stale claim rolls back all these writes. Replay reads a persisted Batch receipt before current configuration or IAM, so a committed submission remains identifiable after disablement or a lost reply.

A new inbound Run receives protected [InboundRunContext](04-agent-facing-tools.md#inboundruncontext) and the execution Service Account as authority. Later Attempts obey ordinary fencing and current capability authorization. Receipt durability is not proof of an outbound effect.

## Provider Data-Plane HTTP

`connectivity` and `all` mount:

```http
POST /connectivity/v1/accounts/{account_id}/events
```

The Account ID locates credentials and is not authentication. The adapter verifies the exact request and provider installation identity. Challenges, acknowledgements, unsupported traffic, signature failures, and capacity backpressure follow the [provider wire contract](07-built-in-ingress-adapters.md).

Eligible input is durable before acknowledgement or cursor advancement. A duplicate retained event reuses the existing admission; the same external identity with conflicting content fails safely. Capacity exhaustion cannot acknowledge an unretained eligible event. Provider transport is at-least-once; retained event identity plus atomic Batch receipts makes Foundation input acceptance at-most-once within its deduplication horizon. This does not make external tool effects exactly once.

The canonical input acceptor is required process composition for reception readiness. Missing composition fails startup; there is no permanently unavailable bridge mode. Drain stops new receipt, completes admitted requests within the shutdown bound, and releases claims. Worker/control-only processes do not expose provider event reception.

## Failure Semantics

| Condition                                                   | Outcome                                                         |
| ----------------------------------------------------------- | --------------------------------------------------------------- |
| Invalid authentication, identity, or body bounds            | Reject before admission                                         |
| Deterministically irrelevant event or disabled exact target | No Agent input; provider-safe acknowledgement                   |
| Capacity exhausted                                          | Backpressure without losing acknowledged input                  |
| Account, target, or execution authority becomes unavailable | Pending Batch is rejected with a safe reason                    |
| Input schema or projection is invalid                       | Terminal rejection                                              |
| Temporary infrastructure failure                            | Retry the same frozen Batch, preserving sequence and membership |
| Claim or Thread transition race                             | Reread/retry with current fence; no partial acceptance          |
| Acceptance reply lost                                       | Replay exact persisted Run or Steer receipt                     |
