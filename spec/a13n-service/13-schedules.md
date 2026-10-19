# Recurring Agent schedules

Service owns durable, time-based Agent invocation. Applications own business calendars, data-readiness rules, cross-system workflows and notifications. External schedulers may still use ordinary input APIs. Harness and Workers do not interpret cron expressions.

## Objects and ownership

A **Schedule** belongs to one Workspace and authorizes recurring input submission as its creator. Creating it atomically creates one empty, dedicated Session and returns its immutable `session_id` and relative `stream_url`. Creation does not create a Thread, Run or Occurrence. Replaying creation returns the same Session, including after archival.

Every successful trigger creates a fresh root Thread and first inbox Entry in that Session. Threads have independent conversation histories; they do not inherit the preceding trigger's messages. The Session is a stable observation entry point, not shared conversation context. Its Memory is explicitly `{enabled: false, spaces: []}`; ordinary Session updates reject Memory changes with `conflict: schedule_managed_memory`. Labels remain editable. Explicit `invocation.memories` mounts use the ordinary memory contract.

An **Occurrence** records one processed periodic trigger or accepted manual dispatch. Future cron times are not preallocated. Its submission lifecycle is independent of execution:

| State       | Meaning                                                                                      |
| ----------- | -------------------------------------------------------------------------------------------- |
| `pending`   | Durable intent; no input has been submitted                                                  |
| `submitted` | The new Thread, first inbox Entry and their Occurrence association committed atomically      |
| `skipped`   | The trigger was omitted, for example because of overlap or a missed deadline                 |
| `failed`    | Input submission could not finish, including permanent refusal or exhausted submission retry |
| `cancelled` | An unsubmitted trigger was stopped by a configuration change, pause or archive               |

Only `pending` may transition. All other states are immutable terminal submission evidence. `submitted` does not require a Run: the Entry may be pending, assigned, or failed by ordinary message handling. Inspect Entry/Thread/Run state for execution results. The scheduler never resubmits a persisted input because execution failed.

### Storage

`schedules` (`sch_`) stores:

- identity, organization, Workspace, fixed Session, creator and last editor;
- name, description, labels, `state`, `pause_reason`, cron expression, timezone, maximum lateness and invocation;
- execution principal and immutable `ExecutionAuthority` ceiling;
- immutable create request key/digest, resource version and timestamps;
- `next_fire_at`, `last_processed_fire_at`, `pending_occurrence_id` and `last_occurrence_id`.

Configuration and progress share a row and its lock. Changes confined to the four progress fields do not change configuration `version`, `updated_at` or ETag. Configuration details exclude progress; `/status` reports it. State is `paused` by default, `enabled` only when explicitly requested, or terminal `archived`. A paused reason is `manual`, `authority_invalid` or `target_invalid`. Only enabled schedules have a next fire time. Processed time never moves backwards.

`schedule_occurrences` (`occ_`) stores immutable identity, scope, Schedule/Session association, trigger (`scheduled` or `manual`), planned UTC time and covered range, Schedule version, configuration snapshot, execution authority, principal, manual requester/key/digest and submission deadline. Mutable pending fields are status, reason, attempt count, next attempt, safe last error, resulting Thread/Entry IDs and finished time. Each row has its own resource version and timestamps. Snapshots record the name, cron/timezone, lateness, invocation, timezone-data version and planned UTC offset. They do not contain credentials.

Constraints enforce:

- one Session per Schedule and at most one Schedule per Session, with matching Workspace;
- one periodic Occurrence per `(schedule_id, scheduled_at)`, regardless of configuration version;
- at most one pending Occurrence per Schedule;
- one manual request key per Workspace/requester, and one creation key per Workspace/creator;
- submitted Thread and Entry IDs are both present and individually unique; other states have neither;
- the Entry belongs to the root Thread, and the root Thread, Session and Occurrence scopes agree;
- only pending Occurrences have no finished timestamp; trigger time, snapshot, authority and request evidence cannot change.

All retained records keep their request evidence. Archival does not delete the Session, execution history or Occurrences. There is no automatic retention cleanup.

### Thread provenance

`Thread.schedule_occurrence_id` is nullable and immutable. Scheduled root Threads receive it internally; asynchronous child Threads inherit it recursively. Resume and later inputs in an owned Thread retain that association. Explicit forks and independent new Threads have no association, even in the same Session. Ordinary submission requests cannot set it.

Run provenance is derived from the Run's Thread as nullable `schedule_origin: {schedule_id, occurrence_id}`; it is not inferred from Session membership. Existing Run triggers remain unchanged: initial same-transaction acceptance uses `input`, later inbox acceptance uses `queued`. Occurrences store no Run ID; the initial Entry's assignment and ordinary Thread history identify execution.

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

