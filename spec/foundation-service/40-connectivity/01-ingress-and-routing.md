# Ingress and Routing

## Design Position

Native Ingresses receive provider events, authenticate and normalize them, route them to one Agent input destination, and submit them through Foundation's existing durable input contracts. They do not execute an Agent, send provider messages, or own another inbox.

Provider configuration is intentionally typed by each adapter. Slack signing secrets, Lark app credentials, Discord gateway configuration, Gmail watch state, and GitHub App installation data do not enter one universal credential or event schema. The stable contract begins with Foundation-owned Ingress identity and the normalized event envelope.

## Ingress and Route

The following conceptual schemas are not wire or ORM models:

```python
class Ingress:
    id: IngressId
    organization_id: OrganizationId
    workspace_id: WorkspaceId
    name: str
    provider_key: str
    provider_config_version: str
    provider_config: IngressProviderConfig
    execution_principal_ref: PrincipalRef
    agents: tuple[AgentId, ...]
    default_agent_id: AgentId
    status: Literal["active", "disabled"]
    version: int
    created_by: PrincipalRef
    created_at: datetime
    updated_at: datetime


class Route:
    id: RouteId
    ingress_id: IngressId
    name: str
    provider_config_version: str
    match: RouteMatchConfig
    agent_id: AgentId | None
    input_mapping: InputMapping | None
    input_batching: InputBatchingPolicy
    capability_overlays: Mapping[AgentId, RunCapabilityOverlay]
    provider_policy: RouteProviderPolicy
    enabled: bool
    version: int
```

`IngressProviderConfig` and `RouteMatchConfig` are adapter-owned tagged unions of strong provider-specific types, such as `SlackIngressConfig` and `GitHubRouteMatch`. They are not arbitrary JSON dictionaries and have no universal credential or matching schema.

An Ingress is one concrete installed or authorized inbound-capable external identity in one Foundation Workspace. A provider's reusable application definition can support many tenant installations, but routes, allowed Agents, and current credentials belong to each concrete Ingress. An Ingress can be receive-only or receive plus bounded same-identity native actions; a send-only identity belongs in the Connector or Remote MCP path.

`execution_principal_ref` is an immutable reference to one active Service Account in the same Workspace. It is the Foundation Principal for every Run or Steer initiated by that Ingress. The external Slack, Lark, Discord, Teams, GitHub, Gmail, or other provider actor remains authenticated audit and model context but never becomes a Foundation Principal by identifier, username, or email coincidence. Admission and every later RunAttempt reauthorize the Service Account's current Agent invocation and shared capability grants. Disabling or deleting it, removing a required RoleBinding, or losing authority for the selected Agent blocks new input without rewriting accepted work.

Organization, Workspace, provider key, execution Principal, and the adapter-declared external installation, tenant, or account identity are immutable. Changing any of them creates another Ingress so retained bindings and event identity never change meaning. Credential rotation, event-subscription state, display configuration, allowed Agents, default Agent, status, and other identity-preserving provider settings update the existing Ingress under exact version preconditions.

`active` means the Ingress is administratively enabled; it is not a continuous health claim. `disabled` blocks new event admission and native action use until explicitly re-enabled. Authentication, subscription, transport, and provider failures remain bounded safe observations and do not create another Ingress lifecycle state. Transient failures remain retryable under the provider contract, while a credential or configuration problem that needs user action is reported through safe diagnostics until the user repairs or disables the Ingress.

The default Agent, every Route-selected Agent, and every Agent named by `capability_overlays` must occur in the Ingress's unique `agents`. For an unbound external reference, a Route-selected Agent overrides the Ingress default only for events matched by that Route. An existing Binding remains fixed. Event content cannot select or switch an Agent.

