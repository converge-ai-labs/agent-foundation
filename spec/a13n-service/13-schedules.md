# Agent schedules

Service owns durable, time-based Agent invocation. Applications own business calendars, data-readiness rules, cross-system workflows and notifications. External schedulers may still use ordinary input APIs. Harness and Workers do not interpret cron expressions.

## Objects and ownership

A **Schedule** belongs to one Workspace and authorizes one-time or recurring input submission as its creator. Its mutable `target` selects `mode: new_thread` or `mode: thread`, with optional `session_id` and `thread_id`. The default creates an empty Session and starts a new Thread on every trigger. A supplied Session selects where new Threads are created. Thread mode binds an existing Thread, or creates one empty Thread during Schedule creation/update if its ID is omitted. An omitted Session ID creates a Session when no existing Thread identifies one. Responses return the resolved target, current `session_id` and relative `stream_url`. A Session’s legacy singular `schedule_id` is populated only when exactly one Schedule currently targets it. Replaying creation returns the same Schedule without creating more resources, including after archival. Creation creates no Run or Occurrence and sends no message.

Targets must be ordinary conversation resources in the same Workspace; an archived Thread is invalid. A supplied Session and Thread must agree. Busy and waiting Threads can be selected. Several Schedules may target the same Session or Thread. All scheduled messages use `next_run`: they queue for separate execution rather than steering current work. Fresh Threads have independent conversation histories; a fixed Thread continues its own history.

A newly created Session starts with Memory `{enabled: false, spaces: []}`. Existing Sessions retain their settings and ordinary Memory updates remain available. Explicit `invocation.memories` mounts apply when creating Threads, including a fixed Thread at configuration time; sending to an existing Thread leaves its mounts unchanged.

Target-only edits change the Schedule version without cancelling pending Occurrences or changing the timeline. No target snapshot is added. A pending Occurrence's saved Session is used for new-Thread submission. Fixed-Thread submission uses the Schedule's current Thread, because pending Occurrences do not reserve a Thread; its actual Session is recorded at submission. Target resolution never combines a saved Session with an unrelated Thread. Submitted work and historical locations never move. The existing Schedule overlap rule still includes work submitted to previous targets.

An **Occurrence** records one processed automatic trigger or accepted manual dispatch. Future trigger times are not preallocated. Its lifecycle records submission and the first completion of its trigger message execution:

| State       | Meaning                                                                                                                                                                |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pending`   | Durable intent; no input has been submitted                                                                                                                            |
| `submitted` | The target Thread, trigger inbox Entry and their Occurrence association committed atomically                                                                           |
| `completed` | The trigger message execution (including waiting/resume successors) reached its first completed, failed or cancelled Run; `execution_outcome` distinguishes the result |
| `skipped`   | The trigger was omitted, for example because of overlap or a missed deadline                                                                                           |
| `failed`    | Input submission could not finish, including permanent refusal or exhausted submission retry                                                                           |
| `cancelled` | An unsubmitted trigger was stopped by a configuration change, pause or archive                                                                                         |

`pending` transitions to `submitted`, `skipped`, `failed` or `cancelled`. Only `submitted` may then become `completed`; every other final state is immutable. `submitted` does not require a Run: the Entry may be pending, assigned, or failed by ordinary message handling. `failed` and `cancelled` describe submission, while `completed` means execution ended, including execution failure or cancellation. The scheduler never resubmits a persisted input because execution failed.

### Storage

`schedules` (`sch_`) stores:

- identity, organization, Workspace, current target Session and optional fixed Thread, creator and last editor;
- name, description, labels, `state`, `pause_reason`, time-rule type, cron expression/timezone or one-time UTC instant, maximum lateness and invocation;
- execution principal and immutable `ExecutionAuthority` ceiling;
- immutable create request key/digest, resource version and timestamps;
- `next_fire_at`, `last_processed_fire_at`, `pending_occurrence_id` and `last_occurrence_id`.

Configuration and progress share a row and its lock. Changes confined to the four progress fields do not change configuration `version`, `updated_at` or ETag. Configuration details exclude progress; `/status` reports it. State is `paused` by default, `enabled` only when explicitly requested, or terminal `archived`. A paused reason is `manual`, `authority_invalid` or `target_invalid`. Only enabled schedules have a next fire time. Processed time never moves backwards.

An enabled one-time Schedule has no next fire time after its automatic Occurrence is recorded, while pending submission can still retry. A one-time Schedule becomes `completed` when its automatic trigger is submitted, skipped for lateness/overlap, or fails permanently or after exhausted retries. Completion ends scheduling, not Agent execution; a submitted Entry may still have no Run. Automatic completion changes configuration version, `updated_at` and ETag. Completed Schedules are readable and replayable but cannot be edited, paused, enabled or manually dispatched; archival remains allowed. Another one-time submission requires a new Schedule. Cron Schedules never become completed. A manual Occurrence does not consume or complete a one-time Schedule; an already accepted manual Occurrence can finish after an overlapping automatic trigger completes it.

`schedule_occurrences` (`occ_`) stores immutable identity, scope, Schedule association, trigger (`scheduled` or `manual`), planned UTC time and covered range, Schedule version, configuration snapshot, execution authority, principal, manual requester/key/digest and submission deadline. The Session starts as the Schedule target and may change while pending to record actual submission. Other mutable pending fields include status, reason, attempt count, next attempt, safe last error, resulting Thread/Entry IDs and finished time. Completion adds nullable `execution_outcome` (`succeeded`, `failed` or `cancelled`), `completion_run_id` and `completed_at`; all three are present exactly when status is `completed`. `finished_at` retains the submission finish time. Completion advances the Occurrence version and `updated_at` without changing submitted input evidence. Each row has its own resource version and timestamps. Snapshots record the name, time rule, lateness, invocation, timezone-data version and planned UTC offset. One-time rules use an absolute UTC instant and have no calendar offset. They do not contain credentials.

Constraints enforce:

- targets and occurrence locations remain in the Schedule Workspace;
- one automatic Occurrence per `(schedule_id, scheduled_at)`, regardless of configuration version;
- at most one pending Occurrence per Schedule;
- one manual request key per Workspace/requester, and one creation key per Workspace/creator;
- submitted and completed Thread and Entry IDs are both present; Entry IDs are unique but Threads can be reused; other states have neither;
- the Entry belongs to the target Thread, and the Thread, actual Session and Occurrence scopes agree;
- only pending Occurrences have no finished timestamp; trigger time, snapshot, authority and request evidence cannot change.

All retained records keep their request evidence. Archival does not delete the Session, execution history or Occurrences. There is no automatic retention cleanup.

### Thread provenance

`Thread.schedule_occurrence_id` remains an immutable creation-origin marker for newly scheduled Threads and children created by an occurrence-triggered Run. It does not describe later messages in a reusable Thread and is not used to complete Occurrences. Explicit forks and independent new Threads have no marker. Ordinary requests cannot set internal provenance.

Scheduled Entries carry an internal nullable `schedule_occurrence_id`. Their initial Run records `occurrence_trigger_entry_id`; waiting/resume successors inherit it. Ordinary messages, forks, child executions and result-notification successors do not inherit this completion association. The final Run retains it as history; subsequent Runs do not continue the association. Run `schedule_origin: {schedule_id, occurrence_id}` is derived from this Run association, never from Session membership or the Thread's creation marker. Ordinary Runs have no origin and perform no Occurrence completion update.

Existing Run triggers remain unchanged: same-transaction acceptance uses `input`, later inbox acceptance uses `queued`. The trigger Entry's assignment identifies the initial Run; `completion_run_id` identifies the final Run in its waiting/resume sequence.

## Authority

Creating a Schedule is explicit durable delegation. The creator becomes its execution principal; clients cannot select another identity. Its authority is the creator's current Workspace verbs, already narrowed by credential confinement and any accepted execution ceiling. Future grants never widen the saved ceiling. Submission checks the current principal, Workspace and grants against it. Manual dispatch further narrows authority to the caller's current credential.

Logout, credential expiry and revocation of the original API key do not independently revoke saved recurring authority. Pause/archive, principal disablement, grant revocation or Workspace archival stop future submission. Existing Run authority checks remain unchanged. Losing `write` alone does not stop previously delegated work when `run` is still permitted.

| Operation                        | Workspace verbs | Additional restriction                              |
| -------------------------------- | --------------- | --------------------------------------------------- |
| Read/list/status/history/preview | `read`          | Tenant isolation                                    |
| Create                           | `write`, `run`  | Caller is the execution principal                   |
| Edit or enable                   | `write`, `run`  | Creator only, within the original authority ceiling |
| Pause or archive                 | `write`         | Any authorized member may stop work                 |
| Manual dispatch                  | `run`           | Creator only, within both authority ceilings        |

Identity and authority cannot be transferred or expanded. Another principal creates another Schedule. Existing Run control follows its own authorization, not Schedule ownership. Tool approval is never automatic.

## Configuration and time

The invocation contains `agent_id`, required nullable `agent_revision_id`, `payload`, optional ordinary `RunOptions`, and explicit Memory mounts. A revision ID pins a revision; explicit null follows the Agent's current default when ordinary message handling creates the Run. Submission does not freeze a revision for an Entry that has no Run yet. Target binding is configured separately from invocation. Imported history, caller MCP headers, temporary attachments and URL input are not accepted. Payload content is fixed text or JSON; it is not a template language. Configuration is at most 256 KiB and existing stricter field/input limits apply.

A recurring time rule is `{type: "cron", expression, timezone}`. Omitted `type` remains accepted as cron for compatibility. Expressions have five fields: minute, hour, day-of-month, month, day-of-week. Numeric values, `*`, lists, ranges and steps are supported; Sunday accepts 0 or 7. Seconds, years, named shortcuts and extended operators are rejected. Restricted day-of-month and day-of-week combine with OR. The timezone is an explicit IANA name; all stored instants are UTC. Preview and dispatch use the same locked timezone-data release.

Nonexistent local times during a forward clock change do not fire. Repeated local times map to two distinct UTC instants, each subject to overlap. Date searches are bounded to eight years. `0 9 * * *` with `Asia/Shanghai` means 09:00 local daily; `*/15 * * * *` means local minutes 00, 15, 30 and 45.

A one-time time rule is `{type: "once", run_at}`. `run_at` is an offset-aware timestamp normalized to UTC; naive timestamps, relative delays and cron fields are rejected. Create, enable from paused, and time-affecting edits require a time strictly after database time and any processed time. Metadata-only edits and idempotent create replay do not revalidate the saved instant against the current time. Preview returns the one UTC instant if it is strictly after `after`, otherwise an empty list; its `local` value is also UTC. Clients display it in their chosen timezone. Precision follows the timestamp, but execution still waits for a sweep and ordinary admission.

Enable and time-affecting configuration edits select a next time strictly after database time and any already processed time. They do not backfill paused intervals or rerun already covered UTC times. A paused one-time Schedule whose instant has passed must be changed to a future time before enable. Maximum lateness defaults to 300 seconds, bounded to 60–86400, and limits input submission only. It does not impose a deadline on a submitted Entry's Run creation or completion. Sweep intervals and execution admission mean a schedule does not promise exact start or completion time.

## Management API

All paths below are relative to `/api/v1` and use the ordinary Workspace, authentication, pagination and error contracts. All Schedule management endpoints below are also admitted through MCP and the internal `schedules` platform toolset. List, detail, status, occurrence history and time preview have a `read` effect; creation, update, enable, pause, archive and manual dispatch have a `write` effect. Internal reads default to allow and writes to ordinary Harness review. Live Session streams remain HTTP-only.

A Run platform credential creates the Schedule as its authenticated principal, with the intersection of current grants and the originating Run's accepted execution ceiling. The Schedule saves this authority, never the short-lived credential; completion or cancellation of the originating Run does not revoke the Schedule. Later submission still rechecks the principal's current authority. Tool selection admits Schedule management, not direct Run submission or control. Approval does not expand business permissions. ETags, request keys, creator-only actions and unknown-outcome rules are identical across HTTP and MCP.

| Method and path                                | Contract                                                          |
| ---------------------------------------------- | ----------------------------------------------------------------- |
| `POST /schedules`                              | Idempotency-Key; first 201, replay 200; Schedule with ETag        |
| `GET /schedules`                               | Configuration page                                                |
| `GET /schedules/{id}`                          | Configuration and ETag                                            |
| `PATCH /schedules/{id}`                        | If-Match; partial configuration update                            |
| `POST /schedules/{id}/enable`                  | If-Match; empty object                                            |
| `POST /schedules/{id}/pause`                   | If-Match; empty object                                            |
| `DELETE /schedules/{id}`                       | If-Match; soft archive, 204                                       |
| `POST /schedules/preview`                      | Time rule, aware `after`, count 1–10 (default 5)                  |
| `POST /schedules/{id}/dispatch`                | Idempotency-Key and If-Match; empty object; first 202, replay 200 |
| `GET /schedules/{id}/status`                   | Progress, pending/latest IDs and unfinished work                  |
| `GET /schedules/{id}/occurrences`              | Compact trigger history page                                      |
| `GET /schedules/{id}/occurrences/{occurrence}` | Full trigger snapshot and submission details                      |

Configuration responses include the resolved target and current Session/stream URL, Workspace, principal/creator/editor, state/reason, version and timestamps. They omit saved authority and request keys. PATCH can replace name, description, labels, time rule, lateness, invocation or target; nested time rule, invocation and target are replaced completely. Only description accepts explicit null. State, identity and progress cannot be patched. Labels do not propagate to Runs; invocation Run options own Run labels.

Authorization precedes replay lookup, which precedes mutable configuration validation and mutation preconditions. A key with a different command digest returns `conflict: idempotency_key_reused`. Creation keys survive archival. Missing If-Match is 428, stale is 412; no-op edits keep their ETag. Archived schedules remain readable and replayable but cannot be restarted or edited.

Schedule pages sort by `(created_at DESC, id DESC)` and filter state, Agent, principal, labels and name/description text. Archived schedules are excluded unless explicitly selected. Occurrence pages use the same order and filter status, trigger, half-open creation interval and current unfinished-work state. Cursors bind Workspace, Schedule and filters. Default page size is 50, bounded to 1–100. Details load input snapshots separately from compact history.

Occurrence list and detail reads accept `include=execution`. Without it, responses omit `execution`. Expanded reads add `initial_entry_status`, nullable `initial_run: {id, status}`, `has_unfinished_work` and `observed_at` under each Occurrence's `execution`. Status values use the ordinary Entry and Run enums. The initial Run is the one assigned to the first Entry; later continuation and child Runs do not replace it. An Occurrence without submitted input has null initial Entry/Run values and no unfinished execution. A submitted Entry may still have no Run. Unfinished work uses the same trigger-execution completion predicate as overlap; false does not imply success. Output remains available through the ordinary Run items API.

An expanded page reads submission and execution from one short read-only repeatable-read transaction, with a bounded batch read for the page's execution facts. `observed_at` records the execution query time. Execution observations can change without changing the Occurrence's `version` or `updated_at`; they are not stored in its immutable snapshot. The transition from `submitted` to `completed` changes the durable state and its version independently of these observations. Expansion changes neither filtering nor ordering, and cursors can be reused with or without it. Existing Workspace read authorization applies to the entire response. Command responses and Session occurrence frames do not include execution observations.

Status reports observation time, next fire time, pending/latest IDs and whether any submitted Occurrence has unfinished work. Its busy page contains up to 20 root Thread/Entry references; continue with the history endpoint's `unfinished=true` filter. Reading does not advance scheduling. Status has no configuration ETag.

Time-affecting edits cancel pending intent; pause/archive also cancel manual pending intent created while paused. Before discarding an elapsed old timeline, record one skipped Occurrence covering that range with the old configuration and the relevant change reason. Name/description/label edits do not alter pending work or the timeline. Configuration mutation and submission serialize on the Schedule lock. Work already submitted is never withdrawn by pause or archive, even when it has no Run yet.

## Dispatch and overlap

Manual dispatch is allowed while paused. It authorizes the current caller and original ceiling before replay lookup, then checks If-Match and whether the Schedule is archived or completed. A pending Occurrence or unfinished submitted Occurrence refuses a new request with `conflict: schedule_busy`, without creating a record. Request keys are scoped to Workspace/requester across schedules. A replay returns the original Occurrence even after configuration changes or archival. Dispatch does not change the automatic timeline, including a one-time trigger. HTTP 202 means intent was saved, not that execution started.

The pending processor locks Schedule before Occurrence and checks database time against the immutable deadline. Submitted Occurrences remain unfinished until their own trigger message execution ends. `waiting` is not completion: its resume successors retain the same trigger Entry ID. A submitted input with no Run remains unfinished, even if its Entry was rejected or withdrawn. Archival alone does not establish completion. On a completed, failed or cancelled Run, a non-null `occurrence_trigger_entry_id` conditionally completes the matching submitted Occurrence in the same transaction, matching Workspace, Thread and trigger Entry. It saves the Run ID, seal time and mapped outcome (`completed` maps to `succeeded`) and stages completion webhooks. No recursive Run query or Thread-wide first-terminal query is required. Completion never reverses; later messages, children, forks and result deliveries do not reopen it.

Manual and automatic triggers use the same processor. It creates a Thread and first Entry or locks an existing open Thread and appends an Entry, then calls ordinary acceptance. The Entry's occurrence marker is persisted before acceptance. Input and submitted Occurrence evidence commit atomically, including the actual Session, Thread and Entry IDs. A receipt without a Run, including a failed Entry, remains submitted. `GET /threads` supports `occurrence_id` to find the submitted Thread and Threads with that creation origin.

An exception rolls back the entire candidate submission, including after-commit callbacks. A separate short failure transaction reacquires the same locks and rereads the Occurrence before recording an attempt; it does nothing if a lost commit response or concurrent pause already finished it. Transient unavailability, database conflicts, request timeouts and explicit rate limits retry after 5, 10, 20, then 40 seconds, honoring a longer Retry-After. Attempts are bounded by `schedules.dispatch_max_attempts`; exhausted attempts fail with `dispatch_attempts_exhausted`. The deadline takes precedence and skips with `missed_deadline`. Before-input permanent authority/target refusal fails the Occurrence and automatically pauses an open Schedule unless that failure completes its one-time automatic trigger, with an audit for automatic pause naming the saved principal and reason. A completed Schedule never returns to paused because an earlier manual Occurrence fails. Error history stores stable codes, not raw exception messages.

Terminal Occurrences clear `next_attempt_at`. Submission repeats the submitted-Occurrence completion check under the Schedule lock; overlap skips the pending trigger. Pause/archive serialize on the same Schedule lock and only cancel unsubmitted intent. They never withdraw an already persisted input. Submission logging runs after commit and includes Schedule, Occurrence and resulting Thread/Entry identifiers without input content.

## Periodic scanning

Every control/all replica runs `dispatch_schedules` using the ordinary sweep lifecycle: startup jitter, a five-second default delay after each pass, cancellation on shutdown and a 30-second pass deadline. Workers do not scan calendars. Pending retries and due schedules have independent tasks and `schedules.batch` allowances (default 100 each), so one backlog does not consume the other's allowance.

For cron, transaction A selects one enabled due Schedule using its ordered due index and `FOR UPDATE SKIP LOCKED`, then reads database time T. It finds the latest matching instant L no later than T and records a single Occurrence covering `[next_fire_at, L]`; older missed matches are coalesced rather than replayed. If `T >= L + max_lateness_seconds`, it skips with `missed_deadline`. Otherwise a pending Occurrence or unfinished submitted Occurrence skips with `overlap`; otherwise it saves pending intent. The same commit advances `last_processed_fire_at` to L and `next_fire_at` strictly beyond T and processed time. An existing unique planned instant only advances progress; its evidence is never changed or reposted.

For a one-time Schedule, transaction A uses its persisted `run_at` as L and `covered_from_at`, applies the same lateness/overlap checks, and atomically clears `next_fire_at` with the Occurrence and processed time. It does not search for another matching time. A terminal skip completes the Schedule in A; pending submission completes it in B after submission succeeds, is skipped, or finally fails. Pause/archive cancel pending intent and keep the explicitly requested state; time-affecting edits cancel the old intent and require a new future instant. Old elapsed intent discarded by these management actions is recorded with the corresponding management reason and does not complete the replacement plan.

After A commits and releases its locks, transaction B uses the ordinary pending processor. A crash before A commits leaves the old timeline; a crash between A and B leaves discoverable pending intent; a lost response after B commits is resolved by reading that terminal Occurrence. Pending discovery orders by the earlier of next attempt and deadline, so backoff cannot hide expiry. Schedule edits, pause and archival share the Schedule lock. Changes confined to runtime progress do not change configuration ETags; transition to `completed` does.

Existing cron requests and stored snapshots remain valid. Before creating one-time Schedules, replace old control/all replicas and update clients that assume every time rule is cron or every Schedule has only the previous three states. All replicas must use the same cron and timezone-data versions. Stop old control replicas before upgrading time interpretation, retain already persisted UTC instants and calculate subsequent times strictly after them. The immutable Occurrence snapshot identifies the timezone-data version used. No per-Schedule timer, new process role, Redis scheduling queue, leader election or generic jobs table is introduced.

The target migration preserves the old completion decision before enabling reusable Threads. For each historical submitted Occurrence whose Thread already has a completed, failed or cancelled Run, it records `completed`, the earliest final Run (ordered by seal time and ID), its seal time and outcome. This includes a rejected trigger with no Run followed by a completed manual message. Original submission evidence and `finished_at` remain unchanged; the newly recorded completion advances the Occurrence version and `updated_at`. Already completed Occurrences and still-unfinished work remain unchanged, and no historical webhook is emitted. Runtime unfinished-work checks use the durable Occurrence state, without a legacy Thread-wide fallback.

The same migration fills Entry markers and Run trigger associations only for initial Runs and their waiting/resume chains, preserving those records’ versions and timestamps. Historical manual Runs do not acquire a trigger association merely because the old completion rule used them. Both backfills use bounded batches within the migration transaction. A non-unique Session index replaces the former unique constraint so Session lookups remain indexed while multiple Schedules can share a Session. Stop old control and worker replicas before migrating; they cannot write the new associations. Target downgrade refuses fixed targets, multiple Schedules in one Session, and histories spanning different target Sessions. Downgrading past the original completion migration refuses completed Occurrences; use a forward repair after adoption.

## Lifecycle webhooks

The existing [subscription and delivery contract](07-facts-and-delivery.md#lifecycle-webhooks) covers Schedule events. `schedule.enabled`, `schedule.paused`, `schedule.completed` and `schedule.archived` describe state changes; enabled creation emits `schedule.enabled`, while paused creation emits no state-change event. Replays and unchanged state/pause reason emit nothing. Automatic pauses include their reason. `schedule.completed` ends one-time scheduling, not execution.

`schedule_occurrence.submitted`, `.skipped`, `.failed`, `.cancelled` and `.completed` describe the corresponding Occurrence transitions, including skips created directly by planning or configuration changes. No event is emitted for pending creation or intermediate submission retries. `schedule_occurrence.completed` includes the durable execution result, completion time and final Run ID. Every event and its recorded transition commit together. Receivers still deduplicate by delivery ID and must accept unordered delivery.

## Session observation and Console

The currently targeted Session is readable before any trigger. It uses the ordinary `GET /sessions/{session_id}/stream` endpoint without a view or history-limit parameter; the returned `stream_url` names this endpoint. Every Session keeps full Thread selection and the existing four output events. Schedule association only adds the state events below; it never filters the Session's Threads by occurrence age, status or ownership.

Sessions with Schedule targets or Occurrence history add two state frames, both scoped by `session_id` and `schedule_id`:

- `schedule`: compact `configuration` (identity, name, state/pause reason, version, execution principal, time rule, maximum lateness) and `progress` (next fire, pending and latest Occurrence IDs).
- `occurrence`: one compact `OccurrenceSummary`, including state, execution completion fields and resulting root Thread/Entry IDs. It contains neither invocation snapshot nor saved authority.

These are current-state observations, not an event log. Intermediate states may coalesce. There is no change cursor, acknowledgement, replay log or extra sync frame. Each connection begins with current Schedule and Occurrence state, then the ordinary Thread and Run output frames. Empty Sessions still emit their Schedule state. A no-Run failure, skip or cancellation remains visible as an Occurrence.

Occurrence snapshots query by each Occurrence's own Workspace and Session, independently of the Schedule's current target. Schedule frames describe Schedules currently targeting the Session and are keyed by Schedule ID. A Session may have several Schedules or only historical Occurrences. No events are broadcast to other historical Sessions. The initial snapshot includes every Occurrence summary and every Thread in the Session, including completed work, child Threads and independent interactive work. Refreshes emit changed state and discover new Occurrences and Threads without a historical window. State and authority checks use a short read-only repeatable-read transaction; object and Redis I/O happen after it closes. Readers of the same Session share one subscription. Snapshot and retained observation state grow with the Session's records, as Thread observation does for ordinary Sessions.

Reconnect starts a fresh snapshot. Consumers also reread their visible history page and selected execution details: execution observations may change between Occurrence state transitions. History pages, full Occurrence details and occurrence-filtered Thread pages remain ordinary HTTP reads; saved output uses the existing Run items API. Redis loss does not erase Schedule/Occurrence state.

Console provides a paginated Schedule list with completed-state filtering, creation with recurring/one-time timing selection, time preview, configuration editing, enable/pause/archive, manual dispatch and paginated trigger history. It labels completed one-time scheduling separately from Agent execution. It distinguishes submission failure/cancellation from execution outcomes, displays completion time and reuses the Run output view. Workspace administrators can create a webhook from a Schedule detail page with its Schedule filter prefilled. Creator-only actions follow the authority table; other writers may stop work. Drafts retain their original ETag, and unchanged create/dispatch retries retain their request key. The Schedule detail page does not subscribe to Session SSE. Users refresh configuration, status and paginated history manually; successful user commands also refresh the page. The interface explains durable delegation and that signing out or revoking the original credential does not stop future triggers.

The Schedule detail page displays the target Agent, selected revision and current target Session/Thread, with links to the Agent and Session pages. The Session link opens its execution history, or an empty Session before the first trigger; it does not redirect back to the Schedule. Explicit historical Thread links load only the selected Thread. It displays every text and JSON input part in saved order, separately from the Schedule description. Readers can inspect the complete input without opening the editor. When editing is unavailable, the page explains whether the Schedule is archived or completed, the signed-in account is not its creator, or the caller lacks the required Workspace verbs. Creator-only controls compare the signed-in principal with `created_by_id`, matching backend authorization.

When creating a one-time Schedule, the date/time picker disables past dates and elapsed time choices, refreshes those limits while open, and rechecks the selected instant before applying or submitting it. Existing saved one-time timing remains editable without forcing a change during metadata-only updates.

The creation form starts with Enable immediately unchecked. Selecting it changes the submit action to Create and enable and sends `state: "enabled"` in the same create request; otherwise it sends `state: "paused"`. Enabling begins future scheduled execution, without an extra immediate dispatch. The edit form does not expose this creation-only choice or include state in configuration updates.

The fields editor lists revisions for the selected Agent. Its Default option writes `agent_revision_id: null`; selecting a numbered version pins that revision. Changing the Agent resets the version choice to Default. Existing pinned revisions remain visible even if the revision list is unavailable.

The invocation editor allows incomplete drafts to move between fields and JSON without submission validation. Opening JSON preserves the current unsaved field draft. Returning without JSON changes restores those fields exactly; edited JSON transfers only when its structure can be represented by the fields editor, preserving unrepresented configuration. Invalid syntax or incompatible content marks the JSON input as invalid without an inline error banner. Attempting to return to fields opens a dialog with localized error details, including line and column for syntax errors, and actions to continue editing or discard JSON changes and restore the draft from before opening JSON. The error dialog appears above the still-visible editor. Clicking outside it or pressing Escape closes only the error dialog and preserves the editor and its draft. Discard is available only when that prior field draft exists. Discarding does not alter other Schedule fields or persisted data. Both editing modes validate the complete invocation before submission.

Console history requests execution expansion instead of fetching each row's Entry and Run separately. Refresh rereads the visible page. Historical links use each Occurrence's actual Session and Thread. `observed_at` describes an observation and is not a resource version.
