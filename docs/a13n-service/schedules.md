---
title: Schedules
description: Preview a schedule, test one trigger, then enable recurring or one-time agent work.
---

Use a schedule to send fixed text or JSON to an agent at a future time or on a recurring timetable. Each trigger creates a new thread with independent conversation history. All triggers belong to the schedule's dedicated session, where you can follow their output.

Start with an agent that already completes the task interactively. You need workspace `write` and `run` permissions to create a schedule. Test one manual trigger before enabling automatic work.

## Create and manage a schedule

For a daily morning report, open **Schedules → Create schedule** in Console:

1. Name it `Morning report` and choose **Recurring**. Enter `0 9 * * *` with the IANA time zone `Asia/Shanghai` for 09:00 local time each day.
2. Select **Preview next times**. Check both local and UTC instants before saving.
3. Choose the agent and enter “Prepare the morning report.” Leave the revision empty to follow its current default revision, or supply a revision ID to pin it. The JSON editor also accepts run options and explicit memory mounts.
4. Create the schedule; it starts paused. Select **Run once**, then **View output** to inspect the agent's result and handle any approval or input request.
5. After the test finishes, select **Enable**. Check the next trigger time and inspect the first automatic result.

After saving, open the detail page to check the target agent, selected revision and fixed session. Read every saved text or JSON input part in order, separately from the description. The session link opens execution history, or an empty session before the first trigger. If editing is unavailable, the page explains whether the schedule is archived or completed, you are not its creator, or your workspace permissions are insufficient.

Cron expressions have five fields. During daylight-saving changes, a missing local time does not fire; a repeated local time maps to two instants, each subject to the overlap rule below. Preview verifies the chosen calendar; execution still depends on service scanning and run admission.

Only the creator can edit, enable or manually dispatch a schedule. Another member with `write` can pause or archive it. Archival preserves history and is permanent. To change the execution identity, create a new schedule as that identity.

A schedule saves its creator's execution authorization. Signing out, credential expiry and revoking the original API key do not stop it. **Pause or archive the schedule to stop future submissions.** Disabling the principal or revoking its execution access also stops them. Tool approval remains in effect.

Time or invocation changes cancel input still awaiting submission. Pause and archive also cancel pending input, while already submitted input and runs continue. Maximum lateness limits submission time, not run completion.

## One-time schedules

Choose **One time** and select a future date and time in the displayed browser time zone. Create the paused schedule, test if needed, then enable it. If its time passes while paused, set a new future time before enabling.

**Run once** is an independent manual trigger; it leaves the automatic trigger intact. An unfinished manual test can therefore cause the automatic trigger to be skipped for overlap.

After automatic submission succeeds, is skipped or finally fails, the schedule enters `completed`, shown as **Scheduling finished**. Inspect the occurrence's execution result separately. A finished schedule can be read or archived; to schedule another time, create a new one.

After a service interruption, submission can still proceed within the original maximum-lateness window (default 300 seconds). Outside it, the trigger is `skipped` with `missed_deadline`, and one-time scheduling finishes. Retries use that same window.

## Read the result

Open the schedule detail page and select a trigger, called an **occurrence**. Follow it through submission, input handling and execution:

| Check                  | Meaning                                                                                       |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| Occurrence `pending`   | Trigger intent is saved                                                                       |
| Occurrence `submitted` | A thread and inbox entry are saved                                                            |
| Inbox entry            | Shows whether input is pending, assigned to a run, consumed or failed                         |
| Run                    | Shows output, tool calls and waiting requests                                                 |
| Occurrence `completed` | Root-thread execution ended; inspect `execution_outcome` for success, failure or cancellation |

`skipped`, `failed` and `cancelled` occurrence statuses describe submission outcomes. For completed execution, `completion_run_id` identifies the first root-thread run that completed, failed or was cancelled; `completed_at` records its end time. `finished_at` remains the submission finish time. Waiting and attempt retries leave execution unfinished.

The scheduler avoids overlap while an earlier occurrence is pending submission or its root thread has no completed, failed or cancelled run. A waiting run remains busy across resumes. Submitted input without a run also remains busy, even if that input was rejected or withdrawn; archiving its thread alone does not finish it.

Once a qualifying root-thread run ends, later conversation, forks and child work do not delay the next trigger or reopen the occurrence. Inputs added to the same thread retain its schedule origin; explicit forks and independent threads do not. Missed recurring times are coalesced to the latest due time. Transient submission failures retry within the lateness and attempt limits; execution failure does not repost the input.

