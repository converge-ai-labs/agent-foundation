# Native Streaming and Notifications

## Design Position

Service Native clients use three deliberately different delivery surfaces:

- Run SSE carries detailed ordered interaction observations for one Run and supports bounded replay with the Run Stream cursor;
- the Workspace event collection reads durable lifecycle facts with its own relational cursor; and
- one WebSocket notification endpoint delivers lightweight best-effort wake-ups for explicitly subscribed resources.

These surfaces do not share an envelope, cursor, replay promise, or authority. There is no combined Workspace SSE/WebSocket stream and no WebSocket variant of the detailed Run stream.

## Boundaries

| Concern                                                 | Owner                                                                      |
| ------------------------------------------------------- | -------------------------------------------------------------------------- |
| Run Stream entries, Redis cursor, and retained snapshot | [Lifecycle and Stream Persistence](24-lifecycle-and-stream-persistence.md) |
| Durable lifecycle facts and retention floor             | [Lifecycle and Stream Persistence](24-lifecycle-and-stream-persistence.md) |
| Resource and lifecycle collection authorization         | [Management API](16-management-api.md) and the owning domain               |
| HTTP and streaming resource safety                      | [HTTP ingress](05-http-ingress-and-request-contract.md)                    |
| SSE framing and Native notification WebSocket           | This document                                                              |

A transport cursor or notification identity grants no resource access. Every attachment authenticates and authorizes the selected resource under current [IAM](33-identity-and-access-management.md).

Run SSE authorizes `run.read`; Workspace and resource lifecycle collections authorize `lifecycle_event.read` plus current resource-read authority; and each WebSocket subscription change authorizes `notification.subscribe` plus every selected resource's read action. These actions are registered centrally by [IAM](33-identity-and-access-management.md#stable-action-registry). A connection opened under an earlier allow does not preserve authority for a later subscribe frame or stream continuation.

## Run SSE

```http
GET /api/v1/runs/{run_id}/stream
Accept: text/event-stream
Last-Event-ID: <run-stream-cursor>
```

`Last-Event-ID` is optional. When present, it names the last completely applied Run Stream entry and replay begins exclusively after it. A client does not place the cursor in an authorization header, query filter, or Service resource ID.

The client owns this consumption checkpoint. It records the `id` only after its local processing completely applies the event and sends the recorded value as `Last-Event-ID` on reconnect. A client that needs recovery across process or device restarts persists the checkpoint outside the SSE connection; merely receiving an event does not advance it.

Each data event uses canonical SSE framing:

```text
id: <run-stream-cursor>
event: <event_type>
data: <one-line JSON RunStreamEvent>

```

`id` is the Redis Stream entry ID retained in the Run replay snapshot. `event` equals the bounded `event_type` carried by `data`. `data` is the complete versioned `RunStreamEvent` owned by the persistence contract. JSON is encoded on one UTF-8 line; clients ignore unknown additive object fields but do not guess unknown required schema versions.

The service sends SSE comments as heartbeats. A heartbeat carries no `id`, does not advance replay, and is not a Run observation. The server can close a healthy connection at its configured maximum lifetime; clients reconnect with the last applied event ID.

### Replay and Live Cutover

The attachment establishes one high watermark after authorization, returns the authorized retained or live entries through that watermark in order, and then subscribes after the same boundary. An entry is neither skipped nor delivered twice by the replay-to-live cutover. Duplicate delivery after a client loses an acknowledgement remains possible, so clients deduplicate by cursor or stable event identity.

If `Last-Event-ID` is covered by the live Redis prefix or complete immutable snapshot, replay continues from that source. If the requested prefix is no longer available and no complete snapshot bridges it, the route returns `409 run_stream_replay_gap` before opening SSE when the gap is known during attachment. A gap discovered after the response starts emits one terminal `a13n.service.replay_gap` event without a replay-advancing `id` and closes the attachment. Its bounded data identifies the Run, requested cursor, available floor, and current high watermark; it contains no missing content.

The client reconciles a gap through current Run, Item, and pending-action reads. It never treats the first surviving stream event as complete history.

A sealed Run stream closes after its final retained observation is delivered. The terminal stream observation reports a projection of the authoritative sealed Run outcome; closing the connection alone does not prove completion.

## Workspace Lifecycle Event Collection

