---
title: Schedules
description: Run an agent once or periodically and inspect each trigger's input and result.
---

A Schedule sends fixed text or JSON to an Agent once at a specified time or at recurring times. Each trigger creates a new Thread in the Schedule's dedicated Session, so it starts with an independent conversation history. The Session is a stable place to observe the work.

## Create and manage a schedule

In Console, open **Schedules → Create schedule**:

1. Enter a name and choose **Recurring** or **One time**. For recurring work, enter a five-field cron expression and an explicit IANA time zone. For one-time work, choose a future date and time in the displayed browser time zone. For example, `0 9 * * *` with `Asia/Shanghai` runs daily at 09:00 local time.
2. Select **Preview next times** to check the local and UTC instants. Daylight-saving gaps do not fire; a repeated local time has two distinct instants, subject to the overlap rule.
3. Choose an Agent and enter its input. Leave the revision empty to follow the latest default revision; provide a revision ID to pin it. The JSON editor accepts the full invocation, including Run options and explicit Memory mounts.
4. Create the Schedule. It starts paused. Select **Run once** to test it or **Enable** to activate automatic submission.

Creation needs Workspace `write` and `run`. Only the creator can edit, enable or manually dispatch the Schedule. Another member with `write` may pause or archive it. Archival stops future triggers but retains the history and cannot be undone. To schedule work under another identity, create a new Schedule as that identity.

**The Schedule keeps its creator's execution authorization. Signing out, credential expiry or revoking the original API key does not stop it.** Pause or archive the Schedule to stop future work. Disabling the principal or revoking its execution access also stops future submission. Tool approval still applies.

Changes affect future triggers. Time or invocation changes cancel input that has not yet been submitted. Pause and archive also cancel pending input; they do not cancel an input or Run that was already submitted. The maximum delay is the time allowed to submit input, not a Run completion deadline.

## One-time schedules

A one-time schedule submits input at one specified instant. Create it with a future time, then enable it. If you pause it until that time has passed, choose a new future time before enabling it again. **Run once** is a separate manual action; it does not consume the automatic trigger. Unfinished manual work can cause the automatic trigger to be skipped by the ordinary overlap rule.

When submission succeeds, is skipped, or finally fails, the schedule becomes `completed`, displayed as **Scheduling finished**. This does not mean the agent has finished or succeeded. Inspect the trigger's submission and execution results. A completed schedule can be read or archived; it cannot be edited, enabled or manually dispatched. Create a new schedule to run again.

Service recovery uses the same maximum delay as recurring work: the default is 300 seconds. A trigger recovered within that window can still submit; a later trigger is recorded as `skipped` with `missed_deadline`, and scheduling finishes. Pending submissions retry within the original window. Pausing or archiving cancels pending submission without withdrawing already submitted work.

## Read the result

The detail page separates three facts:

| Fact       | What it tells you                                                                                                                                                                   |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Occurrence | `pending` means intent was saved; `submitted` means a Thread and inbox Entry were saved. `skipped`, `failed` and `cancelled` concern submission. `completed` means execution ended. |
| Input      | Whether the inbox Entry is still pending, assigned to a Run, consumed or failed.                                                                                                    |
| Run        | The execution result, including waiting for approval or user input.                                                                                                                 |

**Submitted does not mean execution succeeded, and completed does not mean success.** A trigger becomes `completed` when its root thread first reaches a completed, failed or cancelled run. `execution_outcome` is `succeeded`, `failed` or `cancelled`; `completion_run_id` and `completed_at` identify that run and time. `finished_at` keeps the submission finish time. Waiting and attempt retries do not complete the trigger. Select **View output** to open the usual Run view, including its output, tools and queued input. Later inputs in that Thread retain the same Schedule origin. Explicit forks and independent Threads do not.

A trigger is skipped while an earlier trigger is pending submission or its submitted root thread has no run with status `completed`, `failed` or `cancelled`. Waiting and resumed runs remain unfinished until one of these statuses appears. Later manual conversation, forks, child tasks and their result delivery do not delay the next trigger. Submitted input without a run remains unfinished, even if the input was rejected or withdrawn; archiving the thread alone does not complete it. Missed times are coalesced to the latest due time; the scheduler does not replay every missed tick. Transient submission failures retry within the maximum delay and attempt limit. Execution failures do not repost the input.