The invocation contains `agent_id`, required nullable `agent_revision_id`, `payload`, optional ordinary `RunOptions`, and explicit Memory mounts. A revision ID pins a revision; explicit null follows the Agent's current default when ordinary message handling creates the Run. Submission does not freeze a revision for an Entry that has no Run yet. No existing Session/Thread binding, imported history, caller MCP headers, temporary attachments or URL input is accepted. Payload content is fixed text or JSON; it is not a template language. Configuration is at most 256 KiB and existing stricter field/input limits apply.

The time rule is `{type: "cron", expression, timezone}`. Expressions have five fields: minute, hour, day-of-month, month, day-of-week. Numeric values, `*`, lists, ranges and steps are supported; Sunday accepts 0 or 7. Seconds, years, named shortcuts and extended operators are rejected. Restricted day-of-month and day-of-week combine with OR. The timezone is an explicit IANA name; all stored instants are UTC. Preview and dispatch use the same locked timezone-data release.

Nonexistent local times during a forward clock change do not fire. Repeated local times map to two distinct UTC instants, each subject to overlap. Date searches are bounded to eight years. `0 9 * * *` with `Asia/Shanghai` means 09:00 local daily; `*/15 * * * *` means local minutes 00, 15, 30 and 45.

Enable and time-affecting configuration edits select a next time strictly after database time and any already processed time. They do not backfill paused intervals or rerun already covered UTC times. Maximum lateness defaults to 300 seconds, bounded to 60–86400, and limits input submission only. It does not impose a deadline on a submitted Entry's Run creation or completion. Sweep intervals and execution admission mean a schedule does not promise exact start or completion time.

## Management API

All paths below are relative to `/api/v1` and use the ordinary Workspace, authentication, pagination and error contracts. The Schedule endpoints are HTTP-only; MCP and Composer admission are outside this surface.

| Method and path                                | Contract                                                   |
| ---------------------------------------------- | ---------------------------------------------------------- |
| `POST /schedules`                              | Idempotency-Key; first 201, replay 200; Schedule with ETag |
| `GET /schedules`                               | Configuration page                                         |
| `GET /schedules/{id}`                          | Configuration and ETag                                     |
| `PATCH /schedules/{id}`                        | If-Match; partial configuration update                     |
| `POST /schedules/{id}/enable`                  | If-Match; empty object                                     |
| `POST /schedules/{id}/pause`                   | If-Match; empty object                                     |
| `DELETE /schedules/{id}`                       | If-Match; soft archive, 204                                |
| `POST /schedules/preview`                      | Time rule, aware `after`, count 1–10 (default 5)           |
| `GET /schedules/{id}/status`                   | Progress, pending/latest IDs and unfinished work           |
| `GET /schedules/{id}/occurrences`              | Compact trigger history page                               |
| `GET /schedules/{id}/occurrences/{occurrence}` | Full trigger snapshot and submission details               |

Configuration responses include fixed Session/stream URL, Workspace, principal/creator/editor, state/reason, version and timestamps. They omit saved authority and request keys. PATCH can replace name, description, labels, time rule, lateness or invocation; nested time rule and invocation are replaced completely. Only description accepts explicit null. State, identity and progress cannot be patched. Labels do not propagate to Runs; invocation Run options own Run labels.

Authorization precedes replay lookup, which precedes mutable configuration validation and mutation preconditions. A key with a different command digest returns `conflict: idempotency_key_reused`. Creation keys survive archival. Missing If-Match is 428, stale is 412; no-op edits keep their ETag. Archived schedules remain readable and replayable but cannot be restarted or edited.

Schedule pages sort by `(created_at DESC, id DESC)` and filter state, Agent, principal, labels and name/description text. Archived schedules are excluded unless explicitly selected. Occurrence pages use the same order and filter status, trigger, half-open creation interval and current unfinished-work state. Cursors bind Workspace, Schedule and filters. Default page size is 50, bounded to 1–100. Details load input snapshots separately from compact history.

Status reports observation time, next fire time, pending/latest IDs and whether any submitted Occurrence has unfinished work. Its busy page contains up to 20 root Thread/Entry references; continue with the history endpoint's `unfinished=true` filter. Reading does not advance scheduling. Status has no configuration ETag.

Time-affecting edits cancel pending intent; pause/archive also cancel manual pending intent created while paused. Before discarding an elapsed old timeline, record one skipped Occurrence covering that range with the old configuration and the relevant change reason. Name/description/label edits do not alter pending work or the timeline. Configuration mutation and submission serialize on the Schedule lock. Work already submitted is never withdrawn by pause or archive, even when it has no Run yet.
