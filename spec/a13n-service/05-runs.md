# Runs: input, execution and history

## Design position

A thread's history advances only through runs. Four operations move it: **accept** turns queued input into a run, **claim** gives a worker a leased attempt of that run, **execute** drives the Harness and commits checkpoints, and **seal** records the outcome. PostgreSQL owns input assignment, execution authority and the pointer to each run's committed checkpoint; object bytes, Redis frames and worker memory never decide any of them.

Accepting an input does not prove the agent saw it, and incorporating it does not make a failed run part of the thread's history. The Service guarantees at-most-once incorporation of each entry into its assigned run's committed continuation. It does not guarantee exactly-once model requests, tool effects or sandbox changes.

At execution, accepted input distinguishes message content from a deferred-result batch correlated to the exact waiting run it answers. The Service owns both until committed history incorporates them. A resumed run retains its accepted answers in `resume` and the original requests in its waiting parent's checkpoint. Restoring an own checkpoint does not discard that input: the worker supplies remaining results in recovery mode, without replaying old approval grants or duplicating recorded results. Pre-effect checkpoints remain required; recovery uses their continuation plus unincorporated accepted input rather than a second Harness-owned result store.

## Boundaries

| Concern                                                                                          | Owner                             |
| ------------------------------------------------------------------------------------------------ | --------------------------------- |
| Verbs, execution authority and principal validity                                                | [03](03-tenancy.md#authorization) |
| Agents, revisions, per-run overrides and caller headers                                          | [04](04-resources.md)             |
| Environments, thread mounts and what an attempt mounts                                           | [06](06-environments.md)          |
| Memories, thread memory mounts and what an attempt executes with                                 | [11](11-memory.md)                |
| Run objects, display, the thread stream, usage records, outbox and lifecycle webhooks            | [07](07-facts-and-delivery.md)    |
| Worker wakeups, sweeps, admission policy and settings                                            | [09](09-runtime.md)               |
| Routes, preconditions and `Idempotency-Key` replay                                               | [10](10-api.md)                   |
| Input capacity, source selection, resume, assignment, the checkpoint commit, seal and child runs | This chapter                      |

## Model

- A **session** groups related threads. Its `last_run_id` names the most recently accepted run of any of its threads.
- A **thread** advances one history at a time. It has an inbox, desired environment and memory mounts ([06](06-environments.md#mounts), [11](11-memory.md#mounts)) and caller headers ([04](04-resources.md#caller-headers)).
- An **entry** is one queued input: a `message` or a `child_result`. Only a pending message can change.
- A **run** starts from one source, either an entry or the answers that resume a waiting run. It freezes an agent revision, options and an execution identity, and may incorporate further compatible entries. Its initial history never changes.
- An **attempt** executes a run under a renewable lease. Recovery and handoff replace the attempt; the run and its assigned inputs stay.
- A **checkpoint** is resumable Harness state, committed by moving the run's pointer to an immutable object. The display committed with it is what viewers see ([07](07-facts-and-delivery.md#checkpoints-and-display)). A checkpoint never rolls back external effects.

## Tables

The tenant foreign-key rules in [03](03-tenancy.md#tenant-integrity) apply throughout. Payloads, outputs and options are bounded inline JSON; files a message refers to are assets ([04](04-resources.md)).

```
sessions
  id  organization_id  workspace_id  labels  created_by_id  last_run_id NULL
  version  created_at  updated_at
  -- last_run_id and updated_at change without a version bump

threads
  id  organization_id  workspace_id  session_id  origin  origin_thread_id NULL
  origin_run_id NULL  origin_tool_call_id NULL  subagent NULL
  current_run_id NULL  head_run_id NULL  last_run_id NULL
  mcp_headers  labels  archived_at NULL  version  created_at  updated_at
  origin IN ('new', 'fork', 'child')
  CHECK (origin = 'new'   AND origin_thread_id, origin_run_id, origin_tool_call_id all NULL
      OR origin = 'fork'  AND origin_thread_id, origin_run_id set, origin_tool_call_id NULL
      OR origin = 'child' AND origin_thread_id, origin_run_id, origin_tool_call_id all set)
  CHECK ((origin = 'child') = (subagent IS NOT NULL))
  UNIQUE (origin_run_id, origin_tool_call_id) WHERE origin = 'child'

inbox_entries
  id  organization_id  workspace_id  thread_id  kind  delivery  position  principal_id
  authority  payload  size  agent_id NULL  agent_revision_id NULL  options
  child_run_id NULL  origin_run_id NULL
  request_key NULL  request_digest NULL  request_kind NULL  request_target NULL
  status  assigned_run_id NULL  incorporated_checkpoint_seq NULL  failure NULL
  created_at  finished_at NULL
  kind IN ('message', 'child_result')
  delivery IN ('steer', 'next_run')
  status IN ('pending', 'assigned', 'consumed', 'failed', 'withdrawn')
  request_kind IN ('thread', 'message', 'fork')
  CHECK (position > 0 AND size >= 0)
  CHECK ((kind = 'message') = (agent_id IS NOT NULL))
  CHECK ((kind = 'child_result') = (child_run_id IS NOT NULL AND origin_run_id IS NOT NULL))
  CHECK (status NOT IN ('assigned', 'consumed') OR assigned_run_id IS NOT NULL)
  CHECK (status <> 'pending' OR assigned_run_id IS NULL)
  CHECK ((status = 'consumed') = (incorporated_checkpoint_seq IS NOT NULL))
  CHECK ((status IN ('pending', 'assigned')) = (finished_at IS NULL))
  CHECK ((status = 'failed') = (failure IS NOT NULL))
  CHECK (request_key, request_digest, request_kind, request_target all set or all NULL)
  UNIQUE (thread_id, position) DEFERRABLE INITIALLY IMMEDIATE
  UNIQUE (workspace_id, principal_id, request_key) WHERE request_key IS NOT NULL
  UNIQUE (child_run_id) WHERE kind = 'child_result'

runs
  id  organization_id  workspace_id  session_id  thread_id  agent_id  agent_revision_id
  revision_selection  principal_id  authority  options  options_digest  mcp_headers  environment_mounts
  memory_mounts  memory_cursors
  source_entry_id NULL  resume NULL  resumed_by_id NULL  request_key NULL  request_digest NULL
  trigger  lineage  parent_run_id NULL
  status  wait_reason NULL  pending NULL  cancel_requested_at NULL
  current_attempt_id NULL  available_at  attempts  max_attempts
  checkpoint NULL  display NULL  output NULL  failure NULL  usage_at_seal NULL
  labels  started_at NULL  sealed_at NULL  version  created_at  updated_at
  revision_selection IN ('pinned', 'default', 'inherited')
  trigger IN ('input', 'queued', 'resume', 'child_result', 'spawned')
  lineage IN ('root', 'continue', 'fork')
  status IN ('accepted', 'running', 'waiting', 'completed', 'failed', 'cancelled')
  wait_reason IN ('approval', 'client_tool', 'user_input', 'multiple')
  CHECK ((source_entry_id IS NULL) <> (resume IS NULL))
  CHECK ((resume IS NULL) = (resumed_by_id IS NULL))
  CHECK ((request_key IS NULL) = (request_digest IS NULL))
  CHECK (request_key IS NULL OR resume IS NOT NULL)
  CHECK ((trigger = 'resume') = (resume IS NOT NULL))
  CHECK ((lineage = 'root') = (parent_run_id IS NULL))
  CHECK ((status IN ('waiting', 'completed', 'failed', 'cancelled')) = (sealed_at IS NOT NULL))
  CHECK ((status IN ('failed', 'cancelled')) = (failure IS NOT NULL))
  CHECK (status NOT IN ('waiting', 'completed') OR (checkpoint IS NOT NULL AND display IS NOT NULL))
  CHECK ((status = 'waiting') = (wait_reason IS NOT NULL AND pending IS NOT NULL))
  CHECK ((status = 'running') = (current_attempt_id IS NOT NULL))
  CHECK (attempts BETWEEN 0 AND max_attempts)
  UNIQUE (thread_id) WHERE status IN ('accepted', 'running')
  UNIQUE (source_entry_id)
  UNIQUE (workspace_id, resumed_by_id, request_key) WHERE request_key IS NOT NULL

run_attempts
  id  organization_id  workspace_id  run_id  number  status  start_reason
  replaces_attempt_id NULL  worker_id  worker_build  harness_run_id NULL
  lease_token_hash  lease_expires_at  heartbeat_at  yield_reason NULL  failure NULL
  created_at  started_at NULL  finished_at NULL  updated_at
  status IN ('leased', 'running', 'succeeded', 'yielded', 'failed', 'cancelled')
  start_reason IN ('initial', 'recovery', 'handoff')
  CHECK ((start_reason = 'initial') = (replaces_attempt_id IS NULL))
  CHECK ((status IN ('succeeded', 'yielded', 'failed', 'cancelled')) = (finished_at IS NOT NULL))
  UNIQUE (run_id, number)
  UNIQUE (run_id) WHERE status IN ('leased', 'running')
```

`usage_records` is owned by [07](07-facts-and-delivery.md#usage-records); `environments` and `thread_environments` by [06](06-environments.md).

Triggers enforce what a CHECK cannot:

- **Entries.** Identity, principal, authority and replay evidence never change. A settled entry (`consumed`, `failed`, `withdrawn`) never changes. An assigned entry changes only its disposition. Legal transitions are pending → assigned | failed | withdrawn and assigned → pending | consumed | failed | withdrawn.
- **Runs.** An unsealed run changes only its progress columns (status, wait, pending set, cancellation, attempt, availability, pointers, memory cursors, output, failure, usage, labels, timestamps); its selection is frozen at acceptance. A sealed run changes only `labels`. Legal transitions are accepted → running | failed | cancelled and running → accepted | waiting | completed | failed | cancelled.
- **Attempts.** A finished attempt never changes, `harness_run_id` is set once, a running attempt never returns to leased, and an expired lease cannot be extended even before the expiry sweep closes it.
- **Pointers.** A deferred constraint trigger checks at commit that `current_run_id` is the thread's accepted or running run, `head_run_id` is completed or waiting, `last_run_id` is sealed, and `current_attempt_id` is the run's live attempt.

`head_run_id` is the latest completed or waiting run: the history a successor continues. `last_run_id` is the most recently sealed run, including failures. A failed run's checkpoint remains inspection evidence but never becomes head. `runs.checkpoint` and `runs.display` are typed pointers to immutable objects ([07](07-facts-and-delivery.md#checkpoints-and-display)); they move only in fenced worker transactions and freeze at seal.

`runs.options` holds the message's options frozen at acceptance (`labels`, `max_usage`, `overrides` with pins resolved), and the run's `labels` start as the options' labels. `runs.options_digest` is the SHA-256 of the canonical JSON of the options as the source submitted them; a resume or child-result successor inherits its origin's. `runs.mcp_headers` holds the thread's caller headers frozen at acceptance; no view shows them. `runs.environment_mounts` is the mount set frozen at acceptance, `[{name, environment_id, working_directory}]`. `runs.memory_mounts` is the memory mount set frozen at acceptance, `[{name, memory_id, access, recall}]`, and `runs.memory_cursors` holds, per memory ID, the cursor of the memory context its committed history holds ([11](11-memory.md#execution)). `max_attempts` is `worker.max_attempts` at acceptance.

The thread `version` changes on every inbox, mount, pointer, header, label or archive change; inserting or updating entries and changing mounts bump it by trigger. It is the ETag for inbox, mount and header edits and the change signal of the [thread stream](07-facts-and-delivery.md#the-thread-stream).

## Submit and accept

Three requests submit a message. Each needs the `run` verb on the workspace and an `Idempotency-Key`:

| Request                                     | Creates                                                                        |
| ------------------------------------------- | ------------------------------------------------------------------------------ |
| `POST …/threads` (`NewThread`)              | A thread in the named session or a new one, its first entry and initial mounts |
| `POST …/threads/{thread}/inbox` (`Message`) | An entry on an open thread                                                     |
| `POST …/runs/{run}/fork` (`Fork`)           | A thread that continues from the run's history, and its first entry            |

A message names `agent_id`, optionally `agent_revision_id`, a `delivery` (`steer` by default, or `next_run`), a payload and options. The payload is 1–32 content parts: `text` (≤ 65536 characters), `asset`, `url` (plain http(s), ≤ 2048 characters, no user information) or `json` (depth ≤ 32). Options are `labels`, `max_usage.requests` (1–10000) and `overrides` ([04](04-resources.md)).

One transaction authorizes `run` and looks up the request key; a known key replays without validating again, whatever the thread's state. Otherwise it locks the thread and validates under the submitter's authority: the revision selection, that referenced assets are usable, overrides, caller headers ([04](04-resources.md#caller-headers)), initial environment and memory mounts ([06](06-environments.md#mounts), [11](11-memory.md#mounts)) and that each asset reaches the model ([files](#how-files-reach-the-model)). A named session that does not exist is `not_found`; an archived thread is `conflict` (`archived`). It then checks capacity, appends the entry at the next position and calls `accept` with it as the explicit entry. A refused request appends nothing. The receipt `Submitted {thread, entry, run | null}` carries the thread ETag and the entry's current disposition; `run` is the run the entry is assigned to, if any, such as the run it just started. The first call answers 201, a replay 200. Replay compares the request kind, target (the thread for a message, the origin run for a fork, and for a new thread the resolved workspace ID, however the path names the workspace) and a digest of the canonical body; a mismatch is `conflict` (`idempotency_key_reused`). A concurrent caller that loses the unique key rolls back everything it tentatively created, including a new thread or session, and replays the winner. [10](10-api.md#idempotency) owns the header rules.

### Inbox capacity

A thread's input usage is the count and payload bytes (canonical JSON length) of its **pending plus assigned** entries. One predicate owns the comparison with `control.inbox_count` and `control.inbox_bytes`. Assignment and return to pending neither release nor acquire capacity; consumption, failure and withdrawal release it once. Recovery keeps assignments and their occupancy.

An append or pending edit that would exceed either limit is `rate_limited` with `Retry-After: 1` and details `{retry_after_seconds, count, bytes, limit_count, limit_bytes}`. A child result that does not fit stays in its outbox row and is deferred ([child runs](#child-runs)). Resume never enters the inbox, so a full inbox never blocks answering a wait.

### Source selection

`start_run` is the only function that creates a run. `accept` chooses a queued source and calls it; [resume](#waiting-interrupt-and-fork) and [child spawn](#child-runs) call it with their own source.

| Thread condition                                            | Eligible source                                                                                        |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Archived, or an accepted or running run exists              | None                                                                                                   |
| Head waiting with any approval or client-tool item          | None; only [resume](#waiting-interrupt-and-fork) continues it. Messages and child results stay pending |
| Head waiting only for user input (`user_input` items)       | The lowest-position pending message; child results are skipped in the query and cannot starve it       |
| Otherwise                                                   | The lowest-position pending entry                                                                      |
| Latest sealed run failed or cancelled (paused), in addition | Only the explicit entry of the current submission; unrelated pending entries never replace it          |

A wait is question-only when every pending item is `user_input`; `wait_reason` summarizes the kinds but never replaces this per-item check. `accept` examines up to 16 pending entries per call. An entry whose start is refused fails in place with the refusal's code, or a conflict's reason (such as `environment_limit`), inside its own savepoint, and the next candidate is tried. A child result whose origin run never became history fails with `origin_not_committed`. A transient refusal (`unavailable`) aborts the whole transaction instead: the caller gets 503 and nothing is stored.

`start_run`, under the thread lock:

1. Checks that the workspace is not archived (`disabled`) and that the source principal is still valid with its frozen authority ([03](03-tenancy.md#execution-authority)), and selects the revision: the pinned revision (`pinned`), the agent's default (`default`), or the origin run's revision for resume and child-result sources (`inherited`).
2. Validates and freezes the options. A resume or child-result successor inherits its origin's options, overrides included; options are never reconstructed from an editable entry, a changed default or a later thread edit.
3. Chooses the parent: the thread's head (`continue`), otherwise a fork thread's origin run (`fork`), otherwise none (`root`). The fork fallback holds through any number of failed runs until the thread has its own head.
4. Freezes the environment mount set ([06](06-environments.md#mounts)) and the memory mount set, which at the thread's first acceptance first takes the agent's default memory mounts ([11](11-memory.md#mounts)), checks that each asset of a message source still reaches the model under the selected revision, frozen overrides and frozen mounts ([files](#how-files-reach-the-model)), and asks the admission policy ([09](09-runtime.md#extension-points)) to accept the run.
5. Inserts the accepted run with the thread's `mcp_headers` frozen into `runs.mcp_headers` and its parent's memory cursors for the memories still mounted under the same name, assigns the source entry, sets `threads.current_run_id` and the session's `last_run_id`, stages `run.accepted` webhooks ([07](07-facts-and-delivery.md#lifecycle-webhooks)) and registers the after-commit claim wakeup ([09](09-runtime.md#worker-claim-wakeups)).

The trigger records why the run started: `input` (the submission's own entry), `queued` (an earlier entry), `resume`, `child_result` or `spawned` (a child's first run).

### Editing queued input

Only the principal that submitted a pending message may edit it (`PATCH …/inbox/{entry}`: delivery, payload, revision pin, options; an explicit null revision unpins). The edit is validated again under the entry's frozen authority and rechecks capacity; the request digest never changes. Anyone with `run` may withdraw a pending entry (`DELETE`, which keeps a tombstone so the request key cannot be reused) or reorder the pending entries (`PUT …/inbox/order`, which must list exactly the pending entries, at most 512). All three require the thread `If-Match`. A non-pending entry is `conflict` (`entry_{status}`); editing a child result is `conflict` (`not_a_message`); editing another principal's entry is `forbidden`.

## Assignment and incorporation

```
pending ──accept / boundary──> assigned ──checkpoint commit──> consumed
   │                              │
   └──refused / withdrawn         ├──completed / waiting seal, not incorporated──> pending
                                  ├──same, thread archived──────────────────────> withdrawn
                                  └──failed / cancelled seal, not incorporated───> failed (run_ended)
```

The durable state is **assigned to a run**, not offered to an attempt. Recovery and handoff never change assignment.

At each safe boundary, after its checkpoint commit, the worker locks thread → run → attempt, proves its lease and assigns a FIFO batch of compatible pending entries, bounded by `worker.delivery_count` and `worker.delivery_bytes` and scanning at most `control.inbox_count`. Compatibility is checked before budgeting, so incompatible entries neither block nor consume the batch. A message is compatible when its delivery is `steer`, it names the run's agent, its revision pin is absent or equal to the run's, and either its options are the defaults (none given), which join whatever options the run started with, or the digest of its options as submitted equals the run's `options_digest`. Caller headers and principals take no part: anyone with `run` may steer, and the run keeps the principal and authority frozen at acceptance. A child result is compatible when its origin is this run or a completed or waiting run. A `next_run` or incompatible message waits for a later run.

The worker offers assigned entries to the Harness outside any transaction, before it acknowledges the boundary. A tool boundary waits for that acknowledgement, so a compatible entry already pending when the run reaches a tool boundary joins the model request that follows the tools. Each part reaches the model as follows: text as text, `json` as its JSON text, and an asset, read with the run principal's access, or a `url`'s content, fetched once by the worker through the endpoint policy ([08](08-providers.md#outbound-endpoint-policy)) and bounded by `providers.operation_seconds` and `providers.response_bytes`, as a [file](#how-files-reach-the-model). A child result is offered as a text notice. A `url` part that cannot be read is `invalid_argument` at `field` `content.{index}.url`: `reason` `endpoint_denied` for an address the policy refuses, `http_status` (with `status`) for a definitive status other than 200, and `unreadable` for another unreadable answer. A network error, a timeout or a status 408, 429, 500, 502, 503 or 504 is `unavailable` (dependency `url`): the attempt fails, and a later attempt fetches again.

A steer whose content cannot be read, such as one naming an asset that is no longer usable, fails alone with the refusal's code and the run continues without it, whether it is offered at a boundary or again by a recovered attempt. Only the run's own source entry fails the run when it cannot be read, and an `unavailable` read ends the attempt instead. An attempt whose initial entries all fail alone ends as `unavailable` too, and the next continues without them.

### How files reach the model

An asset or a `url`'s content is a file with a name (a URL's is the last segment of its path) and a media type (an asset's `content_type`, a URL's `Content-Type` without parameters). It reaches the model in the first way that applies:

1. **Native.** A file whose type the run's model declares it understands in its characteristics is native model content: `image_understanding` for `image/jpeg`, `image/png`, `image/gif` and `image/webp`; `audio_understanding` for `audio/wav`, `audio/mpeg`, `audio/ogg`, `audio/flac`, `audio/aiff` and `audio/aac`; `video_understanding` for `video/mp4`, `video/webm`, `video/quicktime`, `video/mpeg`, `video/x-matroska`, `video/x-flv`, `video/x-ms-wmv` and `video/3gpp`; and `document_understanding` for `application/pdf`. These are the types Pydantic AI maps to a provider format; others of those kinds, and other document types, whose support differs by provider, take the ways below.
2. **Text.** A text file (`text/*`, JSON, XML, YAML, JavaScript and the `+json` and `+xml` suffixes) of at most 65536 bytes is inlined, decoded with the charset its `Content-Type` declares (UTF-8 otherwise, and always for an asset) and headed by its name, media type and size. A URL's longer text is inlined truncated to its first 65536 bytes, with a note saying so, when the run has no primary environment.
3. **Placed file.** Any other file is written into the run's primary environment (mount `workspace`) at `/workspace/.a13n/attachments/{SHA-256 of its bytes}/{name}`, and the model reads a reference naming the file, its media type, size and path. The name is one path segment: control characters are left out, `/` becomes `_`, a name longer than 200 UTF-8 bytes is cut to that length keeping its extension, and an empty, `.` or `..` name becomes `attachment`. The environment's file tools read the file from there, an image, audio or video file through the agent's media-understanding model when its own model cannot ([04](04-resources.md#models)). A file already at the path with the attachment's size is left as it is, so an entry offered again by a recovered attempt, or a later message naming the same bytes, writes nothing twice; a file of another size, such as a copy the run changed, is written again. A placed file lives only in that environment: a thread whose primary environment is replaced, or a fork with fresh environments, no longer has it. A write the environment refuses as invalid, denied, conflicting, too large or unsupported is `invalid_argument` at the part's `field` with `reason` `placement_refused` and the environment's `code`; any other failed write is `unavailable`, and a later attempt places the file again.

An attempt reads the files of its initial input once the Harness has prepared the environments, and a steer's before offering it, one file at a time: an asset's bytes are read only for native content, inline text or a write, and none are kept once the model's content is built. Inline text and references are marked `display: false`; viewers see the attachment the payload names. A file the run can take in none of these ways is `invalid_argument` at `field` `content.{index}.asset_id` or `content.{index}.url`, with `reason` `environment_required` and its `media_type`. An asset is checked when its message is submitted or edited, against the model of the revision and overrides it names and a primary environment the thread mounts or acceptance would reserve from the agent's template, and again at acceptance, where a refusal fails the entry in place. A URL's content is known only when the worker fetches it.

Incorporation is proven by the exported state: an entry counts as incorporated once its ID is among the steering inputs recorded in the Harness message history. Entries offered as an attempt's initial input (the source entry, or the still-assigned entries a recovered attempt offers again) count once its first model boundary has passed. Enqueuing, observer events and an empty queue are not evidence.

The **checkpoint commit** first publishes the complete state and the current display as new immutable objects, outside any session. One short transaction then locks thread → run → attempt, proves the lease, requires `runs.checkpoint` and `runs.display` to still be what this attempt last committed, moves both pointers, stores the memory cursors of the committed history ([11](11-memory.md#execution)), marks the incorporated entries `consumed` with `incorporated_checkpoint_seq`, and ingests pending usage records. The commit is the checkpoint's durability point: consumption cannot run ahead of or behind the state it describes, and capacity is released once. [07](07-facts-and-delivery.md#checkpoints-and-display) owns the objects and their cleanup.

On recovery the new attempt reads the pointers, loads the named objects and deletes unreferenced objects of the run before delivering input or entering the Harness. Consumed entries are in the restored state; only still-assigned entries are offered again, once per Harness instance. Without a checkpoint the attempt starts from its parent's committed state (forked into this thread for `fork` lineage) or an empty history, then offers the source entry or applies the resume answers. Incorporation after the last committed checkpoint can be replayed.

Before a tool executes, the tool boundary commits a checkpoint and waits for it, so the call exists durably (in state and as an in-progress display item) before it can have an effect. Parallel calls of one model response share one commit. Execution uses the Harness's declared tool recovery: a recovered attempt closes undeclared pending calls with unknown-effect results, and a model can still request another call, so tool approval and idempotence remain necessary.

## Claim, heartbeat and authority

A worker scans every `worker.scan_seconds` and wakes early on a Redis marker ([09](09-runtime.md#worker-claim-wakeups)). With free capacity among its `worker.slots`, it selects due accepted runs with `FOR UPDATE SKIP LOCKED` and, in one transaction per batch, inserts a `leased` attempt with a fresh token and a lease of `worker.lease_seconds`, sets the run `running` and stages `run.running` and `run_attempt.leased` webhooks. `start_reason` is `initial` for the first attempt, `handoff` after a yielded attempt and `recovery` otherwise. Every attempt except a handoff's successor increments `runs.attempts`, so `attempts` counts charged attempts and the attempt list holds one more row per handoff. The attempt becomes `running` and records `harness_run_id` when the Harness run starts (`run_attempt.running`).

Claim takes run → attempt and never the thread; archive and interrupt take thread → run, so the run lock arbitrates between them. Claim does not recheck cancellation or archive: interrupt and archive seal an accepted run at once, so an accepted run never carries a cancellation request. A run is claimable only when the checkpoint it continues from (its own, or before its first commit its parent's) has a format no newer than the worker's, so a newer format waits for a worker that reads it; one of an older format is claimed and the run fails with `checkpoint_incompatible`. A failed claim is logged and retried after one scan interval without disturbing running attempts.

One predicate, evaluated after the relevant locks with fresh database time, proves a worker still holds its attempt:

```
run.status = running
AND run.current_attempt_id = attempt.id
AND attempt.status IN (leased, running)
AND attempt.worker_id = caller.worker_id
AND attempt.lease_token_hash = hash(caller.token)
AND attempt.lease_expires_at > clock_timestamp()
```

Every execution-dependent mutation uses it: checkpoint commits, steer assignment, child operations, environment preparation, lease extension and the worker's seal. A stale worker cannot revive an expired lease even before the sweep. Usage ingested outside a checkpoint commit is the one exception: it records a past charge and is scoped to its attempt without the predicate.

One supervisor task per worker polls every `worker.authority_seconds` for all its running attempts in one transaction, whose statements do not grow with their number. It reads cancellation and each run's authority without locks, and extends from database time the leases with no more than two thirds left. An extension proves the predicate with the attempt row locked and skips a row another transaction holds, so a poll never waits on an execution's own writes; a skipped lease stays due for the next poll. Planning an attempt checks the same authority before execution starts: the workspace is not archived, the principal is active and its current grants allow the frozen authority to run the run. A cancellation request stops execution with a cancelled outcome. An authority check that fails for any reason other than `unavailable` fails the run with `authority_revoked`; `unavailable` fails the poll, which then extends nothing. A renewal not confirmed within one interval has failed; once less than a third of the lease remains unrenewed, new paid calls are refused and the attempt is cancelled, and the run recovers after its lease expires.

The `expire_leases` sweep ([09](09-runtime.md#sweeps)) locks thread → run → attempt, rechecks that the attempt is still current and expired, and recovers the run with `lease_expired`. A sweep item that fails is logged and retried by a later pass. No sweep guesses what inputs a dead worker saw.

## Execute

Execution holds no database session across external I/O. An attempt:

1. Checks the run's authority as renewal does ([claim, heartbeat and authority](#claim-heartbeat-and-authority); `authority_revoked` otherwise), resolves the frozen revision with its overrides, connections, secrets, host capabilities and mounted memories ([11](11-memory.md#execution)), and reads the assigned entries and the requests already recorded against `max_usage`.
2. Deletes unreferenced objects of the run, then loads its own checkpoint or builds the initial state ([assignment and incorporation](#assignment-and-incorporation)). If the restored state already holds a completed or waiting outcome, the attempt seals it without executing.
3. Prepares the frozen mounts ([06](06-environments.md#execution)) while watching for cancellation and drain; a drain yields the attempt as a handoff before the Harness starts.
4. Opens the run's host outside any session, with a store for each mounted record memory ([11](11-memory.md#execution)), and streams the Harness run. Every paid dispatch (model requests including inline children, connection tools, web operations) first passes the call check: cancellation, lease renewal, the request limit (`usage_limit_exceeded`) and the admission policy's `proceed` ([09](09-runtime.md#extension-points)). A refusal becomes the run's outcome. A model request names the model it selected by its ID ([04](04-resources.md#models)); the check admits it as that model and keeps the model under the call's ID for its usage records.
5. At each safe boundary commits a checkpoint, emits a stream boundary ([07](07-facts-and-delivery.md#the-thread-stream)), delivers steers and then acknowledges the boundary. A boundary before a model request does not wait for the acknowledgement; a boundary before tool execution does, so the tools run after both the commit and the steer delivery.
6. Ends: a completed or waiting result commits a final checkpoint with the outcome, then seals; a failed or cancelled result seals with the display's interrupted tail; a handoff yields.

Output larger than `worker.output_bytes` fails the run with `payload_too_large`. An attempt that ends, is cancelled or loses its lease records its usage not yet committed with a checkpoint in one write, shielded from cancellation and bounded by `database.statement_timeout`. An object write never starts within `objects.timeout` of the local lease deadline, so a stale attempt's writes land before a takeover can clean the run's objects; a commit that would start that late ends the attempt instead.

**Service tools.** The Service's own agent tools, the `configuration` toolset ([04](04-resources.md#the-configuration-assistant)), `publish_asset` ([04](04-resources.md#uploads-and-assets)) and the async subagent tools ([child runs](#child-runs)), act under the run's principal and authority. A refused operation fails only its tool call, which the model reads and can recover from; an `unavailable` dependency ends the attempt, and a later attempt retries. Configuration tool arguments that do not fit the request body they stand for fail the tool call with at most five locations and the schema's own messages, never the submitted value.

When a worker drains for shutdown, each attempt yields at its next model boundary (`yield_reason = handoff`), where the in-flight request can simply be sent again; the drain waits up to `worker.drain_seconds`. A wait for mounts yields at once and a wait for children returns early, so the attempt reaches its next boundary. A yielded run is immediately due and spends no attempt.

## Seal and successor scheduling

Seal changes one thread. Cross-thread delivery goes through the outbox after commit.

| Caller                                  | Authority                                                         |
| --------------------------------------- | ----------------------------------------------------------------- |
| Worker                                  | The full worker predicate                                         |
| Interrupt, archive or a parent's cancel | Thread and run locks; the run is still accepted                   |
| Lease sweep                             | Thread, run and attempt locks; the attempt is current and expired |

A completed or waiting seal writes no objects: the final checkpoint commit already holds the outcome, including the exact pending set of a wait, and the seal requires the pointers to still be the ones the attempt committed. A failed or cancelled seal selects no continuation state; the state pointer keeps its last value as inspection evidence. A worker sealing failed or cancelled may first publish a display with its unfinished items marked interrupted and move only the display pointer. Seals by the sweep, interrupt, archive, a parent's cancel or recovery write nothing; readers show any `in_progress` item of a sealed run as `interrupted`.

Inside thread → run → attempt locks, a seal:

1. Records `usage_at_seal` (`requests`, `input_tokens`, `output_tokens` recorded so far).
2. Finishes the attempt, if any: `succeeded` for completed or waiting, otherwise `failed` or `cancelled`.
3. Seals the run's status, output or pending set, and failure; clears `current_run_id`; sets `last_run_id`, and `head_run_id` for completed or waiting.
4. Returns still-assigned entries to pending (completed, waiting; `withdrawn` when the thread is archived) or fails them with `run_ended` (failed, cancelled). Consumed entries stay consumed; they record committed incorporation, not successful work.
5. For a child thread's completed, failed or cancelled run, enqueues one `child_result` outbox row keyed by the run ([child runs](#child-runs)). A waiting child run reports nothing.
6. Stages `run.{status}` and `run_attempt.{succeeded | failed | cancelled}` webhooks.

After commit, whoever sealed deletes the run's objects that no pointer names, and a separate transaction calls `accept` for the thread. The `advance_threads` sweep ([09](09-runtime.md#sweeps)) recovers a lost call: it visits idle, unarchived, unpaused threads with pending input in rotating ID order, in batches of `control.sweep_batch`, so threads whose input stays pending cannot starve the rest. A thread that fails to advance is logged and retried by a later pass.

**Recovery.** A transient worker failure, a lost lease and a handoff return the run to accepted instead of sealing it, with these exceptions: a cancellation request seals it cancelled, and a failed attempt that was the run's last (`attempts = max_attempts`) seals it failed with that attempt's failure. Otherwise the attempt closes as `yielded` or `failed`, assignments stay, and the run becomes due immediately (handoff) or after min(60, 2^(attempts − 1)) seconds. `run.accepted` and `run_attempt.{yielded | failed}` webhooks are staged, and a run that is due at once wakes workers.

## Waiting, interrupt and fork

**Waits.** A run waits when the Harness suspends on deferred calls. Each pending item has `tool_call_id`, `kind` (`approval`, `client_tool` or `user_input`), `tool_name`, `arguments` and optional `presentation`; a wait holds at most 128 items. `ask_user_question` produces `user_input` items.

**Replies to questions.** At a question-only wait an ordinary message starts the successor: the question calls are closed with no-response results and the message is injected once through normal incorporation. Structured answers are never fabricated. Ordinary messages never approve, reject or complete a pending approval or client tool.

**Resume** (`POST …/runs/{run}/resume`) answers the exact wait of the thread's idle waiting head. It needs `run`, an open thread and an `Idempotency-Key`. If the run is not the thread's waiting head or the thread has a current run, it is `conflict` (`not_idle_waiting_head`) and nothing is stored. The waiting run, not a tool-call ID, identifies the suspension, so an answer for a superseded wait conflicts. Answers are `approve`, `reject` (optional reason) or `complete` (result), at most 128 with unique `tool_call_id`s and at most 262144 bytes of canonical JSON. An unknown call ID (reason `no_pending_call`) or an action the item's kind does not accept (reason `action_not_accepted`) is `invalid_argument`, with the call ID as `id`; a `user_input` item accepts no answer.

Answers are normalized against the complete pending set: an omitted approval is rejected with the reason "No decision was given", and an omitted client result or question gets `no_response`. An empty answer list therefore also closes a question-only wait, and the successor continues without a message. The normalized batch is stored as the successor's `resume`; partial progress is never stored. The successor has `trigger = resume`, `continue` lineage, and inherits the waiting run's revision, options, principal and authority; `resumed_by_id` records the caller. Replay is keyed by (workspace, caller, key) and compares a digest of the run ID and request. A second concurrent resume conflicts. If the successor fails, the waiting run is still the head and can be resumed again; answers are never replayed automatically.

**Interrupt** (`POST …/runs/{run}/interrupt`, `run` verb) seals an accepted run cancelled at once and records `cancel_requested_at` on a running one; the worker observes it within `worker.authority_seconds`, cancels in-flight work and seals cancelled, and lease expiry covers a missing worker. Interrupting a cancelled run returns it; a completed, waiting or failed run is `conflict` (`run_{status}`). Cancellation neither undoes effects nor cancels child runs.

**There is no retry operation.** A failed or cancelled run never becomes head, and automatic advancement pauses after it. The next explicit message continues from the unchanged head; running the same input again means submitting it again. Recovery of a crashed attempt is not retry: it resumes the same run from its last checkpoint within `max_attempts`.

**Fork** (`POST …/runs/{run}/fork`) needs a completed or waiting origin (otherwise `conflict` `run_{status}`) whose checkpoint format the Service reads (otherwise `conflict` `checkpoint_incompatible`). It creates a thread in the origin's session with `origin = fork` and the origin thread's `mcp_headers`, and its first entry, atomically under the request key. The new thread shares the origin thread's desired mounts unless `fresh_environments` is set, plus any `environments` the request names ([06](06-environments.md#mounts)), and copies the origin thread's memory mounts plus any `memories` the request names ([11](11-memory.md#mounts)). Forking a waiting run closes the inherited pending calls with default answers in the new branch; it never grants an approval or resolves the original wait. Failed and cancelled runs are not fork origins.

**Archive** (`POST …/threads/{thread}/archive`, `run` verb, thread `If-Match`) is final: there is no unarchive or purge, and every fact is kept. Under the thread lock it sets `archived_at`, withdraws pending entries, removes the desired mounts and stops the current run (an accepted run is sealed cancelled; a running run gets a cancellation request). A running run keeps its frozen mounts until it seals. Archived threads refuse submissions, resume, mount changes and header changes (`conflict` `archived`). Repeating archive returns the thread.

## Child runs

An agent's async subagent edges ([04](04-resources.md)) let a run delegate to a child thread through Harness tools. Spawn proves the parent's lease under thread → run → attempt locks. Steer, cancel and continue prove it under run → attempt locks and then lock the child thread and its run; status reads and waits take no lock. No path locks a parent run while holding a child thread.

**Spawn.** A child is identified by the delegating tool call: `(origin_run_id, origin_tool_call_id)` is unique, so a recovered parent finds the same child instead of starting another. Depth is bounded by `worker.child_depth` (`child_depth_exceeded`) and a run's children by `worker.child_count` (`child_count_exceeded`). The child thread is created in the parent's session with `origin = child`, `subagent` set to the edge name and the parent run's frozen `mcp_headers`. Its environments come from the edge policy, never the child agent's default template: `shared` adopts the parent run's frozen mounts, `dedicated` freezes the edge's template, `none` mounts nothing. It adopts the parent run's frozen memory mounts ([11](11-memory.md#mounts)). Its first message has delivery `next_run`, the edge's pinned revision and `max_usage.requests` equal to the edge's request limit; it starts through `start_run` with `trigger = spawned` under the parent run's principal and authority.

**Other operations.** Each async agent's tools reach only its own edges; an unknown edge name is `not_found`. Refusals follow the [Service tools](#execute) rule. The operations:

- Status reads and waits page (`execution_offset`, `execution_limit`, `total`, `next_offset`) through the runs of every child thread of the parent thread, in spawn order. A wait lasts 30 seconds by default and at most 300, polls only its page's statuses, and returns early when the parent must stop or its worker drains.
- Steer appends a `steer` message while the execution is its child thread's current run, and cancel stops an accepted or running execution like interrupt; otherwise either answers `accepted: false`.
- Continue needs the execution to be its child thread's last run with no current run (otherwise `conflict` `execution_not_resumable`); it appends a `next_run` message and starts the child thread's next run.
- Steers and continuations carry the child run's own agent, revision and frozen options.

**Results.** Each completed, failed or cancelled child run enqueues one `child_result` outbox row. Delivery ([07](07-facts-and-delivery.md#outbox)) locks only the parent thread, settles the row and appends a unique entry `{child_run_id, subagent, status, output, failure}` with delivery `steer` and the origin run's principal and authority, then calls `accept` after commit. A result for an archived parent is settled delivered and ignored. A result that does not fit the parent's inbox is deferred without using an outbox attempt ([07](07-facts-and-delivery.md#outbox)), so it is never dropped for lack of room. The result is a notification of that run, not a second completion of the delegating tool call.

A result steers into its origin run while that run is active. After the origin became history, the result is an ordinary queued source. A failed or cancelled origin that never became history fails the entry with `origin_not_committed`. The child thread stays directly inspectable in all cases.

## Reads

All reads need `read` on the workspace.

- **Sessions** list most recently updated first: a run's acceptance updates its session without changing the session's `version`, and a label edit updates it and changes its `version`. They are filtered by `q` (a session or thread ID, at most 72 characters), `agent_id`, `status` (at most 6) and `trigger` (at most 5) of the latest run, `updated_after`/`updated_before` and `label` (at most 8), all query parameters with `limit` and `cursor` ([10](10-api.md#collections)). Each carries `run_count` and a `preview` of the latest run: its ID, thread, agent ID and name, status, trigger, the first 256 characters of its input text and the first 512 of its text output. `POST …/sessions` creates an empty session (`run`) and `PATCH` changes its labels (`run`, `If-Match`).
- **Threads** list oldest first, filtered by `session_id` and `label`. `PATCH` changes labels and `mcp_headers` (`run`, `If-Match`); headers apply to runs accepted afterwards.
- **Inbox entries** list in position order, optionally filtered by status; a single entry shows its disposition.
- **Runs** of a thread list newest first. A run view carries its source entry's payload as `input`, the frozen options, and `attempts` as charged attempts. `PATCH` changes a run's labels (`run`, `If-Match`), sealed runs included.
- **Items** (`GET …/runs/{run}/items`) returns the committed display with `position` (the `{attempt}-{sequence}` stream position it covers, null before the first checkpoint), `dropped` (the earliest items dropped over the display's item limit, [07](07-facts-and-delivery.md#checkpoints-and-display)) and `complete` (the run is sealed). A display object gone between reading the pointer and the object is re-read once, then `unavailable`.
- **Lineage** returns the run and its ancestors through `parent_run_id`, nearest first across fork origins, 50 per page with a `cursor` ([10](10-api.md#collections)).
- **Attempts** returns every attempt of a run, unpaged.

## Failure semantics

A run's `failure` is `{code, message}`. The codes the Service produces:

| Code                                                                   | Cause                                                                                                                                                       | Retry                                          |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `cancelled`                                                            | Interrupt, archive, a parent's cancel, or a cancellation request observed by the worker or recovery                                                         | None                                           |
| `authority_revoked`                                                    | Attempt planning or the supervisor finds the workspace archived, or the principal or its frozen authority no longer valid                                   | None                                           |
| `usage_limit_exceeded`                                                 | The next model request would exceed `max_usage.requests`                                                                                                    | None                                           |
| `model_call_unknown`                                                   | A model call named no model of the agent graph ([04](04-resources.md#models))                                                                               | None                                           |
| `environment_unavailable`                                              | A frozen mount cannot be used, or an environment provider error that is not transient                                                                       | None                                           |
| `payload_too_large`                                                    | Output exceeds `worker.output_bytes`                                                                                                                        | None                                           |
| `lease_expired`                                                        | The last attempt stopped renewing its lease                                                                                                                 | Earlier attempts recover within `max_attempts` |
| `attempt_failed`                                                       | The last attempt failed with an unexpected error or an unavailable dependency                                                                               | Earlier attempts recover within `max_attempts` |
| Harness codes                                                          | The Harness run failed (model, tool, configuration)                                                                                                         | None                                           |
| `checkpoint_incompatible`, other conflict reasons, Service error codes | A deterministic Service error during execution: a conflict fails the run with its reason (such as `checkpoint_incompatible`), any other error with its code | None                                           |
| Admission codes                                                        | The admission policy refused a call ([09](09-runtime.md#extension-points))                                                                                  | None                                           |

A Service failure raised inside a Harness hook, such as the lease proof or a pinned skill's package read during materialization, reaches the worker wrapped in a Harness error that keeps it as the cause, and the worker classifies that cause as if it had been raised directly. An `unavailable` Service error, such as the object store failing while a skill package is read or a package whose bytes no longer match its digest, and a transient environment provider error are not failures of the run: the attempt fails with `attempt_failed` and a later attempt runs within `max_attempts`. Any other Service error fails the run with that refusal, and a lost lease ends the attempt without sealing the run, which lease expiry recovers. Entry failures are `run_ended` (the assigned run failed or was cancelled), `origin_not_committed`, and the refusal's code, or a conflict's reason, at acceptance or delivery.

## Lock discipline

Multi-row domain locks follow thread → run → attempt → the workspace's environment reservation lock → environments, sorting IDs within a set. A transaction that may reserve an instance, or locks a set of instances, takes the reservation lock before any environment row ([06](06-environments.md#mounts)). Resource rows needed for acceptance follow these; resource operations never lock runs or threads. Object I/O happens outside database locks. Claim and renewal take a suffix of the order, never an earlier lock. Child-result delivery and successor acceptance are separate transactions.

## Trade-offs

- **Assignment is durable, offering is not.** A recovered attempt may offer an entry again that an earlier attempt had shown the model but not committed. Replaying that incorporation is accepted; losing or duplicating a committed one is not.
- **No retry.** A failed run never becomes history, so a user resubmits the input instead of the Service guessing which effects to repeat.
- **Handoffs are free.** A planned drain does not charge `max_attempts`, so a run can be handed off any number of times; each handoff needs a draining worker, which claims nothing more.

## Invariants

- A thread has at most one accepted or running run, and a run has at most one live attempt.
- An entry is consumed at most once, in the same transaction that moves the checkpoint pointer naming the state that contains it.
- Every execution-dependent mutation passes the single worker predicate with fresh database time.
- A sealed run's facts never change; only its labels do.
- A lifecycle change and its webhook outbox rows commit together.
- No database session is held across external I/O.
- A failed or cancelled run never becomes head, and nothing starts automatically after it; only an explicit submission or resume continues the thread.
- A child run's result enters its parent thread at most once, keyed by the child run.
