# Facts, recovery state and delivery

## Design position

The Service produces three kinds of output, with different owners:

| Output                   | What it is                                                                                               | Authority                                          |
| ------------------------ | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| Immutable facts          | Usage attribution and receipts, audit events, revisions, sealed runs, finished attempts, settled entries | PostgreSQL rows that triggers freeze               |
| Current accounting       | Current usage contributions and bounded scope progress                                                   | PostgreSQL, independently of execution checkpoints |
| Resumable run state      | Checkpoint state and display objects                                                                     | Object bytes selected by run pointers              |
| Observations and notices | The thread stream, lifecycle webhooks, identity mail, trace queries                                      | Derived; never decides execution                   |

PostgreSQL owns lifecycle, execution authority, inbox disposition and the pointers that select each run's committed state and display. Objects hold immutable bytes. Redis carries provisional live output and worker wakeups; it never advances execution history or decides that a run is finished. The outbox delivers what a transaction committed, at least once.

## Immutable facts

Database triggers refuse changes to facts:

- `audit_events`, `agent_revisions`, `skill_revisions` and `uploads` refuse every update and delete; `grants` refuse updates ([03](03-tenancy.md)).
- `usage_records` refuse deletion and attribution changes. Individual facts and provider receipts are immutable; scope progress and model observations follow the accounting replacement rules below.
- Sealed runs, finished attempts and settled entries follow the guards in [05](05-runs.md#tables); a sealed run's labels are its only editable property.

There is no lifecycle event log and no workspace-ordered lifecycle cursor. Run and attempt rows record each transition's result (timestamps, status, yield reason, failure), and a run's attempts are its execution history. Outbound notice uses [lifecycle webhooks](#lifecycle-webhooks); live observation uses [the thread stream](#the-thread-stream).

## Usage records

```
usage_records
  id  organization_id  workspace_id  run_id  run_attempt_id  harness_run_id  call_id NULL
  digest  record  model_id NULL  price_snapshot NULL  ingested_at
  PRIMARY KEY (id)          -- the stable Harness scope or contribution ID
```

Each attempt owns independent single-writer [usage scopes](../a13n-harness/12-events-observability-and-usage.md#reporting-boundary). Model and provider rows retain current contributions for indexed queries. One `kind: "cursor"` row per scope retains its bounded identity, attribution, sequence and tool-call count; its digest identifies the latest accepted delta. It contains no contribution array. There are no additional accounting tables, per-record revision columns or revision history. Inline subagents retain independent scopes under the parent Service run and their own `harness_run_id`. A model contribution carries the `model_id` and `price_snapshot` selected at dispatch under its `call_id`; unknown dispatch correlation remains unattributed. Replacement preserves this attribution and the contribution's first `ingested_at`, and never reprices historical consumption.

**Delta ingestion** uses a short transaction independent of execution checkpoint publication. It locks the owning run before the attempt, validates scope ownership and monotonic progress, and atomically writes changed contributions and the scope cursor. A successful commit acknowledges delivery. An identical current-sequence retry is idempotent; older delivery cannot roll back accounting; changed identity or conflicting current-sequence facts fail. For an advancing delta, `after_sequence` must be at most the stored sequence, or zero for a new scope: overlap after an uncertain commit is accepted, while a gap fails without advancing progress. Model observations retain Harness contribution validation, and observed tool calls cannot decrease. Each delivery reads and writes only its changed contributions and bounded scope metadata; the Service neither loads prior full snapshots nor computes a snapshot diff. A failed write retains pending delivery for bounded attempt cleanup retry. Display chunks are not a second ingestion path.

Object-store checkpoints contain the complete Harness state, including `a13n.usage` and nested child state, subject to Harness accounting capacity limits. Checkpoint publication and delta ingestion have independent acknowledgements.

Late ingestion is not lease-fenced: an expired or sealed attempt can still report consumption, but cannot move an execution checkpoint or change a sealed outcome. A takeover starts a fresh usage scope rather than letting old and new writers overwrite one scope. Their distinct contributions both remain counted. Stable provider receipts retain their first owner across scopes in the workspace; conflicting receipt facts are rejected. A native provider-suspended generation cannot continue across fresh scopes; Harness rejects that unsupported continuation before dispatch rather than recounting cumulative usage.

Individual record delivery is append-only: checkpoint and terminal reconciliation insert each fact once, compare its digest, and log and skip oversized (over 65536 bytes) or conflicting records without overwriting the first fact.

`runs.usage_at_seal` is the total visible to the sealing transaction (`requests`, `input_tokens`, `output_tokens` of model records); it is not corrected by later arrivals. `max_usage.requests` counts model records ([05](05-runs.md#execute)).

`GET /usage` (`read`) sums model records per model, including late arrivals, and names each by its key in `model`: `requests`, input, output, cache-read and cache-write tokens, and `cost`, the sum of each record's own priced cost (null when no record of that model was priced). It filters by `run_id`, `thread_id`, `session_id`, `ingested_after` and `ingested_before`. `model` is null for unattributed records. A crash before a provider report reaches the Service can leave an unknown charge; ingestion preserves received facts, not discovery of every billable operation.

### Workspace usage analysis

The `usage` package owns the read API and aggregation over durable consumption. Run execution owns ingestion, scope progress and current contributions. Querying usage needs the Workspace's `read` permission and does not depend on a telemetry backend.

`GET /usage/overview`, `GET /usage/agents` and `GET /usage/models` require timezone-aware `start` and `end`, selecting the half-open interval `[start, end)`, at most 366 days. Consumption selects model contribution rows by their first `ingested_at`; Run metrics independently select Runs by `started_at`. The queries exclude provider receipts and scope metadata rows. Later observations replace the contribution counted at its original ingestion time. No precomputed totals or background aggregation are required.

Model metrics are `requests`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_hit_rate`, `cost` and `unpriced_requests`. Cached input is a subset of input; the rate divides summed cached input by summed input, or is null with no input. `cost` is the recorded USD subtotal, serialized as a decimal string: zero for no requests, null when every request is unpriced, and the known subtotal otherwise. `unpriced_requests` counts null costs, including within a partially priced group. Recorded counters and costs do not establish complete provider billing coverage.

Run metrics are `runs` and `average_duration_seconds`. Each started Run counts once regardless of its request count. The average uses only Runs with `sealed_at`, subtracting `started_at`; it does not join successor Runs or attribute a Run's duration to a model. Runs with no model contributions still appear in Agent aggregates, and late contributions from Runs outside the window still appear in consumption.

The overview returns `usage`, `runs` and `daily`. `timezone` is an IANA timezone, default `UTC`, used only to group the daily consumption. Every intersecting local date is returned in chronological order, including zero-usage dates; boundary days include only the selected interval. Daylight-saving changes follow that timezone's calendar. Totals and daily consumption are computed in one database statement.

Agent rows carry `agent_id`, `name`, `usage` and `runs`; attribution uses the owning Service Run's Agent, without additional subagent rollups. Model rows carry `model` (the model key), `name` and `usage`; null model/name identify unattributed consumption. Both collections use the standard `items`, `next_cursor`, `limit` and query-bound `cursor` contract. Order is known cost descending, then Agent ID or model key ascending, with unknown cost last. Empty consumption on a Run-only Agent has known zero cost. Pagination is a live view; later consumption can change ordering, and reloading starts from the first page.

## Objects

The object store holds immutable bytes under owner-named keys: lowercase slash-separated segments of at most 1024 characters. Its contract, on both the local and S3 backends, is write, read, prefix listing and delete. Every write uses a key no other write uses, ending in 128 random bits or the ID of the row that owns the bytes, so a write is a plain put: repeating an uncertain write stores the same bytes again, and two writers never meet at one key. There is no conditional write and no object catalog table; only committed references make bytes reachable. A committed reference names a key, digest and size, and a read that finds missing or different bytes is `unavailable` (dependency `objects`).

| Key                                                | Owner                                                                 |
| -------------------------------------------------- | --------------------------------------------------------------------- |
| `orgs/{org}/runs/{run}/state/{attempt}/{random}`   | A run's checkpoint state, written by that attempt                     |
| `orgs/{org}/runs/{run}/display/{attempt}/{random}` | A run's display, written by that attempt                              |
| `orgs/{org}/uploads/{upload}`                      | Upload bytes ([04](04-resources.md#uploads-and-assets))               |
| `orgs/{org}/images/{owner}/{random}`               | Organization, workspace and agent images ([03](03-tenancy.md#images)) |
| `users/{user}/images/{random}`                     | User avatars                                                          |

Publication writes the bytes outside any database session, then commits the reference. Paired state/display publication finishes both writes before reporting a failure, so failure sealing cannot race its still-running write. A failure between the two leaves an unused object.

**Run state and display cleanup is owner-driven.** Only a run's own attempts write under its prefix, and only its pointers make an object reachable:

1. A checkpoint transaction stages a `checkpoint_cleanup` outbox delivery for the objects its pointer change replaced, alongside the pointer change. Rollback publishes neither the references nor the reclamation intent. Boundary acknowledgement and successor acceptance do not wait for deletion. A replaced key is never written again, so its deletion is final.
2. A takeover stages a scan of the run prefix that deletes the objects of earlier attempts, read from each key's attempt segment, keeping the captured committed references. Objects of the new or later attempts are skipped because no pointer may name them yet. The scan does not block execution or consume run attempts on deletion failure. Every seal stages a full prefix scan preserving its final references, including a display without a state checkpoint.
3. Each delivery has one total `objects.timeout` I/O budget. A scan lists at most 1000 keys in lexicographic order per claim. It records the last completed key and defers remaining work in a fenced outbox transaction; deferral returns its attempt. Failure retries the same page safely because deletion is idempotent. A deadline with no progress counts as failure. Interruption leaves durable work for another sender; exhausted retries become visible dead deliveries, not a guarantee of unlimited retries.
4. No attempt starts an object write within `objects.timeout` of its local lease deadline. A stale attempt cannot commit its late bytes. Referenced state is protected during takeover; final scans run only after seal has made the references immutable.

This adds at most one small outbox row per checkpoint that replaces a reclaimable object, plus one at takeover and one at seal. With C such checkpoints per second, one control replica's default batch admits up to 32 reclamation claims per second before I/O limits; additional pages and retries consume that capacity too. Backlog monitoring exposes when producers outrun deletion. No per-run scheduler or second in-memory queue coalesces or drops this work.

A run normally holds one state and one display object, plus replaced objects awaiting reclamation. Objects named by frozen pointers are never deleted: a completed or waiting run keeps its final pair because fork and continuation start from it, and a failed or cancelled run keeps its last pair as inspection evidence.

There is no other object reclamation: no age-based upload expiry, orphan inventory or physical purge of history, assets or images. Unused uploads, the bytes of an upload that lost a concurrent request key, and replaced images stay stored; upload limits still apply.

## Checkpoints and display

State and display are independently versioned, Zstandard-compressed storage envelopes. Their prefixes are `a13n-state-zstd/1\n` and `a13n-display-zstd/1\n`; each contains one checksummed Zstandard frame of UTF-8 JSON. The selected pointer's digest and size describe the stored bytes. Reads verify those bytes, the expected envelope kind, checksum, frame completeness, and decoded limits before parsing JSON. State permits at most 256 MiB decoded / 257 MiB stored; display permits 72 MiB decoded / 73 MiB stored, independently of its smaller block-content budget. Compression, decompression, and JSON serialization run off the event loop and outside database sessions. Native `HarnessState` encoding is unchanged. Live deltas are not compressed or base64-wrapped by this codec.

Control and Worker use state/display pointer format 2 together. There is no legacy JSON reader or dual write; existing incompatible checkpoints are rejected without rewriting or deleting their bytes. Newer state formats retain the existing worker claim exclusion. Publication and lease-fenced pointer selection remain separate; compression does not change cleanup ownership or rollback behavior.

A **state object** holds the checkpoint format (currently 2), the Harness state, the checkpoint sequence, the attempt number that wrote it, whether the resume's optional input has been incorporated, and for a waiting run the Harness's deferred requests. `runs.checkpoint` points at it with `{key, digest, size, format, seq, attempt}`; `runs.display` points at the display with `{key, digest, size, format, position}`. A checkpoint of a newer format waits for a worker that reads it; an older one fails its run ([05](05-runs.md#claim-heartbeat-and-authority)). [05](05-runs.md#assignment-and-incorporation) owns incorporation and the checkpoint commit, which also stores the run's memory cursors in `runs.memory_cursors`, outside the state object ([11](11-memory.md#execution)).

A **display** contains the shared [compact display snapshot](../a13n-stream-protocol/00-overview.md#compact-display-contract) and an optional Redis seek hint. Its producer identifies the Service Run and attempt; inline Harness Runs retain separate scopes under that producer. The snapshot's sequence is exposed as `{attempt}-{sequence}`. Canonical native history and producer-side observations feed the shared projector, not an AG-UI event accumulator. Imported or parent native history supplies model context but is not repeated as this Run's display.

Each safe boundary detaches the display together with the canonical native state before asynchronous export or storage. A model boundary captures canonical history, not provider-specific request projections, and does not wait for its public consumer. A tool boundary commits the paired evidence before tool execution is acknowledged. Its boundary marker names exactly the captured sequence, even if later output was already produced. Every checkpoint writes a self-contained snapshot; this intentionally retains O(retained history) write cost. A subsequent attempt restores the last durable baseline with a fresh generation and sequence zero, discarding the old attempt's provisional suffix.

`resume_after` is a confirmed Redis delta ID from the same attempt at or below captured coverage. Checkpoint publication reads the confirmed hint without waiting for Redis. Pending, failed or timed-out writes leave an earlier covered hint or null; terminal publication may use the hint retained after bounded stream cleanup. New attempts start without a hint. It is never semantic coverage or execution authority.

- A display retains at most 4096 compact blocks and `worker.display_bytes` of serialized block content. Oldest blocks are removed first and counted in `snapshot.omitted`; each nested text field retains at most 262144 characters and marks its block `truncated`. State retains complete native values. Retention changes are explicit replayable operations.
- The shared block kinds, statuses, native source addresses and scope identities replace the former Item/event fold. Tool argument completion remains distinct from execution completion; native results and retry failures settle the same tool block.
- Compact request and context-operation summaries preserve execution outcome and provenance without storing raw lifecycle observations. Usage accounting stays in its own durable records. Display order and semantic positions are not timestamps; renderers leave unavailable timing unknown.
- Producer continuity is stored with the snapshot for recovery but omitted from the public read. Browsers apply typed operations and render compact values; they do not reconstruct native execution or fold AG-UI events.

A worker sealing failure or cancellation may publish its final compact display with unfinished scopes closed. A run sealed by the sweep, interrupt, archive, a parent's cancel or recovery retains its last committed display. The read returns that immutable snapshot and the sealed Run; renderers show unresolved blocks as interrupted without rewriting persisted content. [05](05-runs.md#reads) owns `GET …/runs/{run}/items`.

The display and live stream consume AG-UI 1.0 canonical camelCase events through one stream observer per worker Harness root Run. Inline children retain native IDs and carry `subagentRunId`; display identity includes that attribution, so coincident root and child IDs do not collide. Ordered public tool content parts survive display save/reload within existing bounds, while supplemental model-only content remains hidden. Standard `RUN_FINISHED` interrupt observations retain native deferred tool-call IDs but do not replace Service pending records or partial-answer policy. Child safe boundaries cannot publish a root checkpoint or Environment state, and presentation usage does not replace the usage ledger.

## The thread stream

Each thread has one Redis stream, `a13n:thread:{thread}`, written only by workers. It carries the in-flight tail of the thread's output; the committed display holds everything before it. An attempt appends two kinds of entries: output **deltas** carrying the run ID, attempt number, per-attempt sequence and one shared typed `DisplayDelta`; and a **boundary** marker after each checkpoint commit, carrying the exact sequence its captured display covers. Deltas never contain a full snapshot or raw AG-UI event. Appends go through a bounded in-memory buffer (1024 entries) and a background writer, bounded by `redis.timeout`, so event delivery and checkpoint publication do not wait for Redis; a full buffer, a delta over 262144 bytes or a Redis failure drops output, which readers see as a gap. Nothing in the stream needs to survive: durable output is in display objects.

The producer stages typed operations immediately and batches them before allocating a sequence. A batch advances one dense sequence atomically, preserving every block revision; already allocated sequences are never merged. Pending operations flush on the `worker.stream_coalesce_seconds` timer, when they reach 128 operations or 8192 encoded bytes, before a capture, and at attempt cleanup. A window of zero publishes immediately. A large individual operation can exceed the flush threshold; Redis's independent frame bound still applies. These delivery batches change neither compact semantics nor checkpoint pairing.

A boundary records the highest delta sequence offered before its Redis append. Only when its committed coverage spans that entire prefix may the writer trim before it (`XTRIM MINID`), keeping entries within `worker.stream_trim_seconds` of the boundary ID; zero removes the whole proven-covered prefix. A delayed boundary following newer, uncommitted deltas does not trim. Its ID is usable as a seek hint only when the consumer also covers its prefix sequence. Thus a slow checkpoint cannot delete or skip output produced while its storage awaited. The boundary itself stays; a later covering boundary can reclaim the older prefix. As a backstop, `XADD` caps the stream at about `worker.stream_length` entries (coalesced output appends about ten entries a second), and the stream expires `worker.stream_ttl` seconds after its last append.

`GET /threads/{thread}/stream` serves one thread as Server-Sent Events (`Cache-Control: no-store`, `X-Accel-Buffering: no`). Opening it authorizes `read` before the response starts, so refusals are ordinary HTTP errors. A comment line keeps the connection alive every 15 seconds. The connection holds no database session while idle.

A reader that holds a display sends `run=<run_id>&position=<attempt>-<sequence>` and may pass its `resume_after` as `Last-Event-ID`. Both query parameters are required together. The Run must belong to the authorized thread, and the position cannot name an attempt newer than the Run's latest attempt. Position components are canonical nonnegative decimal integers of at most 20 digits. These claims describe the client's coverage; they grant no access or execution authority.

The position initializes sequence tracking only for that Run and attempt. Covered deltas are skipped before payload decoding; boundary notifications remain observable even at covered positions, and never move sequence tracking backwards. A retained hint is used for direct seeking only when its Run and attempt match both the claim and the active execution, and its sequence is covered. An absent, expired or incompatible hint falls back to retained replay filtered by position. Its absence alone does not emit `gap`. The next uncovered delta must be the next sequence; otherwise a gap identifies the missing range through `position`. A boundary beyond the received sequence likewise reports a gap through its committed position. Replay joins live delivery at a captured Redis cursor, retaining events that arrive during snapshot reading and replay. An older-attempt resume receives `reset` before the current attempt's tail; prior-attempt progress never seeds the new attempt.

Without semantic resume query parameters, a connection replays retained entries from their beginning. Header-only resume is rejected: a Redis cursor never initializes semantic coverage. `Last-Event-ID` must be a Redis entry ID, `{ms}-{seq}` with at most 20 digits per component; malformed values are `invalid_argument`. Hint validation looks up the exact entry and compares Redis ID components numerically. Hints are a transport optimization, not proof of complete delivery or that a Run is active.

Periodic SQL refresh observes the selected display revision and coverage as well as the Thread version. It emits a boundary notification when the selected revision changes, and a gap when committed coverage exceeds the reader's coverage, even if the corresponding Redis delta and boundary were both lost. SQL-derived boundaries have no Redis ID. Consumers also periodically refresh the selected Run's compact read, including after the Thread moves on. They reject older Run versions or saved coverage and never replace a sealed baseline with an unsealed response. An HTTP snapshot may replace the current baseline only when its uncovered suffix is replayable; consumer suffix buffers have both entry and byte bounds.

A transport EOF is reconnectable, not successful stream completion. An incomplete final SSE frame, including partial UTF-8, is discarded without advancing coverage or its hint. Reconnection uses the consumer's applied semantic position and a compatible optional hint. Complete malformed frames are protocol errors rather than silently acknowledged data.

Console retains the display and its continuously applied position across connection retries, including transport retries, and refreshes metadata and the saved baseline on explicit reconnect. An older same-attempt snapshot preserves the contiguous local suffix. Cursor-only page reloads instead load a fresh display. Gap positions identify the output a snapshot must cover; later deltas wait without advancing the complete position across a hole. A covering snapshot heals the gap immediately and replays only its contiguous uncovered suffix. If it does not cover the gap, Console waits for a newer boundary or terminal progress instead of repeatedly reading the same snapshot. Transport failures may report a gap without a known position; continuity is reassessed with subsequent deltas, boundaries or terminal state. Attempt changes discard superseded provisional output even before the new attempt's first checkpoint. Terminal reads discard provisional output and show the saved display with its truncation and interruption metadata.

| Frame (`event:`) | `data`                                             | Client action                                                                         |
| ---------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `delta`          | `{run_id, attempt, sequence, delta: DisplayDelta}` | Apply one atomic provisional batch                                                    |
| `boundary`       | `{run_id, attempt, sequence}`                      | The committed display now covers the attempt up to `sequence`                         |
| `changed`        | `{version}`                                        | Re-read the thread (inbox, pointers, mounts, headers, labels or archive changed)      |
| `reset`          | `{run_id}`                                         | A newer attempt replaced the run's output: discard it and re-read its items           |
| `gap`            | `{run_id, position: string \| null}`               | Read items and reassess coverage through `position`, or await a known recovery target |

Redis-derived `delta` and `boundary` frames carry an SSE `id:`, an optional Redis seek hint. SQL-derived `boundary` frames have no ID and never overwrite a confirmed hint. Frames concern only the thread's current run; deltas of attempts older than the current attempt are dropped. A `gap` is sent on a per-attempt sequence discontinuity, a Redis failure, or a connection that falls more than 1024 entries behind. A discontinuity is a delta that does not follow the connection's last sequence of its attempt, or a boundary beyond it; a boundary then sets that sequence, so deltas continue from it without another gap. Only missing uncovered output reports a sequence gap; an absent hint alone does not. A shared read overtaken by trimming detects the missing suffix at its next delta or boundary and recovers from the display. A connection resuming exactly at a retained boundary continues without a gap. A Redis failure while the connection starts or replays sends `gap` and ends the connection; one while following live entries sends `gap` and the connection continues.

All connections of a process share one blocking `XREAD`. Every `control.stream_refresh_seconds` the process runs one snapshot query for all watched threads (version, current run, its latest attempt, selected display revision and coverage) and one authority check per distinct credential and workspace. A thread version change produces `changed`, a newer attempt produces `reset`, and lost read access ends the stream. Changes made by other callers therefore appear within one refresh interval; a client's own operations return their results directly. A delta already in flight when authority changes can still be shown; `reset` corrects it. There is no run-level, session-level or workspace-level stream: a thread has at most one active run, and a child thread is discovered from its parent's output.

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
