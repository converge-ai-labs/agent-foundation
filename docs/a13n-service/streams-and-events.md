# Streams, events, and gateways

Service has separate channels for durable lifecycle facts, presentation output, notifications, and telemetry. Choose the channel for the question you need to answer; they do not share cursors or retention guarantees.

## Choose an observation channel

| Channel                                       | Use it for                                             | Recovery authority                                                    |
| --------------------------------------------- | ------------------------------------------------------ | --------------------------------------------------------------------- |
| Native Run SSE                                | Live and retained Run presentation events              | Run, Items, pending actions, and retained replay                      |
| Workspace / Run / RunAttempt lifecycle events | Durable execution transitions                          | Lifecycle pages and current resource versions                         |
| Notification WebSocket                        | Low-latency hints that subscribed resources changed    | Durable events and resource reads                                     |
| Trace queries                                 | Authorized diagnostic detail from a configured backend | Backend retention plus current Service access checks                  |
| AG-UI / A2A                                   | Protocol-specific interaction                          | The corresponding gateway contract, not Native SDK method equivalence |

## Attach to a Run stream

Use an accepted Run ID and the same authorized Workspace boundary as the Run read:

```bash
curl --no-buffer --fail-with-body "$SERVICE_URL/api/v1/runs/$RUN_ID/stream" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Accept: text/event-stream'
```

`Accept: text/event-stream` is required. Events carry an SSE `id`, an `event` type, and JSON `data`. Heartbeat comments are not application events. To resume, send the last **applied** event ID as `Last-Event-ID`; do not substitute a lifecycle sequence, Run ID, or wall-clock timestamp.

The Service authorizes attachment and periodically rechecks access. It bounds connection lifetime and can close a stream without completing the Run. Proxy buffering should be disabled for this route. Closing a tab, socket, iterator, or SDK client only stops local observation; [interrupt](agents-and-runs.md#waiting-steering-interruption-and-successors) is a separate command.

### Replay gaps

The Service tries retained replay when live history is no longer available. If it cannot satisfy the cursor, attachment fails with `run_stream_replay_gap`; an already-open stream can emit `a13n.service.replay_gap` and close. The gap includes the requested cursor and available floor/high-watermark information where known.

Do not silently skip a gap and append fresh deltas to a stale screen:

1. Stop applying deltas from that attachment.
2. Read current Run state, retained Items, and pending actions.
3. Rebuild the view from authoritative representations, including incomplete or failed outcomes.
4. Establish a new observation position appropriate to the rebuilt view. Preserve application-level deduplication when reapplying retained events.

A complete replay is not a resumable Harness checkpoint, nor is partial text proof of completed side effects. The low-level [Stream Protocol](../a13n-stream-protocol/index.md) defines typed stream processing; Service owns storage, replay, current authorization, and HTTP attachment.

## Consume durable lifecycle events

Three Native reads serve different scopes:

| Endpoint suffix (under `/api/v1`)       | Position input                            | Position output                                                                   |
| --------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------- |
| `/workspaces/{workspace}/events`        | Opaque `cursor`                           | `next_cursor`, `retained_floor`, `high_watermark`                                 |
| `/runs/{run_id}/events`                 | Integer `after_resource_seq`, default `0` | `next_resource_seq`, `retained_resource_seq_floor`, `high_watermark_resource_seq` |
| `/run-attempts/{run_attempt_id}/events` | Integer `after_resource_seq`, default `0` | The same resource-specific sequence fields                                        |

All accept `limit` from 1 to 200, default 50. Apply a page before storing its returned position. Workspace cursors are not portable between collections, principals, or resources. Sequence numbers belong to their resource and cannot be used as SSE IDs.

Run facts cover accepted, running, waiting, completed, failed, and cancelled transitions. RunAttempt facts separately cover leased, running, succeeded, yielded, failed, and cancelled. An Attempt yielding is not necessarily a terminal user Run.

Facts carry identity, resource version, mutation correlation, timestamps, and bounded payloads. Live projection can lag or retry after the durable fact exists. Use the resource's current version when acting on a notification or fact; an old event does not authorize a stale mutation. See [background ownership](background-tasks.md) for projection and collection.

## Notification WebSocket

The endpoint is `/api/v1/notifications` with subprotocol `a13n.service.notifications.v1`. It is a best-effort attachment, not a replacement for durable event pagination. Subscription schemas are included in the [Native contract asset](../assets/reference/service-openapi.json), even though WebSocket traffic is not an HTTP operation in OpenAPI.

Browser clients use the authorized session and origin. Application clients need a WebSocket implementation that can attach the authorization header. Never put credentials in a URL or subprotocol. The [TypeScript helper](sdks.md#stream-and-notification-lifetimes) sends the subscription handshake, acknowledges heartbeats, and exposes connection/gap state.

On a gap or reconnect, reconcile durable events and current resources. Changing subscriptions means closing the old attachment and opening a new one. A successful WebSocket connection does not grant authority beyond the credential's current boundary.

## AG-UI and A2A gateways

Control-capable processes expose protocol-specific routes in addition to Native HTTP:

| Boundary         | Routes / operations                                                                          |
| ---------------- | -------------------------------------------------------------------------------------------- |
| AG-UI            | `POST /ag-ui/v1/agents/{agent_id}/runs` and `/cancel`                                        |
| A2A discovery    | `/.well-known/agent-card.json`, Agent-scoped card and extended card                          |
| A2A messages     | `/a2a/v1/agents/{agent_id}/message:send` and `message:stream`                                |
| A2A tasks        | Task read/list, `:cancel`, `:subscribe`, and task push-notification configuration management |
| Provider ingress | `POST /connectivity/v1/accounts/{account_id}/events` on Connectivity-capable processes       |

A2A availability is controlled by `gateway.a2a_enabled`. These projections share Service execution ownership, but have protocol-specific identifiers, bodies, streaming, and error rules. The TypeScript `http` client targets Native `/api/v1` only; it is not a generic transport for these routes. Consult the running Service's `/api/openapi.json` for its HTTP schemas and use the corresponding protocol client.

Provider ingress uses the configured Application Account's adapter and authentication, not an arbitrary model prompt posted to a Native route. [External tools](external-tools.md) covers admission and routing. Worker-only processes do not expose these product gateways.

## Traces and usage

Telemetry export and trace-query access are separate. Export can be enabled for execution diagnostics without enabling the Service Trace Query API. Trace queries require a configured installed backend adapter. Default composition supplies the Run/IAM-backed authorizer, checking `trace.read`, owning Run visibility, and retained correlation records. Disabled data reads still return the shared unavailable error; the authenticated query descriptor remains available to report the disabled state.

A trace's correlation attributes are not permission evidence. Credentials for Langfuse or another backend do not by themselves authorize a Workspace user to query traces. Backend UI access has its own policy. Usage data describes observed consumption, not an invoice or a guarantee of final cost.

[Harness observation](../a13n-harness/observation.md) owns instrumentation hooks, while Service owns Workspace visibility, diagnostic access, and retained execution identity. [Configuration reference](configuration-reference.md) lists export/query settings; [HTTP contracts](http-contracts.md) explains request and error correlation.