## Use the HTTP API

All routes use the ordinary [Workspace and authentication headers](http.md#workspace). `POST /api/v1/schedules` requires an `Idempotency-Key` and accepts, for example:

```json
{
  "name": "Morning report",
  "schedule": {"type": "cron", "expression": "0 9 * * *", "timezone": "Asia/Shanghai"},
  "invocation": {
    "agent_id": "AGENT_ID",
    "agent_revision_id": null,
    "payload": {"content": [{"type": "text", "text": "Prepare the morning report."}]}
  }
}
```

For a one-time schedule, replace the `schedule` object with an offset-aware timestamp:

```json
{"type": "once", "run_at": "2030-10-21T09:00:00+08:00"}
```

The service stores and returns UTC. Timestamps without a timezone offset and relative delays are rejected. Preview returns zero or one instant after the supplied `after` time; both `utc` and `local` use UTC for a one-time rule. Use `?state=completed` to list finished schedules.

The response includes `id`, a fixed `session_id`, `stream_url` and an ETag. The first creation returns 201; retrying the same body with the same key returns the existing Schedule with 200. Keep that key when a response is lost.

- Read or edit configuration at `/api/v1/schedules/{id}`. PATCH requires the last ETag in `If-Match`.
- POST an empty object to `/{id}/enable` or `/{id}/pause`, with `If-Match`. DELETE `/{id}` archives it with the same precondition.
- POST an empty object to `/{id}/dispatch`, with `If-Match` and `Idempotency-Key`. HTTP 202 records a pending Occurrence. A replay returns that Occurrence with 200, even if it has since finished.
- Read current progress from `/{id}/status` and page trigger history through `/{id}/occurrences`. Read a saved invocation snapshot at `/{id}/occurrences/{occurrence_id}`.

Add `?include=execution` to either occurrence read to include current execution facts in the same response. Each `execution` contains `initial_entry_status`, `initial_run` (an ID and status, or null), `has_unfinished_work` and `observed_at`. Without this parameter, the field is omitted. List reads batch these facts for the page; Console uses this expansion instead of querying each entry and run separately.

The initial run is the run assigned to the trigger's first input. Later runs do not replace it in this summary. `has_unfinished_work` is true for a submitted trigger until its root thread first contains a completed, failed or cancelled run. Waiting is unfinished, including across resumes; later conversation and child work do not reopen a finished trigger. False does not mean the work succeeded. A trigger with no submitted input has null initial entry and run values. Execution observations can change at the same occurrence version. Completion changes its status to `completed` and advances its version. Refresh the expanded history after thread changes or reconnecting. Read full output through the ordinary run items API.

The API reference contains all filters and fields. All Schedule management endpoints are also available through MCP. [Agent Composer](agent-composer.md) enables the `schedules` platform toolset; other Agents can opt in through their toolsets. Reads and time previews run directly by default; create, update, enable, pause, archive and manual dispatch request approval. MCP uses the same request bodies, ETags, idempotency keys and creator permissions as HTTP. A plan created by Composer keeps its saved execution authority after that conversation ends. Live output continues through the HTTP Session stream.

## Observe live state and load history

Use the returned `stream_url`: `/api/v1/sessions/{session_id}/stream`. No extra query parameters are needed. Schedule Sessions add `schedule` and `occurrence` state frames to the existing [Session output protocol](agents-and-runs.md#follow-a-session-stream).

Each connection starts with current plan state, compact trigger summaries and every Thread in the Session, then follows their changes and live output. Completed, child and independent Threads remain observable regardless of their age. Use history pages for browsing trigger records, detail queries for full intent, and the ordinary Run items API for saved output.

The stream shows current state, not a durable transition log: brief intermediate states can be combined. On reconnect, also refresh the visible history page and any selected old Thread, Entry, Run and output. A Run and its Occurrence can finish while disconnected. Console performs these refreshes automatically.

Workspace administrators can select **Add Webhook** on the schedule detail page to prefill its Schedule ID, or configure subscriptions in **Settings → Webhooks**. Subscribe to `schedule_occurrence.completed` for execution completion and inspect `execution_outcome`. `schedule.completed` only means a one-time schedule finished scheduling. Skips, submission failures, cancellations and automatic pauses also have events; see [webhooks](files-and-webhooks.md#webhooks).