## Use the HTTP API

Use the ordinary [workspace and authentication headers](http.md#workspace). Replace `AGENT_ID` with a usable agent ID and send this body to `POST /api/v1/schedules` with a new `Idempotency-Key`:

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

`agent_revision_id` is required: use `null` to follow the current default. The response includes the schedule `id`, fixed `session_id`, `stream_url` and ETag. First creation returns 201; replaying the same body and key returns the existing schedule with 200. Keep the key if the response is lost.

For one-time work, replace the `schedule` object with a future offset-aware timestamp:

```json
{"type": "once", "run_at": "2030-10-21T09:00:00+08:00"}
```

Service stores and returns UTC. It rejects timestamps without offsets and relative delays. `POST /api/v1/schedules/preview` accepts `{schedule, after, count}`; `after` needs an offset and `count` is 1–10. A one-time preview returns zero or one later instant, with both `utc` and `local` expressed in UTC.

To test the paused schedule, set `A13N_URL` and a workspace-scoped `A13N_API_KEY`. Set `SCHEDULE` to the returned ID, `SCHEDULE_ETAG` to its ETag including quotes, and `DISPATCH_KEY` to a new request key for this manual trigger:

```sh
curl -X POST "$A13N_URL/api/v1/schedules/$SCHEDULE/dispatch" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "If-Match: $SCHEDULE_ETAG" -H "Idempotency-Key: $DISPATCH_KEY" \
  -d '{}'
```

HTTP 202 means a pending occurrence was saved. Reuse the same key after an uncertain response; replay returns that occurrence with 200, even after it finishes. Read its progress with `GET /api/v1/schedules/{id}/occurrences?include=execution`, and use the returned thread and run IDs to inspect output.

| Operation                           | Request                                                      |
| ----------------------------------- | ------------------------------------------------------------ |
| Read configuration and current ETag | `GET /api/v1/schedules/{id}`                                 |
| Change future configuration         | `PATCH /api/v1/schedules/{id}` with `If-Match`               |
| Enable or pause                     | POST `{}` to `/{id}/enable` or `/{id}/pause` with `If-Match` |
| Archive                             | DELETE `/{id}` with `If-Match`                               |
| Read current progress               | `GET /api/v1/schedules/{id}/status`                          |
| Page trigger history                | `GET /api/v1/schedules/{id}/occurrences`                     |
| Read a trigger's saved invocation   | `GET /api/v1/schedules/{id}/occurrences/{occurrence_id}`     |

Short paths in the table are relative to `/api/v1/schedules`. Use `?state=completed` on the schedule list to find finished one-time schedules.

Occurrence list and detail reads accept `include=execution`. This adds `initial_entry_status`, nullable `initial_run`, `has_unfinished_work` and `observed_at`. The initial run remains the one assigned to the first input; later resumes do not replace it. `has_unfinished_work` follows the overlap rule, so `false` is not a success result. Unsubmitted occurrences have null entry and run values.

Execution observations can change without an occurrence version change; durable completion changes status and version. Refresh expanded history after thread changes or reconnecting. Read full saved output through the ordinary run items API.

Schedule management is also available through MCP and the `schedules` platform toolset. [Agent Composer](agent-composer.md) enables that toolset; other agents can opt in. Reads and previews run directly by default; mutations request approval. HTTP and MCP share request bodies, ETags, request keys and creator permissions. A Composer-created schedule keeps its saved authority after the conversation ends. Live output uses the HTTP session stream.

## Observe live state and load history

Connect to the returned `stream_url`, `/api/v1/sessions/{session_id}/stream`, without extra query parameters. The ordinary [session output protocol](agents-and-runs.md#follow-a-session-stream) adds `schedule` and `occurrence` state frames for this session.

Each connection starts with current plan state, compact occurrence summaries and every thread in the session, then follows changes and live output. Completed, child and independent threads remain visible regardless of age. Use paginated history for browsing, occurrence detail for the full invocation and run items for saved output.

The stream synchronizes current state; brief intermediate states can coalesce. After reconnecting, refresh the visible history page and selected thread, entry, run and output. Console does this automatically.

For completion notifications, select **Add Webhook** on the detail page or configure **Settings → Webhooks** as a workspace administrator. Subscribe to `schedule_occurrence.completed` and inspect `execution_outcome`. `schedule.completed` marks the end of one-time scheduling, not execution. [Webhooks](files-and-webhooks.md#webhooks) also cover skips, submission failures, cancellations and automatic pauses.
