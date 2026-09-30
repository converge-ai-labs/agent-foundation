---
title: Agents, threads and runs
description: Configure agents, start conversations, and follow, steer, or answer runs.
---

An **agent** is a named, versioned configuration: a model, instructions, tools and policies. People and applications talk to agents in **sessions**. A session holds one or more **threads**, each a single line of conversation; messages you send go to the thread's **inbox**, and each turn of the agent is a **run**. A run executes on a worker as one or more **attempts** and ends `completed`, `waiting`, `failed` or `cancelled`.

All paths below are under `/api/v1` and act in the request's [workspace](http.md#workspace). Reading needs `read`; starting, steering, answering and stopping runs needs `run`; changing agents needs `write`. See [Identity and access](identity.md#roles).

## Agents

In Console, open **Agents → Create agent**. Each save creates an immutable **version** (a revision in the API); **Versions** lists them and **Set as default** chooses the one new conversations use. **Import from YAML** and **Export agent** copy an agent between workspaces, matching each referenced resource to one in the target workspace.

Through the API:

```sh
curl -X POST "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Support", "description": "Answers product questions",
       "config": {"model": "gpt-5.5", "instructions": "Be precise and cite sources.", "user_questions": true}}'
```

The response's `id` (`ap_…`) identifies the agent in paths and references; agents have no key.

- `POST …/agents/{agent_id}/revisions` with `{config, note?, make_default?}` and the agent's `If-Match` adds a revision; it becomes the default unless `make_default` is `false`. A `config` identical to the current default is a no-op: the call returns that revision again (`201`) without creating one, regardless of `make_default`. `POST …/revisions/{revision_id}/set-default` changes the default.
- `POST …/agents/validate` with `{config}` checks a configuration exactly as saving would, and answers `204`.
- `PATCH …/agents/{agent_id}` changes `name`, `description` and `labels`; `PUT …/avatar` sets an image. `POST …/duplicate` copies an agent, from its default or a chosen revision.
- `POST …/archive` stops new runs of the agent (`422 disabled`); runs already accepted finish. `POST …/unarchive` reverses it.
- Lists filter by `label`, `q` (name or description), `archived`, `source` (`custom` or `builtin`), and `skill_id` or `skill_revision_id`, which keep the agents with a revision pinning it.

The built-in [Agent Composer](agent-composer.md) is an agent too, the workspace's one with `source: "builtin"`; it cannot be changed or archived.

### Agent configuration

A revision's `config` holds:

| Field                             | Meaning                                                                                                                                |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `model`                           | The model's key. See [Models](models.md).                                                                                              |
| `model_settings`                  | Model-API-specific settings, such as reasoning effort.                                                                                 |
| `model_characteristics`           | The context window and context-management thresholds.                                                                                  |
| `instructions`                    | The system instructions, up to 256 KiB.                                                                                                |
| `toolsets`                        | Built-in toolsets and each tool's enablement, configuration and permission. See [Tools and connections](tools.md#built-in-toolsets).   |
| `skills`                          | Skills and the revisions they pin, `[{skill_id, revision_id}]`. See [Skills](skills.md).                                               |
| `connection_tools`                | Connections and their tools. See [Use a connection in an agent](tools.md#use-a-connection-in-an-agent).                                |
| `client_tools`                    | Tools your application executes; see [client tools](#client-tools-and-questions).                                                      |
| `user_questions`                  | Offers the `ask_user_question` tool.                                                                                                   |
| `subagents`, `subagent_mode`      | Other agents this agent can delegate to; see [subagents](#subagents).                                                                  |
| `reviewer`                        | The model, by key, that decides calls whose permission is `review`.                                                                    |
| `media_understanding`             | Models, by key, that read images, video or audio for this agent; see [media understanding](models.md#media-understanding).             |
| `plugins`                         | Instances of Harness plugins the deployment installed (`plugins.keys`).                                                                |
| `output_spec`                     | Structured output: one JSON Schema, or 2–32 named `variants`. Without it the result is text.                                           |
| `retries`                         | How many times the model may retry failed tool calls (`tools`) and invalid output (`output`), 0–100 each.                              |
| `default_environment_template_id` | An [environment template](environments.md#templates) from which each new thread gets its own primary environment.                      |
| `memory_mounts`                   | [Memories](memory.md#mount-a-memory-on-a-thread) each new thread mounts when its first run is accepted, `[{name, memory_id, access}]`. |

Saving validates the whole configuration against the workspace: every referenced model, skill, connection, provider and agent must exist and be usable by you, or saving fails with `invalid_argument` on that field, and skill and subagent references without a `revision_id` are pinned to the current default revision. A revision therefore always runs exactly what it was saved with.

### Tool permissions

Every built-in tool, connection tool and client tool has a permission:

| Permission | Effect                                                                                                                                                                              |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `allow`    | The tool runs when the model calls it.                                                                                                                                              |
| `ask`      | The run [waits](#waits-approvals-and-questions) for a person to approve or deny the call.                                                                                           |
| `review`   | The `reviewer` model approves or denies the call; with its default `on_error: approval_required`, a failed review asks a person. A revision that uses `review` must set `reviewer`. |
| `deny`     | The call is refused.                                                                                                                                                                |
| `inherit`  | The default, which allows.                                                                                                                                                          |

Set it per built-in tool in `toolsets.<toolset>.tools.<tool>.permission`, per connection in `connection_tools[].permission` with per-tool overrides in `permissions`, and per client tool in `client_tools[].permission` (`inherit`, `allow` or `deny`). The configuration toolset's writing tools default to `ask`.

### Client tools and questions

A client tool is declared in the revision and executed by your application:

```json
{
  "client_tools": [
    {
      "name": "open_ticket",
      "description": "Open a support ticket in the customer's CRM.",
      "parameters_json_schema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}
    }
  ]
}
```

When the model calls it, the run ends `waiting` with the call in `pending`. Your application performs it and [resumes](#resume-a-waiting-run) the run with the result.

With `user_questions: true`, the model can ask the user a question with `ask_user_question`. The run waits with an entry in `pending.calls`; answer that specific call through [resume](#resume-a-waiting-run). Ordinary messages remain queued until the wait is explicitly resolved.

### Subagents

`subagents` names other agents of the workspace this agent can delegate to. Each edge sets the `agent_id`, optionally a pinned `revision_id` and a `description` for the model, `context` (whether the task, a history summary or selected history, and shared or isolated task state are passed), `usage_limits`, and, for async children, `environment`.

```json
{
  "subagent_mode": "async",
  "subagents": {
    "researcher": {"agent_id": "ap_...", "description": "Finds and summarizes sources.",
                   "usage_limits": {"request_limit": 20}, "environment": {"mode": "shared"}}
  }
}
```

- **Inline** (`subagent_mode: "inline"`, the default): a delegated agent runs inside the parent's run and attempt, as part of that run. Inline subagents may not lead back to the delegating agent, and the graph's depth and size are bounded. `usage_limits` bounds requests, tokens and tool calls of each delegation.
- **Async** (`subagent_mode: "async"`): each delegation starts a child thread in the same session, using the parent run's principal. The parent can check, wait for, steer, cancel and continue children with the Harness [delegation tools](../a13n-harness/delegation-and-codeact.md#asynchronous-children). A child's result arrives as a `child_result` inbox entry and either steers the active parent run or starts its next run; delivery retries when the inbox is full. A child waiting for a person still counts as running. For async edges, only `usage_limits.request_limit` applies.

An async child's environment follows its edge: `shared` (the default) mounts the parent run's environments, `dedicated` reserves a new one from `template_id`, and `none` mounts nothing. The child agent's own `default_environment_template_id` is not used. A run may start at most `worker.child_count` children, and children nest at most `worker.child_depth` levels.

## Start a conversation

`POST …/threads` creates a thread with its first message, and a new session unless you pass `session_id`. It requires an `Idempotency-Key`:

```sh
curl -X POST "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: 9f0c7c1e-2b1f-4a3e-8f53-5d7c0c1c9a10" \
  -d '{"agent_id": "ap_...",
       "payload": {"content": [{"type": "text", "text": "Summarize the attached report."},
                               {"type": "asset", "asset_id": "ast_..."}]},
       "options": {"labels": {"customer": "acme"}}}'
```

The response (`201`, or `200` for a replay) is `{thread, entry, run}`: the new thread, the inbox entry holding the message, and the run it started, or `null` when it could not start yet. The new thread also accepts:

- `message_history`: optional [imported conversation context](#import-conversation-context), used only to initialize this thread;
- `mcp_headers`: [caller headers](#caller-headers) for its MCP connections;
- `environments`: initial [mounts](environments.md#mount-environments-on-a-thread), `[{name, environment_id, working_directory?}]`;
- `memories`: initial [memory mounts](memory.md#mount-a-memory-on-a-thread), `[{name, memory_id, access}]`.

`POST …/sessions` creates an empty session to pass as `session_id`. `GET …/sessions` lists sessions with a `preview` of the latest run and filters by `q` (a session or thread ID), `agent_id`, `status`, `trigger`, `updated_after`, `updated_before` and `label`. `GET …/threads?session_id=…` lists a session's threads, and `PATCH` changes session and thread `labels`.

Sessions list most recently updated first: a new run or a label edit moves a session to the top. A run's acceptance does not change a session's `ETag`, so a label edit's `If-Match` read before a later run still applies.

In Console, **New conversation** starts a session; **Try agent** starts one from the agent's page.

### Import conversation context

To continue a conversation produced outside the Service, add `message_history` to the new-thread request alongside the current `payload`:

```json
{
  "agent_id": "ap_...",
  "message_history": [
    {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": "We are planning a trip to Kyoto."}]},
    {"kind": "response", "parts": [{"part_kind": "text", "content": "How many days will you stay?"}]}
  ],
  "payload": {"content": [{"type": "text", "text": "Three days. Suggest an itinerary."}]}
}
```

These are Pydantic AI conversation messages, not text pasted into the current prompt. Request parts accept `user-prompt` text (also lists of strings or native `TextContent` objects) and `tool-return`; response parts accept `text` and `tool-call`. Historical tool calls use `tool_name`, `tool_call_id` and `args` (a JSON object, a JSON string encoding an object, or null); their returns use the same name and ID plus JSON `content` and optional `outcome` (`success` by default, or `failed`, `denied`, `interrupted`). Every call must have a matching return before another response, a new user prompt or the end of the import. Historical tools do not need to be installed and will not execute.

Import is limited to 256 messages and 256 KiB of normalized JSON. System instructions, media and suspended execution are not accepted. Use the Agent configuration for instructions and the current payload for attachments. Native timestamps, provider fields and usage are accepted, but do not become Service accounting. Application metadata and Run/conversation IDs are retained for readback and cleared before initializing execution; they cannot claim Service input consumption or authority.

OpenAPI and generated clients represent this field as JSON objects rather than duplicating Pydantic AI's type hierarchy. Python users who already use Pydantic AI can serialize a completed text/tool history directly; the Service SDK does not need to depend on Pydantic AI:

```python
import json
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter


def history_json(messages: list[ModelMessage]) -> list[dict]:
    return json.loads(ModelMessagesTypeAdapter.dump_json(messages))
```

Pass the resulting array as `message_history`. It must satisfy the import restrictions above; not every possible Pydantic AI history is importable. Thread readback retains your submitted JSON values, without inserting omitted timestamps or other native defaults. Repeating the same submitted request with the same idempotency key replays it.

The Thread keeps the immutable import for readback, but it does not manufacture historical Runs, display Items, tool executions or usage. Follow-up messages continue the committed checkpoint without reimporting; forks inherit that checkpoint. You cannot replace history on an existing Thread.

## Submit a message

`POST …/threads/{thread_id}/inbox` adds a message to a thread. It takes the same `agent_id`, `payload` and `options` as a new thread, requires an `Idempotency-Key`, and returns `{thread, entry, run}`.

A message's `payload.content` holds 1–32 parts:

| Part                                 | Content                                                                                                                                |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `{"type": "text", "text": ...}`      | Up to 65,536 characters.                                                                                                               |
| `{"type": "asset", "asset_id": ...}` | A workspace [asset](files-and-webhooks.md#assets), read with the submitter's access.                                                   |
| `{"type": "url", "url": ...}`        | An HTTP(S) URL. The Service fetches it under its [outbound policy](configuration.md#outbound-requests); the model provider never does. |
| `{"type": "json", "value": ...}`     | Structured input, nested at most 32 levels.                                                                                            |

A refused URL fails its inbox entry. An unreachable URL or transient response can retry on another attempt, within the run's attempt budget.

`agent_revision_id` pins a revision; otherwise the run uses the agent's default revision at the time it starts. `delivery` chooses what happens while a run is active:

- `steer` (the default) joins the active run at its next model request when it names the same agent, with no revision pin or the run's own one. A steer that gives no `options` joins whatever options the run started with; one that gives `options` joins only when they match the run's. Otherwise it waits for a later run.
- `next_run` always waits for the next run.

When the thread is idle, either delivery starts a run at once (trigger `input`). Messages that waited start runs in inbox order when the thread becomes idle (trigger `queued`).

A thread accepts no new run while:

- a run is active;
- its history head is `waiting` on any pending item, including questions; resume that exact Run first;
- its latest run `failed` or was `cancelled`: queued messages wait, and only a message you submit now starts a run. After that run the queue continues.

A message that cannot run, for example because its agent was archived or an override is no longer valid, fails in place with a `failure` and does not block the messages behind it.

Archiving a workspace stops its execution: a queued or newly submitted entry fails in place with `disabled`, and a run already executing fails with `authority_revoked` the next time its worker renews authority, within `worker.authority_seconds`.

### Attached files

An asset or a URL's content reaches the model in one of three ways:

- A JPEG, PNG, GIF or WebP image, a common audio or video format, or a PDF is sent to the model natively when the run's model declares that understanding (`image_understanding`, `audio_understanding`, `video_understanding` or `document_understanding` in its [characteristics](models.md)). Other formats, such as SVG, TIFF, Word or Excel files, are never sent natively, because providers differ on them.
- A text file of up to 64 KiB is included as text: any `text/*` type, such as plain text, Markdown or CSV, and JSON, XML, YAML or JavaScript. A URL's text is decoded with the charset it declares. When the run has no environment, a longer text from a URL is included cut to its first 64 KiB, and the model is told it was cut.
- Any other file, such as an archive, a PDF the model cannot read or a larger text file, is written into the run's primary environment (the `workspace` mount) under `/workspace/.a13n/attachments/`, with a name shortened to 200 bytes and cleaned of control characters and `/`. The model is told the file's path, name, media type and size and works on it with its file and shell tools. The same file attached again, in a later message or by a retried attempt, is written only once, unless the run changed the copy's size. The file lives only in that environment, so a thread that replaces its primary environment, or a fork with fresh environments, no longer has it.

When the run can take a file in none of these ways, the message is refused with `400 invalid_argument`, reason `environment_required`, the part's `field` (such as `content.1.asset_id`) and the file's `media_type`. Give the agent an [environment template](environments.md), mount an environment on the thread, or choose a model that understands the media. The same check applies when a queued message is edited, and again when its run starts. A URL's type is known only once it is fetched, so a non-text URL the run cannot read fails its entry then. A file the environment refuses to write fails its entry with `invalid_argument`; other steers carry on.

### Manage queued messages

`GET …/threads/{thread_id}/inbox` lists entries, and `GET …/inbox/{entry_id}` reads one, with their `status`: `pending` (queued), `assigned` to a run, `consumed`, `failed` or `withdrawn`. Changes to pending entries take the **thread's** `If-Match`:

- `PATCH …/inbox/{entry_id}` edits the `delivery`, `payload`, `agent_revision_id` (`null` unpins) or `options` of your own pending message.
- `DELETE …/inbox/{entry_id}` withdraws a pending entry. Its tombstone keeps the idempotency key.
- `PUT …/inbox/order` with `{"entry_ids": [...]}` reorders the pending entries; the list must name exactly the pending entries.

Anyone with `run` may withdraw and reorder. A thread holds at most `control.inbox_count` pending and assigned entries totalling `control.inbox_bytes`; beyond that, submissions answer `429 rate_limited`. Console shows these under **Queued messages**, and sends messages typed while a run works as guidance (`steer`).

### Per-run options

`options` applies to the run a message starts:

- `labels` become the run's labels, which `PATCH …/runs/{run_id}` can change later.
- `max_usage.requests` (1–10,000) limits the run's model requests; the run fails with `usage_limit_exceeded` when it needs more.
- `overrides` change the revision's configuration for this run only. `toolsets` replaces whole toolsets; `retries` and each `subagents` edge change only the fields they set, and a `null` edge removes it; `model`, `model_settings`, `model_characteristics`, `instructions`, `skills`, `connection_tools`, `client_tools`, `plugins`, `reviewer`, `media_understanding` and `output_spec` replace the revision's value. The result is validated like a saved configuration, when you submit and again when the run starts, and frozen into the run.

```json
{"options": {"max_usage": {"requests": 40},
             "overrides": {"model": "claude-opus-4-6", "instructions": "Answer in German."}}}
```

A resume or a child result continues with the options of the run it follows. In Console, **Run options** sets the model, an instructions override, media understanding, a pinned revision and other configuration for the next run.

## Caller headers

A thread can pass non-credential context, such as a tenant or conversation ID, to its MCP connections as HTTP headers. Set `mcp_headers` when creating the thread, or change it with `PATCH …/threads/{thread_id}` and the thread's `If-Match`:

```json
{"mcp_headers": {"conn_...": {"x-tenant-id": "acme"}}}
```

Each key must be an enabled MCP connection of the workspace. At most 32 connections and 16 KiB of names and values are allowed; `authorization`, transport and protocol headers, and names the connection's own authentication uses are refused. Each run freezes the thread's headers when it starts, and forks and async children inherit them. Run views do not show them.

## Waits, approvals and questions

A run that needs something from outside ends `waiting`. Its `pending.approvals` lists execution approvals and `pending.calls` lists external results, including user questions and custom human-operated tools. `wait_reason` summarizes these groups as `approval`, `call`, or `multiple`:

```json
{
  "status": "waiting",
  "wait_reason": "approval",
  "pending": {
    "approvals": [{"tool_call_id": "call_delete", "tool_name": "delete_file", "arguments": {"path": "report.txt"}, "presentation": null}],
    "calls": []
  }
}
```

### Resume a waiting run

Submit one complete batch naming the exact waiting Run and every pending call ID:

```bash
curl -X POST "$A13N_URL/api/v1/runs/$RUN/resume" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Idempotency-Key: resume-1" \
  -H "Content-Type: application/json" \
  -d '{"approvals": {"call_delete": {"action": "deny", "reason": "Keep the file"}}, "calls": {}}'
```

Approval values are `{ "action": "approve" }` or `{ "action": "deny", "reason": "..." }`. Call results are `{ "status": "returned", "value": ... }` or `{ "status": "failed", "message": "..." }`. Returned values can be any JSON; an explicit failure becomes a tool failure the Agent can handle, not necessarily a failed Run.

For a user question, a returned value contains structured answers or a free-text response:

```json
{
  "approvals": {},
  "calls": {
    "call_question": {
      "status": "returned",
      "value": {"response": "Up to 200 dollars per night."}
    }
  }
}
```

A structured value could be `{"answers": {"Which color?": "blue"}}`; multi-select answers use arrays. The Service checks it against that call's exact questions using the Harness validator. To intentionally skip the question, send `{"status": "failed", "message": "User chose not to answer"}` for that call.

Both result maps are required, and each must cover its pending group exactly. Missing results, unknown IDs and wrong categories reject the entire request without changing the wait. There are no default answers or partial submissions. In Console, review each item and submit the complete set; **Continue without feedback** explicitly submits denials and failed results after confirmation.

The response is the successor run (`201`, or `200` for an idempotent replay). Results are stored in its existing `resume` field, without another inbox message. Only the thread's waiting history head can be resumed while nothing else runs; stale requests receive `409 conflict` with reason `not_idle_waiting_head`. After a failed successor, explicitly resume the still-waiting head again.

To accompany those results with a clarification or attachment, include optional `input` in the same resume request:

```json
{
  "approvals": {},
  "calls": {"call_lookup": {"status": "returned", "value": {"available": true}}},
  "input": {"content": [{"type": "text", "text": "Use the updated delivery address."}]}
}
```

`input` accepts the same parts as `payload`. The whole resume is accepted or rejected together and stored on the successor; no extra inbox entry is created. The model receives tool results before the accompanying user content. Recovery preserves that content without duplicating it, even if the worker stopped at an approved tool's pre-effect checkpoint. An asset must be usable in the workspace and readable by the inherited configuration. A URL or file that cannot be materialized fails the successor rather than silently dropping the clarification. The complete resume body is limited to 256 KiB.

The accompanying input does not replace any required result. Ordinary inbox messages remain separate and cannot close a wait. Queued messages retain their order; after resume, compatible steers can join the successor while `next_run` messages wait for a later run.

## Interrupt, fork and archive

- **Interrupt**: `POST …/runs/{run_id}/interrupt` cancels a run. A run that has not started is `cancelled` at once; a running run is asked to stop at its next safe point and shows `cancel_requested_at` until then. Interrupting a cancelled run returns it; a completed, waiting or failed run answers `409` (`run_completed`, ...). Console's **Stop** interrupts the active run.
- **Fork**: `POST …/runs/{run_id}/fork` starts a new thread in the same session whose first run continues from a `completed` or `waiting` run, with a new message (the same body as a submission) and an `Idempotency-Key`. A fork of a waiting run automatically denies approvals and marks other calls as having no response in the new branch before processing the new message. The original wait is unchanged. The fork shares the origin thread's mounted environments unless `fresh_environments` is `true`, and `environments` adds more. It copies the origin thread's memory mounts, and `memories` adds more. Failed and cancelled runs cannot be forked.
- **Archive**: `POST …/threads/{thread_id}/archive` with the thread's `If-Match` ends a thread permanently: pending messages are withdrawn, its mounts are removed, and an active run is interrupted. Its history stays readable.

A failed or cancelled run never becomes history: continuation uses the last completed or waiting run. If that head is still waiting, resume it explicitly; ordinary messages stay queued.

## Results

`GET …/runs/{run_id}` returns the run: `status`, `trigger`, `lineage` (`root`, `continue` or `fork`), `parent_run_id`, the `input` or `resume` that started it, `options`, `environment_mounts`, `memory_mounts`, `pending`, `output`, `failure {code, message}`, `usage_at_seal`, `labels` and timestamps. `output` is the agent's final text, or JSON matching its `output_spec`.

- `GET …/threads/{thread_id}/runs` lists a thread's runs, newest first. A thread's `head_run_id` is its latest completed or waiting run, `current_run_id` its active run.
- `GET …/runs/{run_id}/items` returns `run` and a compact `snapshot` of input, text, reasoning, tools, media and execution summaries, with `display_revision`, `position`, optional `resume_after`, and `complete`. Render unresolved blocks of a sealed Run as interrupted. A display keeps at most 4096 blocks within its byte budget; `snapshot.omitted` counts removed blocks. Producer continuity is not exposed.
- `GET …/runs/{run_id}/lineage` returns the run and its ancestors, nearest first, across forks.
- `GET …/runs/{run_id}/attempts` lists attempts with their `start_reason` (`initial`, `recovery` after a lost worker, `handoff` when a worker shuts down) and outcome. A run fails after `max_attempts` attempts that were not handoffs.

A failed run's `failure.code` names the cause, such as `usage_limit_exceeded`, `environment_unavailable`, a validation code, or `attempt_failed`.

## Follow a thread stream

`GET …/threads/{thread_id}/stream` is a server-sent event stream of the thread's live output:

```sh
curl -N "$A13N_URL/api/v1/threads/$THREAD/stream" -H "Authorization: Bearer $A13N_API_KEY"
```

| Event      | Data                                 | Meaning                                                                                |
| ---------- | ------------------------------------ | -------------------------------------------------------------------------------------- |
| `delta`    | `{run_id, attempt, sequence, delta}` | One atomic batch of shared display operations.                                         |
| `boundary` | `{run_id, attempt, sequence}`        | The run committed a checkpoint; its items now cover everything up to this point.       |
| `changed`  | `{version}`                          | The thread changed (a run started or ended, the inbox changed): read the thread again. |
| `reset`    | `{run_id}`                           | The run moved to a new attempt: discard its live output and read its items again.      |
| `gap`      | `{run_id, position?}`                | Live output was skipped: read the Run's compact snapshot again.                        |

The stream is provisional; the run's items are the durable record. To render a thread:

1. Read `GET …/runs/{run_id}/items` for the active run. When `position` is non-null, open the stream with `?run=<run_id>&position=<position>`. Pass its non-null `resume_after` as the `Last-Event-ID` header. Without a saved position, start retained replay without a cursor.
2. Apply each uncovered `delta` atomically. Its producer must match the Run and attempt, `from_sequence` must match the applied sequence, and `through_sequence` advances by one. Validate every block revision before exposing the batch; a covered batch is a no-op. Operations are `block.put`, `block.append`, `scope.put`, and `blocks.remove`.
3. On `reset`, discard superseded provisional output and read items again. On `gap`, read items and compare their position with the gap's optional `position`: clear the gap only when the missing range is covered, otherwise await a newer checkpoint or terminal state. On `changed`, read the thread.
4. Refresh the compact read periodically while active, not only the Run's status: a saved display may advance even when its final Redis delta and notice were both lost. Reject stale Run versions or older saved coverage. Read the final snapshot once sealed.

The snapshot's `resume_after` can lag because checkpoints do not wait for Redis writes. With the Run and position supplied, the server filters covered deltas and uses retained hints to seek directly. Missing, expired or incompatible hints fall back to filtered retained replay; they do not by themselves indicate lost output. Keep client deduplication for overlapping delivery. On a network reconnect, retain the display and send its continuously applied position with a matching hint; after a page refresh, load a snapshot first. Never advance that position across a gap. The producer batches typed operations within `worker.stream_coalesce_seconds` before assigning one sequence to the whole batch. A checkpoint can trim only the Redis prefix its snapshot covers, respecting `worker.stream_trim_seconds`; a delayed boundary cannot trim newer provisional output. The Service also caps a stream at about `worker.stream_length` entries and expires it `worker.stream_ttl` seconds after the last output.

Redis-derived `delta` and `boundary` frames carry an SSE `id`; SQL-derived boundaries have no ID. A header-only resume is rejected: always pair a hint with applied Run/position coverage. Receive gaps only for missing required sequences or transport uncertainty, not simply an expired hint. On EOF discard an incomplete final SSE frame and reconnect from applied coverage, with bounded retry/backoff. Reload snapshots and reassess coverage at later boundaries or when the Run seals. The Service sends a keep-alive comment every 15 seconds and ends the stream when your access to the workspace ends. The frames' JSON Schema is `proto/a13n-service/thread-stream.schema.json`.

## Usage

Every model request is recorded with its token counts and a snapshot of the model's pricing. `GET …/usage` sums the records per model:

```sh
curl "$A13N_URL/api/v1/usage?thread_id=$THREAD" -H "Authorization: Bearer $A13N_API_KEY"
```

```json
{"models": [{"model": "gpt-5.5", "requests": 12, "input_tokens": 48210, "output_tokens": 3104,
             "cache_read_tokens": 30112, "cache_write_tokens": 0, "cost": "0.0931"}]}
```

Each entry's `model` is a model key, or `null` for records attributed to no model. Filter by `run_id`, `thread_id`, `session_id`, and `ingested_after`/`ingested_before`. `cost` is `null` when no record of the model was priced. A run's `usage_at_seal` is its usage when it ended; reports that arrive later still count in `/usage`.

## Traces

With a trace backend configured (`telemetry.trace_backend`, see [logs, metrics and traces](configuration.md#logs-metrics-and-traces)), each attempt exports its Harness spans, and the workspace can query them. Console shows them under **Traces**.

- `GET …/runs/{run_id}/attempts/{attempt_id}/trace` lists an attempt's spans, including its inline subagents.
- `GET …/traces` lists root spans, one per attempt, filtered by `session_id`, `thread_id`, `run_id`, up to 8 `attribute=key:value` filters, and `started_after`/`started_before` (the last day by default, at most 31 days). A cursor is bound to these filters as given, including the window: changing `started_after` or `started_before` on the next page is `invalid_cursor`, and later pages keep the window the first page resolved.
- `GET …/traces/{trace_id}` returns a trace's root span, and `…/spans` its spans, each with name, kind, timing, status, model, usage, cost, input, output and attributes.
- `GET …/trace-backend` returns the backend `type` (`null` when none) and `queryable_since`.

Queries return only the workspace's spans. Without a backend they answer `503 unavailable`. How much content the spans hold depends on `telemetry.trace_content`.
