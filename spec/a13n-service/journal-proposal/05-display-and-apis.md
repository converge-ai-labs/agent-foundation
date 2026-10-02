# Display, reading APIs, and live delivery

> Discussion proposal, not the current specification. See [status and scope](README.md).

## Display is an event projection

Today's folded Display updates an item repeatedly as tool arguments stream, the tool runs, and its result arrives. It saves the resulting collection as a bounded whole. The proposal changes the product representation to independently renderable event entries.

For example:

```json
[
  {
    "seq": 41,
    "kind": "tool.started",
    "data": {
      "call_id": "call_example",
      "name": "shell",
      "arguments": {"command": "pwd"},
      "status": "started"
    }
  },
  {
    "seq": 42,
    "kind": "tool.completed",
    "data": {
      "call_id": "call_example",
      "name": "shell",
      "arguments": {"command": "pwd"},
      "status": "completed",
      "result": {"text": "/workspace\n", "exit_code": 0}
    }
  }
]
```

Loading only event 42 is enough to show which tool completed, with which arguments, and what it returned. Repeating bounded identifying information is intentional. Large arguments/results use immutable references rather than repeating large byte arrays. No state replay or lookup of event 41 is necessary.

The client may visually group related entries that it already has by `call_id`, but the persisted unit remains the event. A request for event 41 shows that historical fact, not an inferred claim that the tool is still running now. The initial API does not promise “one latest card per tool across an arbitrarily selected history window,” which would need additional grouping work. No persistent latest-card index is introduced.

Use the same principle for messages, compaction summaries, child execution, and requested configuration changes. An event's renderer returns either no item or one self-contained item, which may contain several bounded content parts. That keeps pagination tied to event positions. Do not render both a tool-outcome event and the corresponding internal model-history insertion as duplicate user-facing tool results.

## Proposed read surface

Retain the familiar Run `items` resource, replacing its whole-Display response with pagination. The proposed routes, within the existing workspace route prefix, are:

| Route                            | Purpose                                                                                    |
| -------------------------------- | ------------------------------------------------------------------------------------------ |
| `GET /runs/{run}/items`          | Display items projected from the Run's events                                              |
| `GET /runs/{run}/events`         | Authorized semantic events for inspection                                                  |
| `GET /runs/{run}/state?at_seq=T` | Persisted execution state reconstructed through Run event T                                |
| `GET /sessions/{session}/events` | Ordered control and execution events across the Session, optionally scoped to Threads/Runs |

The Session events route may offer the same Display projection for a combined view. `seq` always means a position in one Run stream; Session queries use `session_seq`. Avoid a parameter that silently changes its numbering system with a filter.

For event and Display collections:

- `direction=forward|backward` selects traversal order.
- `after` and `before` are exclusive sequence bounds in the route's numbering system; together they select a middle interval.
- `limit` caps returned entries and `max_bytes` caps the response payload, within server maximums.
- The first response fixes an `upper_seq` from committed history. An opaque `next_cursor` binds that upper bound, route, filters, direction, and scan position for later pages.

For example, `after=149&before=181&direction=forward&limit=20` asks for the first 20 visible entries among events 150–180. A backwards first page with no bounds asks for the newest committed entries. Another first-page request is required to include events committed after the fixed upper bound.

```json
{
  "items": [
    {"event_id": "evt_example_1042", "seq": 42, "kind": "tool.completed", "data": {"name": "shell", "result": {"text": "/workspace\n"}}}
  ],
  "upper_seq": 100,
  "next_cursor": "opaque-cursor",
  "execution_complete": false
}
```

This response abbreviates item data. `execution_complete` describes the Run's terminal state; it does not mean every future late fee has already arrived. Display history has no global item cap or `dropped` prefix. There are only per-event, per-page, and per-request work limits.

An event filter can require scanning records that produce no item. Advance the cursor by scanned position, not just the last rendered entry, and permit a short or empty page with a continuation cursor when a work limit is reached. Never loop indefinitely to fill `limit`. An individually large content part returns a bounded preview and an authorized content reference, rather than forcing the client to download an entire result.