```http
GET /api/v1/workspaces/{workspace_id}/events?limit=50&cursor=<opaque>
```

This is a bounded JSON collection, not an SSE endpoint. It returns authorized durable lifecycle and management facts in ascending relational sequence order:

```python
class WorkspaceEventPage:
    items: tuple[LifecycleEvent, ...]
    next_cursor: str | None
    retained_floor: str
    high_watermark: str
```

The schema is a conceptual wire shape. The cursor binds the Workspace, Principal scope, filters, and last returned lifecycle sequence. A cursor below the retained floor returns `409 lifecycle_replay_gap` with the safe current floor and high watermark. Resource filters never mix detailed Run token, reasoning, tool-argument, or message deltas into this collection.

Pagination accepts only the opaque starting `cursor` and bounded `limit`; there is no end-cursor parameter. The client continues with `next_cursor` and stops at its desired checkpoint or the reported high watermark.

Clients use this collection to reconcile background lifecycle and management changes after a notification disconnect. It does not replace resource reads or the detailed Run SSE.

## Resource Lifecycle Event Collections

Webhook consumers recover a resource-local sequence gap through the owning resource collection:

```http
GET /api/v1/runs/{run_id}/events?after_resource_seq=10&limit=50
GET /api/v1/run-attempts/{run_attempt_id}/events?after_resource_seq=10&limit=50
```

The response is ordered by the positive contiguous `resource_seq` owned by the lifecycle-event contract:

```python
class ResourceLifecycleEventPage:
    resource_type: Literal["run", "run_attempt"]
    resource_id: str
    items: tuple[LifecycleEvent, ...]
    next_resource_seq: int
    retained_resource_seq_floor: int
    high_watermark_resource_seq: int
```

`after_resource_seq` is the last resource event durably applied by the caller; `0` begins before the first event when that history remains retained. Each page contains every authorized retained lifecycle event after that value through its bounded page limit. The caller continues with `next_resource_seq` until it reaches its desired recovery target, or the reported `high_watermark_resource_seq` for general catch-up. Every `LifecycleEvent` item includes the stable source event identity, resource identity, `resource_seq`, resource version, event type, and bounded payload. Hook-name filters do not remove events from this recovery collection: a consumer may ignore an event for its business projection only after advancing through its `resource_seq`.

The request accepts `after_resource_seq` plus bounded `limit`; it does not accept an end sequence. For Webhook gap recovery, the received `resource_seq` is the caller's local target, and the caller stops paging after it has consumed through that sequence.

If `after_resource_seq + 1 < retained_resource_seq_floor`, the requested next event is no longer retained. The API returns `409 lifecycle_resource_replay_gap` with the floor, high watermark, and an authorized current-resource link. The caller then reboots its projection from current resource state and records that historical event-complete recovery was not possible. A resource sequence, resource ID, or response link grants no authority; every page reauthorizes the resource.

## Notification WebSocket

```http
GET /api/v1/notifications
Upgrade: websocket
Sec-WebSocket-Protocol: a13n.service.notifications.v1
```

The endpoint accepts exactly the `a13n.service.notifications.v1` subprotocol. Authentication completes before upgrade. A connection begins with no resource subscription and therefore receives no product notification until the client subscribes.

Client and server data frames are UTF-8 JSON objects with a required `type`. Unknown frame types or fields are protocol errors.

### Subscription Frames

```python
class NotificationSubscription:
    subscription_id: str
    scope: Literal["thread", "workspace"]
    resource_id: str
    topics: tuple[NotificationTopic, ...]


class SubscribeFrame:
    type: Literal["subscribe"]
    request_id: str
    subscriptions: tuple[NotificationSubscription, ...]


class UnsubscribeFrame:
    type: Literal["unsubscribe"]
    request_id: str
    subscription_ids: tuple[str, ...]
```

The schemas are wire contracts. A `thread` subscription names a Thread; the server resolves its Workspace. A `workspace` subscription names a Workspace. There is no Organization-, deployment-, or implicit all-Workspace subscription.

One subscribe frame is atomic. The server validates limits and authorizes every subscription and topic before changing connection state. If one entry fails, the whole frame fails and prior subscriptions remain unchanged. A successful `subscribed` or `unsubscribed` frame echoes `request_id` and the resulting subscription IDs. A rejected request returns an `error` frame with a stable safe code and leaves subscription state unchanged.

