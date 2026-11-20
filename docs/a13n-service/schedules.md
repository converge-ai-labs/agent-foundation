---
title: Schedules
description: Automate background agent execution with cron schedules, one-time triggers, overlap prevention, and dedicated occurrence auditing.
---

**Schedules** enable Service to dispatch prompts or structured payloads to agents automatically—either at a future timestamp or recurring on a timetable. Whether generating daily engineering summaries, auditing system logs overnight, or dispatching customer reports, schedules operate independently in the background on your distributed workers.

## Create and manage a schedule

You can create and test a recurring schedule directly in Console:

1. Navigate to **Schedules → Create schedule**.
2. Name the schedule (e.g., `Daily Morning Standup Report`).
3. Select **Recurring** and provide a 5-field cron expression (e.g., `0 9 * * 1-5` for 09:00 AM Monday through Friday) alongside your target IANA time zone (e.g., `America/New_York` or `Asia/Shanghai`).
4. Click **Preview next times** to inspect the next 5 calculated execution instants in both local and UTC times.
5. Select the target agent, choose whether to pin a specific revision or follow the latest default, and enter the prompt instructions (e.g., *"Synthesize the previous day's git commits and open PRs into team highlights."*).
6. Under **Delivery target**, choose whether each occurrence creates a **New thread each time** (recommended for standalone reports) or **Reuses one thread** (for continuous long-term logs).
7. Save the schedule. New schedules begin in a `paused` state.
8. Click **Run once** to trigger an immediate manual test run. Inspect the output to verify tool calls and responses.
9. Click **Enable** to activate automatic cron execution.

Service schedules enforce strict **overlap prevention**: if a prior occurrence is still actively executing when the next cron tick arrives, Service holds the new occurrence until the active run settles, preventing duplicate resource consumption and concurrent write conflicts.

## One-time schedules

For tasks that need to run exactly once at a designated future time (e.g., deploying an approved schema change during a scheduled maintenance window):

1. Choose **One time** in the schedule creation modal.
2. Select your future date and target time.
3. Save the schedule and enable it.
4. Once the target time arrives, Service dispatches the task, marks the schedule state as `completed`, and records the execution result.

If a transient network partition occurs, Service will execute the one-time schedule within its configured maximum lateness window (default 300 seconds).

## Read the result

Each time a schedule fires, Service creates an **Occurrence** record tracking the lifecycle of that specific trigger:

| Occurrence status | Phase |
| :--- | :--- |
| `pending` | Trigger timestamp reached; trigger intent safely recorded in the queue |
| `submitted` | Inbox entry allocated and assigned to a compute run |
| `running` | Worker actively executing the agent run and invoking tools |
| `completed` | Run finished; terminal output and token metrics captured |
| `skipped` | Trigger skipped due to active overlap or exceeded lateness deadline |

Click any occurrence row in the schedule history table to inspect the generated output, review exact tool arguments, or follow the complete execution trace in Debug view.

## Use the HTTP API

Schedules can be created, updated, and dispatched programmatically via the Service API.

Submit `POST /api/v1/schedules` with an idempotency key:

```json title="create_schedule.json"
{
  "name": "Daily Morning Standup Report",
  "schedule": {
    "type": "cron",
    "expression": "0 9 * * 1-5",
    "timezone": "America/New_York"
  },
  "target": {
    "mode": "new_thread",
    "session_id": null
  },
  "invocation": {
    "agent_id": "agent_reporter_123",
    "agent_revision_id": null,
    "payload": {
      "content": [
        {
          "type": "text",
          "text": "Generate the morning engineering summary."
        }
      ]
    }
  }
}
```

The response returns the schedule ID and its current ETag:

```json
{
  "id": "sched_abc123",
  "name": "Daily Morning Standup Report",
  "state": "paused",
  "created_at": "2026-11-20T10:00:00Z"
}
```

To trigger a manual test dispatch immediately via the API:

```bash
curl -X POST "$A13N_URL/api/v1/schedules/sched_abc123/dispatch" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "If-Match: $SCHEDULE_ETAG" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{}'
```

Common schedule management endpoints:

| Action | HTTP Request |
| :--- | :--- |
| **Get schedule details** | `GET /api/v1/schedules/{id}` |
| **Update configuration** | `PATCH /api/v1/schedules/{id}` (requires `If-Match`) |
| **Enable schedule** | `POST /api/v1/schedules/{id}/enable` |
| **Pause schedule** | `POST /api/v1/schedules/{id}/pause` |
| **Archive schedule** | `DELETE /api/v1/schedules/{id}` |
| **List occurrences** | `GET /api/v1/schedules/{id}/occurrences` |

## Observe live state and load history

To observe execution progress in real time as scheduled tasks fire, connect to the session stream:

```text
GET /api/v1/sessions/{session_id}/stream
```

The SSE stream broadcasts `schedule` and `occurrence` lifecycle updates alongside live token chunks. For automated alerting, configure a [Webhook](webhooks.md) subscribed to the `schedule_occurrence.completed` event to deliver reports directly to Slack, email, or internal monitoring systems.