Run reads locate matching chunks by sequence range and read only those chunks plus the relevant recent PG tail. Session reads merge streams by committed Session position; sparse overlaps may require extra chunk reads. Bound that fanout, expose continuation when needed, and measure it separately from the common single-Run read. Neither read constructs a whole-Session Display in memory.

## Authorization and content boundaries

All routes enforce the existing workspace, Session, Thread, and Run authorization rules. Opaque cursors bind the requested scope and are not bearer authority. Recheck read access on every request and during long-lived live delivery.

Display serialization includes only the content intended for user presentation. Model-only supplemental content, private continuation data, and raw state must not be included in an ordinary Display response merely because the journal can carry them. The proposed explicit events/state inspection routes require workspace `read`, scoped to the requested Run or Session, and intentionally expose the persisted execution evidence to that authorized reader. This adds no separate permission framework; it must be documented as inspection access and tested separately from Display serialization. Credentials and secret configuration remain excluded from persistence or protected behind existing resource references. Referenced content uses authorized Service reads or appropriately scoped object access, not an unrestricted raw storage key.

## Live output and durable catch-up

Keep token streaming provisional. A Redis-backed live channel can carry current Attempt fragments for responsiveness, while aggregated events commit to PostgreSQL. Their identifiers and acknowledgements are distinct:

| Delivery                           | Identity                             | Recovery source                                  |
| ---------------------------------- | ------------------------------------ | ------------------------------------------------ |
| Provisional text/argument fragment | Attempt plus live transport position | May be dropped; not durable history              |
| Committed semantic event           | Stable event ID and stream sequence  | Unified PG/S3 journal reader                     |
| Wakeup/commit hint                 | Run plus committed head position     | Hint only; client confirms through durable reads |

A committed aggregate supersedes the corresponding local provisional bubble. On reconnect, the client pages events after its last durable position and deduplicates stable event IDs. A Redis gap, trimming, or Redis restart does not require a new Display snapshot; the client catches up from PG/S3 immediately, without waiting for checkpoint generation or archival.

An Attempt change discards only provisional output from the superseded Attempt. Committed earlier events remain visible. The live handler holds no database session across its stream wait. Periodic durable-head checks cover missed wakeups; Redis availability never decides whether a Run has completed.

This is a breaking Service transport change. The generic Harness may still emit its supported streaming protocol, but the Service adapter, exported stream schema, Console, and SDK consumers must agree on the new durable-event/catch-up contract. Do not layer it onto the old snapshot/fold reset behavior through a compatibility branch.

## Cancellation and interruption

Cancellation appends a committed terminal event and any available bounded interrupted aggregate. It does not upload a final Display that overwrites a chain of earlier Display patches. A crashed worker's uncommitted fragments may be lost; earlier committed history is retained.

For example, a committed `tool.started` followed by `run.cancelled` means the interaction was interrupted unless a committed outcome says otherwise. A displayed terminal/interruption entry includes its own reason and bounded affected call references where useful. It need not rewrite the old start event. Run metadata provides terminal status when a client loads only an earlier historical page.

## Configuration and mount events

Separate intent from actual execution selection:

| Event                                      | Meaning                                                                           |
| ------------------------------------------ | --------------------------------------------------------------------------------- |
| Session control: `configuration.requested` | A user requested an agent/model/configuration for a subsequent Run                |
| Session control: `mount.changed`           | Desired Thread mounts changed for a subsequent Run                                |
| Run: `run.accepted`                        | Records the actual agent revision, model, options, and mounts frozen for this Run |
| Run: `run.started`                         | Execution began under those accepted bindings                                     |

These events carry enough permitted names, identities, and effective scope to explain the change without looking up a mutable current configuration. A later rename does not rewrite what the historical event said. Secrets are never copied into the journal. Changing desired configuration while a Run is waiting does not change the waiting Run's selection; it affects a later Run.

The same protocol covers user operations and execution events, but they retain different authorities. A logged request does not itself authorize a worker to use a new model or mount.