The stable topic registry is:

| Topic                    | Thread scope | Workspace scope | Meaning                                                              |
| ------------------------ | -----------: | --------------: | -------------------------------------------------------------------- |
| `thread.updated`         |          Yes |             Yes | Thread version, current Run, or head may have changed                |
| `run.updated`            |          Yes |             Yes | A correlated Run lifecycle or summary may have changed               |
| `pending_action.updated` |          Yes |             Yes | Authorized waiting-Run pending projection may require reconciliation |
| `session.updated`        |           No |             Yes | Session list or summary may have changed                             |

Adding a topic is additive. Clients ignore an unknown notification topic only after negotiating a profile that permits additive topics; they never interpret it as a known state transition.

### Notification Envelope

```python
class NotificationFrame:
    type: Literal["notification"]
    schema_version: Literal["1"]
    notification_id: str
    subscription_id: str
    topic: NotificationTopic
    workspace_id: str
    resource_type: str
    resource_id: str
    resource_version: int | None
    session_id: str | None
    thread_id: str | None
    run_id: str | None
    occurred_at: datetime
```

The notification is a best-effort wake-up. It carries no prompt, message text, reasoning, tool arguments or results, pending response schema, Artifact content, Secret, credential, or internal execution identity. It is not acknowledged, persisted for the client, replayed, or ordered across subscriptions. Duplicate, coalesced, delayed, and dropped notifications are all permitted.

After any disconnect, the client resubscribes and reconciles through Workspace events and current resource reads. `notification_id` supports diagnostics and local duplicate suppression only; it is not a cursor.

### Heartbeat, Limits, and Close

The server emits an application `heartbeat` frame containing an opaque nonce and timestamp. The client returns `heartbeat_ack` with the same nonce within the declared interval. Heartbeats carry no product state. Missing acknowledgement closes the connection.

The connection has bounded frame size, subscriptions, topics, queue depth, idle interval, and lifetime. Overflow closes only that connection. Standard close codes have these meanings:

|   Code | Meaning                                                     |
| -----: | ----------------------------------------------------------- |
| `1000` | Normal client or server close                               |
| `1001` | Service drain or bounded connection replacement             |
| `1008` | Authentication, authorization, or protocol-policy violation |
| `1009` | Frame or subscription limit exceeded                        |
| `1011` | Required notification dependency failed                     |

Authentication failure known before upgrade returns the ordinary HTTP `401` and does not create a WebSocket.

## Failure Semantics

| Failure                                    | Client action                                                  | Run consequence |
| ------------------------------------------ | -------------------------------------------------------------- | --------------- |
| SSE cursor is outside retained history     | Read current resources and reattach from an available boundary | None            |
| SSE delivery disconnects                   | Reconnect with last fully applied event ID                     | None            |
| Workspace lifecycle cursor expires         | Reconcile current resources and restart at returned floor      | None            |
| Resource lifecycle predecessor expires     | Rebootstrap current resource state at the returned boundary    | None            |
| Notification connection drops or overflows | Reconnect, resubscribe, and reconcile                          | None            |
| Subscription authorization is revoked      | Subscription is denied or removed; safe error/close follows    | None            |
| Service drains                             | Reconnect to another ready replica                             | None            |

## Compatibility and Invariants

SSE event schemas and Run Stream cursor compatibility belong to the Run Stream owner. Workspace lifecycle cursor compatibility belongs to the lifecycle event owner. Resource lifecycle API compatibility includes the resource-sequence domain, contiguity, and explicit retention-gap response. `a13n.service.notifications.v1` versions the WebSocket frame contract; breaking frame or subscription changes require another subprotocol.

1. Detailed Run observations use SSE only.
2. Durable Workspace lifecycle replay uses a bounded JSON collection only.
3. Resource lifecycle recovery is ordered by `resource_seq` and never infers a missing event from the Workspace cursor.
4. Native WebSocket notifications are best effort and have no cursor or replay.
5. A notification contains wake-up metadata, never detailed interaction content or a domain command.
6. A new WebSocket connection has no subscriptions.
7. Every subscription is explicitly authorized, bounded, and removed on disconnect.
8. Disconnecting any Native transport never cancels or seals a Run.
