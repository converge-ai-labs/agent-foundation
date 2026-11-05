---
title: Stream and read results
description: Associate results with submitted messages, rebuild live output after reconnecting, and read saved history.
---

Use this guide after [submitting work from your application](connect-application.md). Retain the session, thread and inbox entry IDs from the receipt. Read saved run output for a final result; add the session stream when your interface needs progress. A disconnected observer does not cancel execution.

## Results

Read `GET /runs/{run_id}` and choose the next action from `status`:

| Status                | What to do                                                 |
| --------------------- | ---------------------------------------------------------- |
| `accepted`, `running` | Keep observing this run.                                   |
| `completed`           | Read `output`, text or JSON matching `output_spec`.        |
| `waiting`             | Read `pending` and submit a complete resume response.      |
| `failed`              | Inspect `failure.code` and `failure.message`.              |
| `cancelled`           | Inspect saved history before choosing a follow-up or fork. |

For submitted messages, first confirm which run consumed the inbox entry. An assigned entry can return to pending if it was not incorporated. A thread's latest run is not necessarily the answer to a particular message.

Use `/runs/{run_id}/items` for ordered display history. It returns recent items and `complete`; page backward with the first item's ordinal as `before`, or forward with `after`. Fields exceeding 32 KiB use `content_refs`; read saved content at `/runs/{run_id}/contents/{content_id}`. Each field retains at most 16 MiB, with `truncated` marking a saved prefix. Console's full/saved-content actions read these references.

For diagnosis, `/runs/{run_id}/agent` returns executed configuration, `/lineage` returns ancestors, and `/attempts` explains initial execution, recovery or worker handoff. `/threads/{thread_id}/runs` lists newest first. `current_run_id` identifies active work; `last_run_id` identifies the ended history from which continuation starts.

## Follow a session stream

Use `GET /sessions/{session_id}/stream` for live display across the session. It is an SSE stream that starts each connection from current state, without a reconnect cursor.

| Event       | Client action                                                                                                              |
| ----------- | -------------------------------------------------------------------------------------------------------------------------- |
| `thread`    | Replace the thread and named current/last runs.                                                                            |
| `items`     | Replace that run's items from ordinal `first` onward; keep earlier final items. Continue live display from `continuation`. |
| `delta`     | Apply provisional output to the run's display.                                                                             |
| `gap`       | Mark missing output and wait for the next `items` frame to repair it.                                                      |
| `app_ready` | Enable the identified [MCP Apps](mcp-apps.md#mcp-apps-for-embedding-applications).                                         |

An `items` frame with `complete: true` is the run's final display. Use the run's status for its outcome. On reconnect, discard unfinished displays and rebuild active runs from the new snapshot. Read `/runs/{run_id}/items` for runs that ended while disconnected. Ignore unknown event names.

Reconnect with randomized backoff capped at 30 seconds, respecting `Retry-After` for 429 or 503. Other 4xx refusals and invalid frames require correction rather than automatic retries. The stream is provisional; saved items are the durable display record. The frame schemas are exported in `proto/a13n-service/session-stream.schema.json`.
