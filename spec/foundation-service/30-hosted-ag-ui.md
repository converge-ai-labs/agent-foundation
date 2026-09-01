# Hosted AG-UI

## Design Position

Foundation Service hosts an AG-UI HTTP/SSE adapter for every callable AgentPreset. The adapter accepts standard `RunAgentInput`, maps it to the canonical [`AgentInput`](33-agent-input.md) and the existing Session/Thread/Run control contract, and delivers standard AG-UI `BaseEvent` values derived from the selected Harness release group and durable Foundation facts. It is not another Agent runtime or lifecycle authority.

New and continued AG-UI work authorizes the same `agent_preset.invoke`,
`run.continue`, and, when waiting defaults are selected, `run.feedback` actions
as the equivalent Native operation. Cancellation authorizes `run.interrupt`, and
SSE attachment authorizes `run.read`. The IAM
[stable action registry](10-identity-and-access-management.md#stable-action-registry)
owns these action names and grants; external IDs and adapter bindings never
select or preserve authority.

Hosted AG-UI is always present on `control` and `all` roles. It has no deployment or AgentPreset-level enable switch and does not require a Foundation SDK. A standard AG-UI client can call it using the wire profile defined here.

## Boundaries

| Concern                                                                              | Owner                                                                                        |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Standard AG-UI input and event models                                                | Pinned upstream AG-UI dependency selected by the Foundation-compatible Harness release group |
| Harness-to-AG-UI observation                                                         | [`HarnessAguiObserver`](../agent-stream-protocol/00-overview.md)                             |
| Canonical accepted input                                                             | [Agent Input](33-agent-input.md)                                                             |
| Durable Run acceptance and waiting feedback                                          | [Agent Control](34-agent-control-input-and-continuation.md)                                  |
| Durable interruption                                                                 | [Active Execution](35-agent-control-active-execution.md)                                     |
| Hosted external bindings, input validation, lifecycle projection, retention, and SSE | This document                                                                                |
| Current Principal and AgentPreset authorization                                      | [Foundation IAM](10-identity-and-access-management.md)                                       |
| Preset-specific schemas, visibility, and limits                                      | [Protocol configuration](12-agent-management.md#protocol-configuration)                      |

The adapter never reconstructs AG-UI events from Native notification envelopes and never implements a second Harness event converter.

## HTTP Surface

```http
POST /ag-ui/v1/agent-presets/{agent_preset_id}/runs
Content-Type: application/json
Accept: text/event-stream
Last-Event-ID: <hosted-agui-cursor>
```

The request body is one standard `RunAgentInput` under the pinned AG-UI schema. Its `agent_preset_id` comes only from the route. No body field, `context`, or `forwardedProps` value can select another Foundation AgentPreset, Workspace, Model, Secret, Connection, or Principal.

The response is SSE. Each AG-UI event is serialized as one standard JSON `BaseEvent` in a `data` field. Foundation can add an SSE `id` for retained delivery and reconnect; that ID is a Hosted AG-UI delivery cursor, not a standard AG-UI identity or Native Run Stream cursor. `Last-Event-ID` resumes exclusively after the last completely applied hosted event.

The adapter also exposes the AG-UI cancellation surface:

```http
POST /ag-ui/v1/agent-presets/{agent_preset_id}/cancel
Content-Type: application/json
```

The body names the external `threadId` and `runId`. Cancellation resolves their persisted binding, reauthorizes the current AgentPreset and Run, and invokes the Native durable Run interrupt command. The Foundation Run outcome is `cancelled`; closing the run SSE does not call this command.

## External Binding

Hosted AG-UI persists a binding with this conceptual meaning:

```python
class AguiThreadBinding:
    client_identity: str
    agent_preset_id: str
    external_thread_id: str
    session_id: SessionId
    root_thread_id: ThreadId
    active_thread_id: ThreadId


class AguiRunBinding:
    client_identity: str
    agent_preset_id: str
    agent_preset_revision_id: str
    external_thread_id: str
    external_run_id: str
    run_id: RunId
```

The schemas are conceptual durable records, not another public resource model. External IDs are opaque bounded correlation chosen by the client. They grant no read, continuation, cancellation, or stream authority.

The first accepted AG-UI run for a previously unbound `(client identity, AgentPreset, threadId)` creates a Session, root Thread, and root Foundation Run through the ordinary application contract. A later AG-UI run continues the binding's current active Thread. `parentRunId` bound to the active completed Foundation Run is an ordinary continuation; one bound to an authorized earlier completed Foundation Run creates an explicit Foundation fork and atomically selects that fork as the binding's active Thread. A new ordinary user tail against the exact current waiting Run has the explicit abandonment meaning defined below. Cross-Preset, cross-client, incomplete, or concealed sources fail.

`runId` is the idempotency identity within one client, AgentPreset, and external Thread. Repeating the same `runId` and canonical accepted input returns or reattaches to the original Run. Reuse with different input conflicts. A lost HTTP response never causes the client to invent another `runId` for the same intent.

## Input Authority

Hosted input is append-only relative to Foundation history:

- a new `user` message can become accepted input for a new Run;
- a new `tool` message must resolve one exact unconsumed waiting client-tool call;
- assistant, system, developer, reasoning, and prior tool history are never imported or overwritten by a client snapshot; and
- a full `messages` array is a compatibility snapshot whose known prefix must equal the current authorized presentation and whose only accepted difference is a valid new tail.

An initial call does not import an arbitrary prior transcript. Historical import is a separate Native operation with its own authority and validation.

The adapter maps the accepted new user tail into one canonical `AgentInput`. Text and binary parts retain their order, media type, acquisition, and delivery semantics; structured AG-UI data maps to `structured_content` and follows the selected AgentPresetRevision's optional `ProtocolConfig.input_data_schema`. The common Agent input contract validates the resulting value before the control operation accepts a Run. AG-UI protocol fields that express state, context, client tools, or feedback remain command options or correlated feedback and never enter `AgentInput` implicitly.

Standard `state`, `context`, and `tools` plus Foundation extensions are bounded untrusted inputs:

| Input                   | Accepted meaning                                                                                                     |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------- |
| empty or absent `state` | Always valid                                                                                                         |
| non-empty `state`       | Product context validated by the selected Revision's public JSON schema; never `HarnessState` or a resource mutation |
| `context`               | Bounded semantic context validated by the selected Revision's policy; never authentication or resource selection     |
| `tools`                 | Client-executable tool declarations validated against the selected Revision's policy and frozen at acceptance        |
| `forwardedProps.a13n`   | Versioned Foundation extension object containing only fields declared below                                          |

`forwardedProps.a13n` can contain a structured `resume` array. Each element names one pending call and one approve, reject, complete, or respond value under the exact frozen kind. The array can cover any explicit subset and calls the same atomic waiting-feedback command as Native input; omitted approvals become rejections and omitted non-approval calls become no-response outcomes. It accepts a new child Run and never reopens the waiting parent. Duplicate, unknown, or mismatched entries and unknown Foundation extension fields fail validation.

When the binding's current/head Run is waiting, a new ordinary user tail without `resume` means “abandon the outstanding interaction and handle this message.” The adapter reads the exact waiting digest, invokes the Native existing-Thread route with `waiting_resolution.mode="defaults"`, and supplies the mapped `AgentInput`. That explicit application call creates one `waiting_continue` successor: its first model request receives both the default deferred results and the new user input. Existing queued submissions remain ordered and unconsumed. A user tail and explicit `resume` are mutually exclusive in one AG-UI request; the client sends another external `runId` after explicit feedback if it needs further semantic input.

The adapter sets `waiting_resolution` only for this declared abandonment case. It never adds the field to an ordinary completed-head continuation or silently converts an extension validation failure into defaults. The server-owned binding supplies current Thread version and sealed digest under the same final concurrency checks; a stale binding conflicts without accepting a Run or rebinding pending inbox delivery.

The normalized client tool surface and exact `agent_preset_revision_id` are frozen with the accepted Run. A feedback run reuses the waiting Run's exact surface; it cannot change tool names, schemas, or pending-call identity.

## Lifecycle Projection

One external `runId` binds one Foundation Run. It never binds a RunAttempt or Harness Run.

```mermaid
sequenceDiagram
    participant Client
    participant Adapter
    participant App as Foundation application
    participant Worker
    participant Projection as Hosted AG-UI projection

    Client->>Adapter: RunAgentInput
    Adapter->>App: authorize and accept Run
    App-->>Adapter: durable Run receipt
    Adapter-->>Client: RUN_STARTED
    Worker->>Projection: allowed observer events
    Projection-->>Client: text and tool events
    Worker->>App: waiting or sealed terminal outcome
    App->>Projection: durable outcome projection
    Projection-->>Client: interrupt, RUN_FINISHED, or RUN_ERROR
```

`RUN_STARTED` comes from durable Run acceptance. `RUN_FINISHED` and `RUN_ERROR` come only from the selected sealed Run outcome. An observer's Harness terminal event is suppressed as an external lifecycle authority and is replaced by the matching durable projection. Worker takeover creates a new Harness Run and fresh observer without changing external `runId` or producing another `RUN_STARTED`.

A waiting Run emits the versioned custom `a13n.foundation.run_status` event with `status="waiting"` and a complete authorized pending contract, then closes the current delivery attachment. It does not emit a false success or error. A later new `runId` carrying valid `resume` accepts and observes the feedback Run; a later ordinary user tail explicitly defaults that pending set and observes the one composite waiting-Continue Run. Waiting-bound steer and async results are not projected into the composite first request and enter only when the Foundation-owned awaited delivery hook reaches its later safe boundary.

Transport abort, HTTP cancellation, EOF, timeout, and SSE disconnect terminate delivery only. The Run continues according to durable state.

## Event Visibility

The Hosted profile always permits these standard event families when their source exists:

- Run lifecycle produced by the Hosted adapter;
- text message lifecycle; and
- client-visible tool-call and tool-result lifecycle.

The selected Revision's ProtocolConfig can select supported state, message snapshot, activity, subagent, and safe reasoning-summary projections from the Gateway's registered allowlist. It cannot expose raw chain-of-thought, encrypted reasoning values, raw provider frames, internal tool payloads, or Foundation execution identities.

`RAW`, raw `a13n.harness.*` fallback, RunAttempt, Worker, Redis, internal Harness Run, provider-native events, and unprocessed exceptions are never delivered. The stable Foundation custom registry initially contains only:

| Name                         | Meaning                                                                                                                               |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n.foundation.run_status` | Accepted, running, waiting, cancellation, or other safe durable Run status projection                                                 |
| `a13n.foundation.artifact`   | Stable authorized result projection; an explicitly published Asset carries its bounded `AssetRef` without an object key or bearer URL |
| `a13n.foundation.replay_gap` | Hosted delivery history is unavailable and client reconciliation is required                                                          |

Each custom `value` contains its own `schema_version`. ProtocolConfig can select from this finite registry but cannot invent an event name or schema.

When `a13n.foundation.artifact` projects an [Asset](37-asset-management.md), the hosted binding retains the exact `asset_id` and reauthorizes the current caller before metadata or content delivery. The custom event does not create another Asset identity, pin Asset retention, or make an AG-UI cursor a content credential. A deleted Asset remains unavailable even when the hosted event is still replayable.

## Replay and Failure

Foundation retains a Hosted AG-UI projection with stable event identities and a bounded cursor independently from `HarnessAguiObserver.snapshot()`. Reconnect replays that projection and crosses to live delivery without skipping an event. `HarnessAguiObserver.resume()` can reconstruct one fresh observer from exact source history for one Harness Run; it is never the client replay mechanism and never spans Worker-created Harness Runs.

If the requested Hosted cursor is outside retained history, attachment fails before SSE with a bounded conflict when known. A gap discovered after streaming starts emits `a13n.foundation.replay_gap` and closes. The client reads current Foundation state or starts another supported reconciliation flow; it never continues from an arbitrary surviving event.

| Failure                                     | Observable outcome                        | Durable consequence                    |
| ------------------------------------------- | ----------------------------------------- | -------------------------------------- |
| Invalid snapshot or extension               | Bounded AG-UI request failure             | No Run accepted                        |
| Duplicate `runId`, same input               | Reattach or replay original Run           | No duplicate Run                       |
| Duplicate `runId`, different input          | Conflict                                  | Original Run unchanged                 |
| Observer conversion fails                   | Hosted delivery fails safely              | Durable Run authority remains separate |
| Client disconnects or overflows             | Attachment closes                         | Run continues                          |
| Cancellation command races terminal outcome | Owning Run transition selects one outcome | External Run projects the winner       |

## Compatibility and Invariants

Foundation selects one published Harness release group, which pins the Harness, Agent Stream Protocol, Environment Provider, and compatible AG-UI Python schema. Foundation manifests and compatibility tests select exact package versions; this specification defines behavior and does not hard-code a release artifact version. Breaking changes to Foundation extensions require a new extension schema or Hosted route compatibility line.

1. Hosted AG-UI uses `HarnessAguiObserver` as its only Harness event converter.
2. One external AG-UI Run binds one Foundation Run; Worker recovery does not change either identity.
3. Client messages append accepted input and never overwrite Foundation history.
4. Durable acceptance and sealed outcome, not transport or Harness observation, produce external Run lifecycle.
5. A waiting Run is resumed by accepting another Run under another `runId`; explicit `resume` uses waiting feedback, while a new ordinary user tail uses declared default abandonment and waiting Continue.
6. Hosted disconnect never cancels a Run.
7. Every delivered event belongs to the stable Hosted visibility registry and contains no private execution payload.
