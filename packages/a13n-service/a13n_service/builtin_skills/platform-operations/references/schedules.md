# Schedules

Use the `schedules` tools to manage one-time or recurring Agent invocations. Each trigger sends fixed text or JSON into a new Thread in the Schedule's dedicated Session. Obtain Agent and revision IDs from resource reads; a null `agent_revision_id` follows the current default when the input becomes a Run, while an explicit revision pins it. Preserve unrelated invocation options and Memory mounts when editing.

## Prepare and review

Confirm the intended time, timezone, target Agent, input and revision choice from the user's request or existing configuration. Ask about missing choices that affect the result. Recurring rules use five-field cron and an explicit IANA timezone, such as `Asia/Shanghai`. One-time rules use `type: once` and a future `run_at` with a timezone offset. Resolve relative times against current time and the user's timezone; never assume the server timezone. Do not guess the current time if reliable context is unavailable.

Call `preview_schedule` with the proposed rule and an aware `after` time before creating or changing timing. It is a read even though its HTTP method is POST. Explain the resulting dates and times before requesting write approval. Creation defaults to paused. Use `state: enabled` only when the user explicitly requests starting future scheduled execution. Enabling does not also run the Agent immediately; `dispatch_schedule` is a separate manual trigger, including for paused plans.

Before proposing creation or enablement, explain that the Schedule retains the user's execution authority after this conversation and its short-lived credential end. Signing out or revoking the original API key does not stop it. Pause or archive stops future submissions; disabling the principal or revoking execution access also blocks future submission. Existing submitted work continues. Approval of Schedule management never pre-approves tools called by future Runs.

Creation needs both `write` and `run`. Only the creator may edit, enable or manually dispatch. Another workspace writer may pause or archive. The saved authority cannot be expanded or transferred; a different execution identity requires a new Schedule. Never collect or store credentials in the invocation.

## Versions and retries

Read `get_schedule` before editing, enabling, pausing, archiving or manually dispatching, and send its ETag as `If-Match`. Keep the exact target, request body and original version in the reviewed call. A stale version requires rereading and a new review of changed arguments, not silently substituting a fresh ETag.

Supply `Idempotency-Key` for creation and manual dispatch. Retain the same key for the same intended operation and body when recovering from a lost response; do not create a fresh key and risk duplicate work. Inspect current state first when the outcome is unknown. A different body with the same key is a conflict. A dispatch replay returns the original occurrence, even after it finishes. Supply the required empty `request_body: {}` for enable, pause and dispatch.

Completed one-time Schedules cannot be edited, enabled or manually dispatched; create a new Schedule to run again. Archived Schedules cannot be restored. `schedule_busy` means an earlier occurrence is still pending or has unfinished work; inspect it before attempting another dispatch.

## Inspect results

Use `list_schedules` with supported filters such as `q`, `agent_id` and `state`; follow its cursor without changing filters. These lists do not accept the execution queries' `view=compact` option. Read details for the selected Schedule. Use `get_schedule_status` for progress and `list_schedule_occurrences` for trigger history; request `include: execution` when current execution state is needed. Read `get_schedule_occurrence` for the saved invocation snapshot.

Distinguish Schedule state from occurrence and Run state. A one-time Schedule's `completed` state only ends scheduling. Occurrence `submitted` means input was persisted; occurrence `completed` records a final Run outcome, which can be succeeded, failed or cancelled. Inspect `execution_outcome`, `completion_run_id` and the execution summary instead of equating completion with success. Use existing execution tools to inspect the selected Run and its output. Live Session streams are not MCP tools.