`match` and `provider_policy` are provider-specific typed data validated by the adapter under the exact `provider_config_version`. Messaging `provider_policy` has the common bounded shape owned by [Messaging Ingress](02-messaging-ingress.md#interaction-policy); other providers retain their own typed policy. `capability_overlays` uses the common [Run Capability Overlay](../28-agent-management.md#run-capability-overlay) contract and is never interpreted by the provider adapter. The selected Agent's entry applies when present; absence means no Route overlay for that Agent. Foundation does not pretend that a Slack channel, Gmail label, GitHub repository, and webhook path share one native resource model.

One event resolves to zero or one Route. Route matchers under one Ingress must be non-overlapping for every provider event type; configuration validation rejects overlap it can prove, and runtime ambiguity fails closed. An unmatched event uses the provider's bounded default policy, input mapping, input batching, and no Route capability overlay; an unbound target uses the Ingress default Agent, while an existing Binding retains its Agent. An event never fans out because several Routes match.

Disabling an Ingress rejects new event admission after authentication and prevents native action use. An event in the scope of a disabled Route is ignored or rejected under provider policy and never falls through to the unmatched Ingress defaults. Disablement rewrites no retained event, Binding, Thread, or Run fact.

## Normalized Event

```python
class ExternalRef:
    kind: str
    id: str


class InboundEvent:
    ingress_id: IngressId
    external_event_id: str
    type: str
    occurred_at: datetime | None
    received_at: datetime
    text: str | None
    actor: BoundedJsonObject | None
    context: BoundedJsonObject
    refs: Mapping[str, ExternalRef]
    data: BoundedJsonObject
    raw_ref: ProtectedObjectRef | None
```

The outer fields have common meaning; `actor`, `context`, `data`, and protected raw bytes retain provider-specific schemas. Safe actor and context projections include provider IDs plus available display names, workspace or tenant labels, channel or resource names, mention information, and reply relationships so the Agent receives useful context without parsing protected raw bytes. Each event type has a provider-owned normalization version. The adapter, not a user template or model, owns:

- body and header bounds;
- signature, gateway, poller, or provider-session authentication;
- stable external event identity;
- provider timestamp interpretation;
- trusted actor and external reference extraction; and
- redaction and classification before protected raw retention.

Users cannot redefine these security or correlation facts from arbitrary raw JSON. `refs` contains only stable references the adapter declares, such as a Slack discussion, Gmail thread, GitHub pull request, or repository. Missing stable provider identity remains missing; Foundation does not hash mutable display text or ask a model to invent one.

Raw provider data is never included in Agent input by default. When policy retains it, it is bounded protected evidence with independent access and retention; logs, errors, metrics, and ordinary event reads omit it.

## Input Mapping

Every provider supplies a useful default mapping to the canonical [AgentInput](../17-agent-input.md). A Route can replace that default with a bounded declarative mapping over this safe view:

```python
class EventInputView:
    type: str
    occurred_at: datetime | None
    text: str | None
    actor: BoundedJsonObject | None
    context: BoundedJsonObject
    data: BoundedJsonObject


class EventInputBatch:
    events: tuple[EventInputView, ...]
```

The mapping receives one ordered `EventInputBatch`, including a one-element batch when no coalescing occurred, and constructs one complete canonical `AgentInput`. Its explicitly versioned expression language contains exactly four composable operations:

1. select a complete safe value or nested field through a bounded structural path;
2. construct an object from named child expressions;
3. construct an array from ordered child expressions; and
4. produce a bounded static JSON value, including as a selection's default when its path is missing.

A missing selected path without a configured static default fails the mapping. One mapping owns both single-event and bounded-burst transformation rather than requiring a second batch-to-input contract. An added mapping-language operator cannot expose the protected event envelope or change event authority.

The mapping supports no Jinja, string interpolation, JSONPath, executable code, network call, Secret lookup, or arbitrary expression. It is compiled when the Route changes and its result is validated as canonical `AgentInput` and against the selected Agent input schema again when the single event or completed batch is prepared for submission. A deterministic mapping or validation failure rejects the frozen admission rather than retrying it. Provider data remains untrusted input and cannot add an Agent, Tool, Skill, MCP server, ConnectorConnection, Principal, permission, or Run grant.

Protected source metadata such as Ingress, Route, event identity, correlation refs, and `raw_ref` is not part of `EventInputView` and remains outside model-controlled input. A mapping can expose safe normalized values to the model, but doing so does not make those values authority. A new Run accepted from this event retains the exact protected [`IngressRunContext`](04-agent-facing-tools.md#ingressruncontext); an event accepted as Steer remains correlated to its durable Ingress admission and Steer receipts without becoming a second Run source.

## Input Batching and Frequency

```python
class InputBatchingPolicy:
    min_interval_ms: int
    max_batch_events: int
```

Both values are positive and constrained by deployment safety bounds. A Route can choose them within those bounds; an unmatched event uses the provider's bounded default. They regulate Foundation input submission, not provider receipt.

After Route, Agent, and Agent Thread resolution, eligible events are coalesced by destination Agent Thread while preserving exact Ingress, Route, mapping, target, and capability compatibility. The first event when no submission interval is active is submitted immediately. Further compatible events within `min_interval_ms` append in provider order to the one logical pending batch for that Agent Thread. At the interval boundary, the selected `InputMapping` converts the complete batch into one canonical `AgentInput`. A batch never exceeds `max_batch_events` or the platform byte bound; overflow forms later ordered batches and is neither discarded nor summarized by the infrastructure. An incompatible event retains its admission until it can be submitted without changing the active Run's context or tools. An active or current/head waiting Run receives a batch only through one Steer. An inactive Thread accepts one ordinary Run.

This mechanism bounds Run and Steer frequency without weakening receipt durability. Workspace and Ingress pending-event and byte budgets are deployment safety limits rather than additional Route knobs. When durable capacity is exhausted, the adapter applies provider-specific backpressure, withholds an HTTP acknowledgement, or does not advance a gateway or poller cursor. It never acknowledges an unretained eligible event and then drops it silently.

## Agent and Agent Thread Routing

```python
class AgentThreadBinding:
    ingress_id: IngressId
    external_ref: ExternalRef
    agent_id: AgentId
    agent_thread_id: ThreadId
    version: int
    created_at: datetime
    updated_at: datetime
```

`(ingress_id, external_ref.kind, external_ref.id)` is unique. One stable external reference therefore fixes one Agent and one Agent Thread. Creating the first Binding and accepting the first Run for its Agent Thread is atomic from the caller's perspective: a concurrent duplicate reuses the winning identity and does not create another Binding.

Provider routing first resolves one adapter-declared stable reference from `refs`. An exact existing Binding fixes both Agent and Agent Thread. Without a Binding, the matched Route override or Ingress default selects one allowed Agent, then atomically creates the first Agent Thread and Binding. The correlation rule cannot query arbitrary raw paths or use fuzzy matching. If the selected reference is absent, correlation fails safely rather than silently creating an unrelated history.

Routing independently resolves the bound or initially selected Agent's effective capability configuration. User-mapped input cannot change the Agent, Binding, or capabilities. An accepted routing result contains zero or one matched Route, one Agent, and one new or existing Agent Thread. The Connectivity subsystem does not fan out one event; independent multi-Agent work begins only when the selected Agent explicitly creates it through the ordinary Agent runtime.

## Reliable Admission and Service Input

```mermaid
sequenceDiagram
    participant Provider
    participant Adapter as Inbound adapter
    participant Store as Durable admission
    participant Router as Route resolver
    participant Service as Foundation input acceptance
    participant Agent as Agent Thread

    Provider->>Adapter: provider event
    Adapter->>Adapter: bound, authenticate, normalize
    Adapter->>Router: match Route and apply deterministic admission policy
    Router-->>Adapter: irrelevant or one Agent and Thread destination
    Adapter->>Store: persist event and immutable routing decision
    Router->>Service: selected Agent, Thread destination, AgentInput, capabilities
    Service->>Agent: create Run or steer active Run
    Service-->>Adapter: durable acceptance or safe rejection
    Adapter-->>Provider: provider-specific acknowledgement
```

After authentication and bounded normalization, the adapter and Route can reject an event through deterministic policy before durable admission. Examples include a group message that does not satisfy its messaging interaction mode, a disabled Route, or an unsupported event type. Such an event creates no Agent input and needs no durable retry record; the adapter can acknowledge it under provider policy.

Every event that can create Agent input is durably admitted before acknowledgement. The admission is an internal transport fact, not a public resource, second Agent inbox, or execution lifecycle. It begins in `pending` and can transition exactly once to `accepted` or `rejected`. `accepted` contains the exact Run or Steer receipt proving Foundation input acceptance. `rejected` contains one bounded safe reason proving that the event cannot be submitted without violating the accepted routing, input, or authorization contract. A transient failure leaves the admission `pending`; neither terminal state returns to `pending`. The concrete table, queue, worker, retry counter, and storage layout are replaceable implementation details.

The durable admission freezes the complete effective routing decision that made the event eligible: the matched Route or unmatched-default decision, selected Agent, adapter-declared correlation target, selected existing Agent Thread Binding when present, Input Mapping, batching policy, provider policy and native actions, and capability overlay. A target without an existing Binding retains the exact Agent and stable external reference so the later atomic Binding and Run acceptance cannot choose another destination. Implementations can retain bounded immutable values or exact content digests; they cannot depend on rereading mutable Route configuration to recover the decision.

Pending retry never reroutes or reinterprets an event after configuration changes. Before Run or Steer acceptance, Foundation reauthorizes the frozen Agent, target, Ingress, Route, execution Principal, and capabilities against current administrative and IAM state. A transient service failure leaves the admission `pending`; disablement, deletion, lost authority, or incompatibility that makes the frozen decision invalid transitions it to `rejected` with a bounded safe reason. Foundation never substitutes another Agent, Route, mapping, target, or capability selection for that admission.

The adapter acknowledges an eligible HTTP webhook only after durable local admission or durable Foundation input acceptance. A separately deployed adapter can use its own durable spool before acknowledging; an in-process adapter does not create a second spool merely for layering. A gateway or poller advances its provider cursor only after the same durable boundary. Pending payloads are retained until accepted or rejected. Accepted and rejected identities retain only bounded deduplication evidence through the adapter-declared maximum provider retry or replay horizon; protected raw evidence follows its independent retention. Deterministically irrelevant events filtered before admission need no durable identity record. Once the horizon expires, the contract makes no unbounded historical deduplication claim.

Event identity makes provider retries idempotent through the declared retention horizon. Provider receipt is at-least-once; accepted Foundation input for one retained Ingress event identity is at-most-once. Neither claim makes model or external tool side effects exactly once.

After routing, the Ingress delegates completely to Foundation Service:

- a new destination uses ordinary new-Thread and root Run acceptance;
- an idle existing Thread uses ordinary existing-Thread Run acceptance;
- a compatible current running or current/head waiting Run uses only the common active [Steer contract](../19-agent-control-active-execution.md#public-steer-api) and never creates a queued submission;
- when a busy Run lacks the same Ingress context or accepted capability surface, the durably admitted event remains pending at the Ingress admission boundary until the Thread can accept a compatible Run rather than changing the active Run's tools; and
- any bounded burst coalescing occurs before one canonical Steer or Run submission and never replaces the durable Foundation Thread inbox.

If a Steer loses the race with a Run transition, routing rereads the Thread: it Steers the then-current compatible running or current/head waiting Run, retains the admission while work is accepted but not yet running, or accepts an ordinary successor Run when the Thread is inactive. The original external event identity makes this retry idempotent.

## Failure Semantics

| Condition                                                      | Outcome                                                                                                 |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Invalid signature, gateway identity, or payload bounds         | Event is rejected before routing and no Run authority is created                                        |
| Duplicate external event                                       | Prior pending state or terminal admission result is reused                                              |
| Ingress or Route disabled                                      | Event is safely ignored or rejected according to provider acknowledgement policy; no Run is accepted    |
| Execution Service Account is inactive or unauthorized          | Event remains unaccepted; no external actor or creator authority is substituted                         |
| More than one Route matches                                    | Routing fails closed and no Run is accepted                                                             |
| No allowed Agent can be selected                               | Routing fails safely and creates no AgentThreadBinding                                                  |
| Correlation reference is absent or incompatible                | Existing-thread routing fails; no fuzzy or fallback Binding is created                                  |
| Active Run is incompatible with the Ingress context            | Event remains durably admitted and is retried after the Thread can accept a compatible Run              |
| Frozen routing decision is no longer authorized or compatible  | Admission becomes `rejected`; no current Route or Agent is substituted                                  |
| Frozen Input Mapping or resulting AgentInput is invalid        | Admission becomes `rejected`; deterministic input is not retried                                        |
| Eligible event exceeds current durable admission capacity      | Adapter applies provider-specific backpressure and does not acknowledge or advance a replayable cursor  |
| Foundation input acceptance unavailable before acknowledgement | Adapter fails the delivery so the provider retries, unless an independent durable spool already owns it |
| Acknowledgement is lost after durable acceptance               | Provider retry resolves through the same external event identity                                        |

## Invariants

01. Provider authentication and normalization precede Route and Agent selection.
02. One normalized envelope permits provider-specific data without creating a universal provider payload schema.
03. User mapping controls the safe Agent input projection, not event identity, correlation, credentials, routing authority, or capabilities.
04. AgentThreadBinding fixes one exact adapter-declared external reference to one Agent and Agent Thread and never uses model inference.
05. Native ingress owns no parallel Agent inbox, Run queue, or Agent-execution retry lifecycle; its bounded admission retry ends at Run or Steer acceptance.
06. Every activation-eligible provider event is durable before acknowledgement; deterministically irrelevant traffic creates no admission record.
07. One inbound messaging event activates at most one Agent.
08. Ingress input never creates a queued submission; a compatible current running or current/head waiting Run receives only Steer.
09. Input batching preserves eligible event order and bounds Run or Steer frequency without silently dropping acknowledged input.
10. A durable admission moves only from `pending` to exact Run or Steer `accepted`, or to terminal `rejected` with one safe reason.
11. Durable admission freezes one effective routing decision; retries reauthorize that decision and never reroute it through current mutable configuration.
