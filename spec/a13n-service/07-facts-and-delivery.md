# Facts, recovery state and delivery

## Design position

The Service produces three kinds of output, with different owners:

| Output                   | What it is                                                                                               | Authority                                          |
| ------------------------ | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| Immutable facts          | Usage attribution and receipts, audit events, revisions, sealed runs, finished attempts, settled entries | PostgreSQL rows that triggers freeze               |
| Current accounting       | Latest Context usage snapshots and their current contribution projections                                | PostgreSQL, independently of execution checkpoints |
| Resumable run state      | Checkpoint state and display objects                                                                     | Object bytes selected by run pointers              |
| Observations and notices | The thread stream, lifecycle webhooks, identity mail, trace queries                                      | Derived; never decides execution                   |

PostgreSQL owns lifecycle, execution authority, inbox disposition and the pointers that select each run's committed state and display. Objects hold immutable bytes. Redis carries provisional live output and worker wakeups; it never advances execution history or decides that a run is finished. The outbox delivers what a transaction committed, at least once.

## Immutable facts

Database triggers refuse changes to facts:

- `audit_events`, `agent_revisions` and `skill_revisions` refuse every update and delete; `grants` refuse updates ([03](03-tenancy.md)).
- `usage_records` refuse deletion and attribution changes. Legacy facts and provider receipts remain immutable; Context snapshots and their model observations follow the accounting replacement rules below.
- Sealed runs, finished attempts and settled entries follow the guards in [05](05-runs.md#tables); a sealed run's labels are its only editable property.

There is no lifecycle event log and no workspace-ordered lifecycle cursor. Run and attempt rows record each transition's result (timestamps, status, yield reason, failure), and a run's attempts are its execution history. Outbound notice uses [lifecycle webhooks](#lifecycle-webhooks); live observation uses [the thread stream](#the-thread-stream).

## Usage records

```
usage_records
  id  organization_id  workspace_id  run_id  run_attempt_id  harness_run_id  call_id NULL
  digest  record  model_id NULL  price_snapshot NULL  ingested_at
  PRIMARY KEY (id)          -- the stable Harness scope or contribution ID
```

Each attempt owns independent single-writer [Context usage snapshots](../a13n-harness/12-events-observability-and-usage.md#context-usage-snapshot). One row retains each scope's latest JSON snapshot, including its sequence; model and provider rows project that snapshot's current contributions for indexed queries. There are no per-record revision columns or revision history. Inline subagents retain independent scopes under the parent Service run and their own `harness_run_id`. A model contribution carries the `model_id` and `price_snapshot` selected at dispatch under its `call_id`; unknown dispatch correlation remains unattributed. Replacement preserves this attribution and the contribution's first `ingested_at`, and never reprices historical consumption.

**Snapshot ingestion** uses a short transaction independent of execution checkpoint publication. It serializes with the owning run and attempt, validates scope ownership and sequence through Harness, and atomically replaces the latest scope and its query projection. Equal delivery is idempotent; an older valid snapshot cannot roll back current accounting; changed identity or conflicting same-sequence facts fail. A failed write retains pending delivery for bounded attempt cleanup retry. Display chunks are not a second ingestion path.

Late ingestion is not lease-fenced: an expired or sealed attempt can still report consumption, but cannot move an execution checkpoint or change a sealed outcome. A takeover starts a fresh usage scope rather than letting old and new writers overwrite one scope. Their distinct contributions both remain counted. Stable provider receipts retain their first owner across scopes in the workspace; conflicting receipt facts are rejected. A native provider-suspended generation cannot continue across fresh scopes; Harness rejects that unsupported continuation before dispatch rather than recounting cumulative usage.

Individual record delivery remains append-only: checkpoint and terminal reconciliation insert each fact once, compare its digest, and log and skip oversized (over 65536 bytes) or conflicting records without overwriting the first fact. These records and current Context snapshots share the same initial schema; there is no historical migration chain or released-payload conversion.

`runs.usage_at_seal` is the total visible to the sealing transaction (`requests`, `input_tokens`, `output_tokens` of model records); it is not corrected by later arrivals. `max_usage.requests` counts model records ([05](05-runs.md#execute)).

`GET /usage` (`read`) sums model records per model, including late arrivals, and names each by its key in `model`: `requests`, input, output, cache-read and cache-write tokens, and `cost`, the sum of each record's own priced cost (null when no record of that model was priced). It filters by `run_id`, `thread_id`, `session_id`, `ingested_after` and `ingested_before`. `model` is null for unattributed records. A crash before a provider report reaches the Service can leave an unknown charge; ingestion preserves received facts, not discovery of every billable operation.

### Workspace usage analysis

The `usage` package owns the read API and aggregation over durable consumption. Run execution owns ingestion, current snapshots and contribution projections. Querying usage needs the Workspace's `read` permission and does not depend on a telemetry backend.

`GET /usage/overview`, `GET /usage/agents` and `GET /usage/models` require timezone-aware `start` and `end`, selecting the half-open interval `[start, end)`, at most 366 days. Consumption selects model contribution rows by their first `ingested_at`; Run metrics independently select Runs by `started_at`. The queries exclude non-model provider receipts and snapshot envelope rows. Later observations replace the contribution counted at its original ingestion time. No precomputed totals or background aggregation are required.

Model metrics are `requests`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_hit_rate`, `cost` and `unpriced_requests`. Cached input is a subset of input; the rate divides summed cached input by summed input, or is null with no input. `cost` is the recorded USD subtotal, serialized as a decimal string: zero for no requests, null when every request is unpriced, and the known subtotal otherwise. `unpriced_requests` counts null costs, including within a partially priced group. Recorded counters and costs do not establish complete provider billing coverage.

Run metrics are `runs` and `average_duration_seconds`. Each started Run counts once regardless of its request count. The average uses only Runs with `sealed_at`, subtracting `started_at`; it does not join successor Runs or attribute a Run's duration to a model. Runs with no model contributions still appear in Agent aggregates, and late contributions from Runs outside the window still appear in consumption.

The overview returns `usage`, `runs` and `daily`. `timezone` is an IANA timezone, default `UTC`, used only to group the daily consumption. Every intersecting local date is returned in chronological order, including zero-usage dates; boundary days include only the selected interval. Daylight-saving changes follow that timezone's calendar. Totals and daily consumption are computed in one database statement.

Agent rows carry `agent_id`, `name`, `usage` and `runs`; attribution uses the owning Service Run's Agent, without additional subagent rollups. Model rows carry `model` (the model key), `name` and `usage`; null model/name identify unattributed consumption. Both collections use the standard `items`, `next_cursor`, `limit` and query-bound `cursor` contract. Order is known cost descending, then Agent ID or model key ascending, with unknown cost last. Empty consumption on a Run-only Agent has known zero cost. Pagination is a live view; later consumption can change ordering, and reloading starts from the first page.

## Objects

The object store holds immutable bytes under owner-named keys: lowercase slash-separated segments of at most 1024 characters. Its contract, on both the local and S3 backends, is create-only write, read, prefix listing and delete. A write of identical bytes to an existing key succeeds, so an uncertain write is resolved by repeating it; different bytes are refused and never replace what is stored. There is no conditional replacement and no object catalog table. A committed reference names a key, digest and size, and a read that finds missing or different bytes is `unavailable` (dependency `objects`).

| Key                                      | Owner                                                                     |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| `orgs/{org}/runs/{run}/state/{digest}`   | A run's checkpoint state                                                  |
| `orgs/{org}/runs/{run}/display/{digest}` | A run's display                                                           |
| `orgs/{org}/uploads/{upload}`            | Upload bytes and their receipt ([04](04-resources.md#uploads-and-assets)) |
| `orgs/{org}/images/{owner}/{digest}`     | Organization, workspace and agent images ([03](03-tenancy.md#images))     |
| `users/{user}/images/{digest}`           | User avatars                                                              |

Publication writes the bytes outside any database session, then commits the reference. Paired state/display publication finishes both writes before reporting a failure, so failure sealing cannot race its still-running write. A failure between the two leaves an unused object.

**Run state and display cleanup is owner-driven.** Only a run's own attempts write under its prefix, and only its pointers make an object reachable:

1. A checkpoint transaction stages a `checkpoint_cleanup` outbox delivery for provably replaced objects alongside the pointer change. Rollback publishes neither the references nor the reclamation intent. Boundary acknowledgement and successor acceptance do not wait for deletion. State sequences and display positions advance monotonically; a replaced display at the same position is retained until seal because its digest may recur.
2. A takeover stages a scan of older attempts' state objects, keeping the captured committed reference and skipping state from the new or later attempts. It does not block execution or consume run attempts on deletion failure. Active display prefixes are not scanned: their unreferenced same-position bytes may still recur. Every seal stages a full prefix scan preserving its final references, including a display without a state checkpoint.
3. Each delivery has one total `objects.timeout` I/O budget. A scan lists at most 1000 keys in lexicographic order per claim. It records the last completed key and defers remaining work in a fenced outbox transaction; deferral returns its attempt. Failure retries the same page safely because deletion is idempotent. A deadline with no progress counts as failure. Interruption leaves durable work for another sender; exhausted retries become visible dead deliveries, not a guarantee of unlimited retries.
4. No attempt starts an object write within `objects.timeout` of its local lease deadline. A stale attempt cannot commit its late bytes. Referenced state is protected during takeover; final scans run only after seal has made the references immutable.

This adds at most one small outbox row per checkpoint that replaces a reclaimable object, plus one at takeover and one at seal. With C such checkpoints per second, one control replica's default batch admits up to 32 reclamation claims per second before I/O limits; additional pages and retries consume that capacity too. Backlog monitoring exposes when producers outrun deletion. No per-run scheduler or second in-memory queue coalesces or drops this work.

A run normally holds one state and one display object, plus replaced objects awaiting reclamation. Objects named by frozen pointers are never deleted: a completed or waiting run keeps its final pair because fork and continuation start from it, and a failed or cancelled run keeps its last pair as inspection evidence.

There is no other object reclamation: no age-based upload expiry, orphan inventory or physical purge of history, assets or images. Unused uploads and replaced images stay stored; upload limits still apply.

## Checkpoints and display

A **state object** holds the checkpoint format (currently 1), the Harness state, the checkpoint sequence, the attempt number that wrote it, whether the resume's optional input has been incorporated, and for a waiting run the Harness's deferred requests. `runs.checkpoint` points at it with `{digest, size, format, seq, attempt}`; `runs.display` points at the display with `{digest, size, format, position}`. A checkpoint of a newer format waits for a worker that reads it; an older one fails its run ([05](05-runs.md#claim-heartbeat-and-authority)). [05](05-runs.md#assignment-and-incorporation) owns incorporation and the checkpoint commit, which also stores the run's memory cursors in `runs.memory_cursors`, outside the state object ([11](11-memory.md#execution)).

A **display** is folded from the AG-UI events the worker streams, never reconstructed from message history. It is the ordered list of the run's items, the number of earliest items `dropped` over its item limit, and the stream position `{attempt}-{sequence}` it covers. Every checkpoint commit writes the whole display as a new object, so the durable view always describes exactly the restored history, and output after recovery continues it. Output streamed after the last checkpoint is provisional; a crash removes it from the durable view and the next attempt regenerates it.

```
Item
  id                 itm_…, stable across attempts for the same message, tool call or observation
  kind               text_message | reasoning_message | tool_call | observation
  state              in_progress | completed | interrupted | failed
  first_stream_id    position of the item's first event
  last_stream_id     position of its latest event
  started_at         time of its first event
  ended_at           time of the latest event that left it completed or failed; null otherwise
  content            the event fields the Console renders
```

- Times come from the AG-UI event's own timestamp, or the worker's clock for an event without one.
- A message text, tool arguments or tool result keeps at most 262144 characters and is marked `truncated`; the state keeps the full value. An observation larger than 32768 bytes keeps only its name.
- A tool result the model sees as a failure (a retry prompt or denial) leaves its item `failed` with `content.failure = {code: "tool_failed", message}`.
- A tool call is committed as `in_progress` before it executes, so evidence of an attempted side effect never disappears.
- Each Harness observation is one `observation` item, except a tool call's streamed arguments: the stream protocol reports each argument delta of a model response part as an `a13n.pydantic_ai.part_delta` observation and completes the call only at the part's end, and the consecutive argument deltas of one part fold into one item. It is the first delta's observation with `value.event.delta.args_delta` holding all their text, and the first delta's time.
- A display keeps at most 4096 items: the oldest are dropped first and counted in `dropped`. When the remaining items' serialized UTF-8 bytes exceed `worker.display_bytes`, the oldest items give up their content first, replaced by `{"omitted": true}`. The display is a view, and the state keeps every message: its limits never fail a run.
- An attempt that ends without finishing its items marks them `interrupted`; a later attempt continuing the same item returns it to `in_progress`.

A worker that seals a run failed or cancelled may write its in-memory display as one more object, with unfinished items interrupted. A run sealed by the sweep, interrupt, archive, a parent's cancel or recovery keeps its last committed display; readers show its `in_progress` items as `interrupted`. [05](05-runs.md#reads) owns `GET …/runs/{run}/items`.

## The thread stream

Each thread has one Redis stream, `a13n:thread:{thread}`, written only by workers. It carries the in-flight tail of the thread's output; the committed display holds everything before it. An attempt appends two kinds of entries: output **deltas** carrying the run ID, attempt number, per-attempt sequence, the AG-UI event and the item it changed; and a **boundary** marker after each checkpoint commit, carrying the sequence its display covers. Appends go through a bounded in-memory buffer (1024 entries) and a background writer, bounded by `redis.timeout`, so Redis latency never slows execution; a full buffer, a delta over 262144 bytes or a Redis failure drops output, which readers see as a gap. Nothing in the stream needs to survive: durable output is in display objects.

Model output arrives a token or a few at a time, so consecutive **fragments** are coalesced before they get a sequence. `TEXT_MESSAGE_CONTENT`, `REASONING_MESSAGE_CONTENT` and `TOOL_CALL_ARGS` events that differ only in `delta` and `timestamp` merge into one event with the concatenated `delta` and the first fragment's `timestamp`; streamed tool-call argument observations of one part that differ only in `args_delta`, `timestamp` and their source `sequence` and `occurred_at` merge the same way into the first one, with the concatenated `args_delta`. A merged event is released when another event arrives, `worker.stream_coalesce_seconds` after its first fragment, before a checkpoint commits, and when the attempt ends; its text stays within 8192 characters, and a window of 0 streams every fragment. Sequences stay dense, the boundary covers every event observed before it, and the display folded from merged events has the same items as one folded from their fragments, apart from the stream positions they record.

After appending a boundary the writer removes the entries before it (`XTRIM MINID`) that were appended more than `worker.stream_trim_seconds` earlier, measured by the boundary's own entry ID; with 0 it removes every entry before the boundary. The boundary itself stays. The stream therefore retains the entries after its latest boundary and those within the retention window before it; earlier entries may be removed at any time. As a backstop, `XADD` caps the stream at about `worker.stream_length` entries (coalesced output appends about ten entries a second), and the stream expires `worker.stream_ttl` seconds after its last append.

`GET /threads/{thread}/stream` serves one thread as Server-Sent Events (`Cache-Control: no-store`, `X-Accel-Buffering: no`). Opening it authorizes `read` before the response starts, so refusals are ordinary HTTP errors. Without `Last-Event-ID` the connection replays every retained entry; with one it continues after that entry, and an entry ID no longer retained starts with a `gap`. It then follows live entries. `Last-Event-ID` must be a stream entry ID, `{ms}-{seq}` with at most 20 digits in each part; any other value is `invalid_argument`. A comment line keeps the connection alive every 15 seconds. The connection holds no database session while idle.

| Frame (`event:`) | `data`                                                                | Client action                                                                    |
| ---------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `delta`          | `{run_id, attempt, sequence, event, item: {id, kind, state} \| null}` | Apply provisional output                                                         |
| `boundary`       | `{run_id, attempt, sequence}`                                         | The committed display now covers the attempt up to `sequence`                    |
| `changed`        | `{version}`                                                           | Re-read the thread (inbox, pointers, mounts, headers, labels or archive changed) |
| `reset`          | `{run_id}`                                                            | A newer attempt replaced the run's output: discard it and re-read its items      |
| `gap`            | `{run_id}`                                                            | Deltas were skipped: re-read the run's items; the next boundary covers the gap   |

Only `delta` and `boundary` frames carry an SSE `id:`, the Redis entry ID a client passes back as `Last-Event-ID`. Frames concern only the thread's current run; deltas of attempts older than the current attempt are dropped. A `gap` is sent on a per-attempt sequence discontinuity, a Redis failure, or a connection that falls more than 1024 entries behind. A discontinuity is a delta that does not follow the connection's last sequence of its attempt, or a boundary beyond it; a boundary then sets that sequence, so deltas continue from it without another gap. A connection whose `Last-Event-ID` was removed, that starts after removed entries of the current attempt, or whose shared read is overtaken by trimming therefore receives `gap` before the attempt's next delta or boundary, and recovers from the display. A connection resuming exactly at a retained boundary continues without a gap. A Redis failure while the connection starts or replays sends `gap` and ends the connection; one while following live entries sends `gap` and the connection continues.

All connections of a process share one blocking `XREAD`. Every `control.stream_refresh_seconds` the process runs one snapshot query for all watched threads (version, current run and its latest attempt) and one authority check per distinct credential and workspace. A thread version change produces `changed`, a newer attempt produces `reset`, and lost read access ends the stream. Changes made by other callers therefore appear within one refresh interval; a client's own operations return their results directly. A delta already in flight when authority changes can still be shown; `reset` corrects it. There is no run-level, session-level or workspace-level stream: a thread has at most one active run, and a child thread is discovered from its parent's output.

`proto/a13n-service/thread-stream.schema.json` exports each frame's `data` schema by event name ([10](10-api.md#exported-contracts)).

## Outbox

```
outbox   (obx_)
  id  organization_id NULL  workspace_id NULL  kind  dedupe_key  target  payload  subscription_id NULL
  status  available_at  attempts  lease_owner NULL  lease_token_hash NULL  lease_expires_at NULL
  last_error NULL  created_at  delivered_at NULL  settled_at NULL
  kind IN ('webhook', 'child_result', 'email', 'memory_purge', 'checkpoint_cleanup')
  status IN ('pending', 'delivered', 'dead')
  CHECK (organization_id IS NOT NULL OR kind = 'email')
  CHECK ((status = 'delivered') = (delivered_at IS NOT NULL))
  CHECK ((status <> 'pending') = (settled_at IS NOT NULL))
  UNIQUE (kind, dedupe_key)
```

The outbox is durable at-least-once delivery of what a transaction committed. The five kinds are closed by the CHECK; it is not a general job system. Only account mail has no organization. `target` and `payload` hold everything delivery needs; `subscription_id` is diagnostic provenance, not a foreign key.

**Staging** happens in the transaction of the change that needs the delivery. A row without an explicit dedupe key uses its own ID. A row keyed by a durable fact, such as one sealed child run, is staged at most once. A delivery that can never be sent is staged already `dead` with its reason.

**Delivery** is the `deliver_outbox` sweep ([09](09-runtime.md#sweeps)), every `control.scan_seconds`. Kinds are delivered side by side. Within a kind, a pass claims up to the kind's `batch` due pending rows whose lease is absent or expired, up to its `parallel` at a time with `SKIP LOCKED`; each claim uses an attempt (it increments `attempts`), records a fresh token and holds a lease of the kind's `lease_seconds` from database time. A due row that has already used the kind's `max_attempts` attempts is not claimed again: its last claim lapsed without settling, and the row becomes `dead` with `last_error = unsettled`. No batch starts after one lease has passed, so a pass ends within two leases.

Handlers run outside any database session for external I/O, within half their claim lease, and settle their own claim: `delivered`, `dead`, a retry at a later time, or `deferred`, which makes the row due again later and gives back the attempt its claim used, for a row that cannot be delivered yet through no fault of the delivery. Settlement applies only while the row is pending and the claim's token still matches, so a sender whose lease lapsed changes nothing. `last_error` keeps at most 1024 characters. A handler that raises leaves the retry policy to the outbox, which has one owner:

- the row retries after min(3600, 2^min(attempts, 16)) seconds multiplied by uniform jitter in [0.8, 1];
- once it has used the kind's `max_attempts` attempts it becomes `dead`;
- `Undelivered(reason)` keeps its secret-free reason as `last_error`; any other exception keeps only its type name.

An unsettled claim becomes claimable again when its lease expires, with its attempt used, so a handler that keeps being cancelled or whose process dies ends `dead`. Successful and dead outcomes set `settled_at` from database time; a delivery staged dead also has this timestamp. Redelivery clears it when returning a dead webhook to pending.

**Policy and retention.** `outbox.defaults` applies to every kind; `outbox.by_kind.<kind>` overrides only explicitly supplied fields. Startup resolves and validates one immutable policy per kind, rejecting unknown kinds, fields and invalid combinations. There are no implicit per-kind defaults. The shared policy defaults are `batch = 32`, `parallel = 8`, `lease_seconds = 60`, `max_attempts = 12`, `delivered_retention_seconds = 86400` and `dead_retention_seconds = 1209600` (one and fourteen days). The handler's own operation timeout must fit within half its kind's lease.

`purge_outbox` runs every `outbox.purge_interval_seconds` (60 by default), deleting expired successful and dead rows by their **settlement time**, using each kind's retention. It repeats short transactions of at most `outbox.purge_batch` rows (1000) within one shared `outbox.purge_budget_seconds` budget (5 seconds). Pending rows are never deleted by age or queue depth. Retention controls settled history; it cannot strictly cap a table whose producers continuously outrun consumers. [12](12-observability.md#metrics) owns sustained backlog and dead-delivery signals. PostgreSQL autovacuum reuses deleted space; the Service schedules no table rewrite or `VACUUM FULL`.

| Kind           | Staged by                                                                          | Handler                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| -------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `webhook`      | Every run and attempt transition ([lifecycle webhooks](#lifecycle-webhooks))       | One signed POST, then settle delivered; otherwise `Undelivered`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `child_result` | A child thread's completed, failed or cancelled seal, keyed by the run             | Locks the parent thread; appends the result entry and settles in one commit ([05](05-runs.md#child-runs)). A missing parent is dead (`invalid_target`); an archived one is delivered and ignored (`thread_archived`); a full inbox defers the row for 30 seconds (`inbox_full`). A result is its output, at most `worker.output_bytes`, and the envelope naming it; settings keep `worker.output_bytes` plus 65536 bytes for the envelope below `control.inbox_bytes` (2 MiB by default, [09](09-runtime.md#settings)), so a result always fits an empty inbox and a deferral only waits for room |
| `email`        | Identity flows that send a one-use link ([03](03-tenancy.md#identity-mail))        | Sends over SMTP, then settles delivered; a failure raises                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `memory_purge` | Deleting a record memory, keyed by the memory ([11](11-memory.md#namespace-purge)) | Opens the provider's store on the namespace and purges it, then settles delivered; a store error raises `Undelivered` with its code, and an unregistered provider type is dead (`type_unavailable`)                                                                                                                                                                                                                                                                                                                                                                                               |

| `checkpoint_cleanup` | Checkpoint replacement, takeover and seal | Deletes retired references or scans orphan objects as described in [object reclamation](#objects); continued scans persist progress and defer, failures use the shared retry policy. |

Identity mail is staged only when `auth.mail.smtp_host` is set; otherwise the flow logs that mail is not configured and stages nothing. The mail content is encrypted for its outbox row and never logged.

## Lifecycle webhooks

A [subscription](04-resources.md#subscriptions) selects lifecycle kinds and optionally filters by `agent_id`, `session_id` and `thread_id`. Each transition stages its kinds as the last phase of its own transaction, so a transition and its deliveries commit together:

| Transition                                                        | Kinds                                                                                      |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Acceptance                                                        | `run.accepted`                                                                             |
| Claim                                                             | `run.running`, `run_attempt.leased`                                                        |
| The Harness run starts                                            | `run_attempt.running`                                                                      |
| Seal of a running run                                             | `run.{status}`, `run_attempt.{succeeded \| failed \| cancelled}`; waiting uses `succeeded` |
| Seal of an accepted run (interrupt, archive or a parent's cancel) | `run.cancelled`                                                                            |
| Recovery or handoff                                               | `run.accepted`, `run_attempt.{failed \| yielded}`                                          |

Staging matches the workspace's enabled subscriptions in ID order, up to `control.subscriptions` of them (more are logged and skipped), and inserts one outbox row per matching subscription and kind. The subscription read at that point is the selection point: a later edit or deletion affects later transitions, never staged deliveries. The row ID is the delivery ID. The row copies the URL and the signing secret, re-encrypted for that row; a secret that cannot be decrypted stages the delivery dead with `signing_secret_unavailable`, so it never fails the transition.

The payload describes the transition, never secrets:

```
{id, type, occurred_at, workspace_id,
 run: {id, session_id, thread_id, agent_id, agent_revision_id, status, trigger, wait_reason, failure},
 attempt: {id, number, status, start_reason, yield_reason, failure} | null}
```

The sender POSTs the payload as compact JSON with sorted keys and these headers:

- `X-A13n-Delivery-Id`: the delivery ID;
- `X-A13n-Webhook-Timestamp`: Unix seconds;
- `X-A13n-Webhook-Signature`: `v1=` and the hex HMAC-SHA256 of `"<timestamp>.<delivery id>.<body>"` with the subscription's signing secret.

The endpoint policy ([08](08-providers.md#outbound-endpoint-policy)) is checked on every attempt, redirects are not followed, the response body is never read, and `control.webhook_timeout` bounds the whole POST. A 2xx status is delivered; anything else is `Undelivered` with `HTTP {status}` or the error's type name, and the outbox retries it. A retry after an uncertain result can deliver twice and deliveries are unordered, so receivers deduplicate by delivery ID and read the run when they need current state.

`GET …/subscriptions/{id}/deliveries` lists a subscription's deliveries newest first with status, attempts, next attempt time, last error and payload. `POST …/deliveries/{delivery}/redeliver` returns a dead delivery to pending, due now with a fresh attempt budget and the same delivery ID; a delivery that is not dead is `conflict` (`not_dead`), and one staged without a secret is `conflict` (`signing_secret_unavailable`). Both need workspace `admin`; redelivery is audited as `webhook_delivery.redeliver`.

## Trace query

The deployment's one trace backend ([08](08-providers.md#trace-backends)) receives every attempt's Harness spans and answers trace queries. Queries select spans by the correlation attributes every exported span carries ([12](12-observability.md#traces)).

| Route (`read`)                              | Returns                                                            |
| ------------------------------------------- | ------------------------------------------------------------------ |
| `GET …/runs/{run}/attempts/{attempt}/trace` | The attempt's spans, including its inline child runs               |
| `GET …/traces`                              | Trace root spans, one per attempt                                  |
| `GET …/traces/{trace}`                      | The trace's root span                                              |
| `GET …/traces/{trace}/spans`                | The trace's spans                                                  |
| `GET …/trace-backend`                       | `{type, queryable_since}`, both null when no backend is configured |

- An attempt's spans are searched from one minute before the attempt was created to one minute after it finished (or now).
- A trace listing filters by `session_id`, `thread_id` and `run_id` (each an [object ID](02-layout.md#object-ids)) and up to eight `attribute=key:value` selectors that match root-span attributes exactly. A selector splits at its first colon; its key is at most 128 characters and its value at most 256; it may not name the `a13n.` namespace, a key or value that redaction would hide, or a key twice. The window defaults to the last day and spans at most 31 days; otherwise `invalid_argument` on `started_after`.
- A trace ID is 32 lowercase hex characters, found within the last 31 days (`queryable_since`); `GET …/traces/{trace}` of an unknown trace is `not_found`, and its span listing is empty.

Scope is authorized before any supplied ID is used, and the database session closes before the backend is called. Spans keep the backend's native order; the Service never re-sorts a page. The backend is never trusted for tenancy: every returned span is checked against every attribute the query selected, comparing a string attribute as is and a number or boolean as its JSON text, as the backends match them; any other value never matches. A page can therefore hold fewer items than its limit while `next_cursor` continues. Credentials are redacted from each span's status message, input, output, attributes, resource attributes, and event and link attributes. A cursor carries the backend's position and the first page's resolved window, and is bound to the whole query ([10](10-api.md#collections)), the window parameters as given included, so a changed one is `invalid_cursor`; later pages keep the first page's resolved window. No configured backend is `unavailable` with dependency `trace`; a backend failure is `unavailable` with dependency `trace:{type}`.

## Invariants

- A fact row is never updated or deleted, and a usage record ID never changes meaning.
- A run's state and display objects are reachable only through its pointers, and objects named by frozen pointers are never deleted.
- The display committed with a checkpoint covers exactly the stream up to its position.
- The thread stream never changes durable state; a client that follows its control frames converges on the durable view.
- A thread-stream connection that misses deltas of the current attempt receives `gap` before any later delta or boundary of that attempt.
- A transition and its webhook deliveries commit together; a delivery is settled only by its current claim.
- A pending outbox row is never purged.
- Every span a trace query returns carries the caller's organization and workspace correlation.
