# Agents, threads and runs

An **agent** is a named, versioned configuration: a model, instructions, tools and policies. People and applications talk to agents in **sessions**. A session holds one or more **threads**, each a single line of conversation; messages you send go to the thread's **inbox**, and each turn of the agent is a **run**. A run executes on a worker as one or more **attempts** and ends `completed`, `waiting`, `failed` or `cancelled`.

All paths below are under `/api/v1/workspaces/{workspace_id}`. Reading needs `read`; starting, steering, answering and stopping runs needs `run`; changing agents needs `write`. See [Identity and access](identity.md#roles).

## Agents

In Console, open **Agents → Create agent**. Each save creates an immutable **version** (a revision in the API); **Versions** lists them and **Set as default** chooses the one new conversations use. **Import from YAML** and **Export agent** copy an agent between workspaces, matching each referenced resource to one in the target workspace.

Through the API:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"key": "support", "name": "Support", "description": "Answers product questions",
       "config": {"model": {"model_id": "mdl_..."}, "instructions": "Be precise and cite sources.",
                  "user_questions": true}}'
```

- `POST …/agents/{agent_id}/revisions` with `{config, note?, make_default?}` and the agent's `If-Match` adds a revision; it becomes the default unless `make_default` is `false`. A `config` identical to the current default is a no-op: the call returns that revision again (`201`) without creating one, regardless of `make_default`. `POST …/revisions/{revision_id}/set-default` changes the default.
- `POST …/agents/validate` with `{config}` checks a configuration exactly as saving would, and answers `204`.
- `PATCH …/agents/{agent_id}` changes `key`, `name`, `description` and `labels`; `PUT …/avatar` sets an image. `POST …/duplicate` copies an agent, from its default or a chosen revision.
- `POST …/archive` stops new runs of the agent (`422 disabled`); runs already accepted finish. `POST …/unarchive` reverses it.
- Lists filter by `label`, `q` (key, name or description), `archived`, and the skill or skill revision the agents pin.

The built-in [configuration assistant](configuration-assistant.md) is an agent too; it cannot be changed or archived.

### Agent configuration

A revision's `config` holds:

| Field                             | Meaning                                                                                                                                                                       |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model`                           | `model_id`, plus `settings` (model-API-specific, such as reasoning effort) and `characteristics` (context window and context-management thresholds). See [Models](models.md). |
| `instructions`                    | The system instructions, up to 256 KiB.                                                                                                                                       |
| `toolsets`                        | Built-in toolsets and each tool's enablement, configuration and permission. See [Tools and connections](tools.md#built-in-toolsets).                                          |
| `skills`                          | Skills and the revisions they pin. See [Skills](skills.md).                                                                                                                   |
| `connection_tools`                | Connections and their tools. See [Use a connection in an agent](tools.md#use-a-connection-in-an-agent).                                                                       |
| `client_tools`                    | Tools your application executes; see [client tools](#client-tools-and-questions).                                                                                             |
| `user_questions`                  | Offers the `ask_user_question` tool.                                                                                                                                          |
| `subagents`, `subagent_mode`      | Other agents this agent can delegate to; see [subagents](#subagents).                                                                                                         |
| `reviewer`                        | The model that decides calls whose permission is `review`.                                                                                                                    |
| `media_understanding`             | Models that read images, video or audio for this agent; see [media understanding](models.md#media-understanding).                                                             |
| `plugins`                         | Instances of Harness plugins the deployment installed (`plugins.keys`).                                                                                                       |
| `output_spec`                     | Structured output: one JSON Schema, or 2–32 named `variants`. Without it the result is text.                                                                                  |
| `retries`                         | How many times the model may retry failed tool calls (`tools`) and invalid output (`output`), 0–100 each.                                                                     |
| `secret_requirements`             | Secrets the agent's tools need; see [Secrets](skills.md#secrets).                                                                                                             |
| `default_environment_template_id` | An [environment template](environments.md#templates) from which each new thread gets its own primary environment.                                                             |
| `memory_mounts`                   | [Memories](memory.md#mount-a-memory-on-a-thread) each new thread mounts when its first run is accepted, `[{name, memory_id, access}]`.                                        |

Saving validates the whole configuration against the workspace: every referenced model, skill, connection, provider and agent must exist and be usable by you, and skill and subagent references without a `revision_id` are pinned to the current default revision. A revision therefore always runs exactly what it was saved with.

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

With `user_questions: true`, the model can ask the user a question with `ask_user_question`. The run waits with a `user_input` item; the answer is the user's next message.

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

- **Inline** (`subagent_mode: "inline"`, the default): a delegated agent runs inside the parent's run and attempt, as part of that run. Inline subagents may not lead back to the delegating agent, and the graph's depth and size are bounded. `usage_limits` bounds requests, tokens and tool calls of each delegation. Each inline subagent uses its own secret declarations.
- **Async** (`subagent_mode: "async"`): each delegation starts a **child thread** in the same session, with `origin: "child"` and `subagent` set to the edge name, whose first run executes the edge's revision under the parent run's principal and authority. The parent can check, wait for, steer, cancel and continue its children with the Harness [delegation tools](../a13n-harness/delegation-and-codeact.md#asynchronous-children). When a child run completes, fails or is cancelled, its result arrives in the parent thread's inbox as a `child_result` entry with `{child_run_id, subagent, status, output, failure}`: it joins the parent's active run, or starts a parent run with trigger `child_result`. A result is never dropped; if the parent's inbox is full, delivery is retried. A child waiting for a person counts as still running. For async edges, `usage_limits` takes only `request_limit`, which becomes the child run's `max_usage.requests`.

An async child's environment follows its edge: `shared` (the default) mounts the parent run's environments, `dedicated` reserves a new one from `template_id`, and `none` mounts nothing. The child agent's own `default_environment_template_id` is not used. A run may start at most `worker.child_count` children, and children nest at most `worker.child_depth` levels.

## Start a conversation

`POST …/threads` creates a thread with its first message, and a new session unless you pass `session_id`. It requires an `Idempotency-Key`:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: 9f0c7c1e-2b1f-4a3e-8f53-5d7c0c1c9a10" \
  -d '{"agent_id": "ap_...",
       "payload": {"content": [{"type": "text", "text": "Summarize the attached report."},
                               {"type": "asset", "asset_id": "ast_..."}]},
       "options": {"labels": {"customer": "acme"}}}'
```

The response (`201`, or `200` for a replay) is `{thread, entry, run}`: the new thread, the inbox entry holding the message, and the run it started, or `null` when it could not start yet. The new thread also accepts:

- `mcp_headers`: [caller headers](#caller-headers) for its MCP connections;
- `environments`: initial [mounts](environments.md#mount-environments-on-a-thread), `[{name, environment_id, working_directory?}]`;
- `memories`: initial [memory mounts](memory.md#mount-a-memory-on-a-thread), `[{name, memory_id, access}]`.

`POST …/sessions` creates an empty session to pass as `session_id`. `GET …/sessions` lists sessions with a `preview` of the latest run and filters by `q` (a session or thread ID), `agent_id`, `status`, `trigger`, `updated_after`, `updated_before` and `label`. `GET …/threads?session_id=…` lists a session's threads, and `PATCH` changes session and thread `labels`.

Sessions list most recently updated first: a new run or a label edit moves a session to the top. A run's acceptance does not change a session's `ETag`, so a label edit's `If-Match` read before a later run still applies.

In Console, **New conversation** starts a session; **Try agent** starts one from the agent's page.

## Submit a message

`POST …/threads/{thread_id}/inbox` adds a message to a thread. It takes the same `agent_id`, `payload` and `options` as a new thread, requires an `Idempotency-Key`, and returns `{thread, entry, run}`.

A message's `payload.content` holds 1–32 parts:

| Part                                 | Content                                                                                                                                |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `{"type": "text", "text": ...}`      | Up to 65,536 characters.                                                                                                               |
| `{"type": "asset", "asset_id": ...}` | A workspace [asset](files-and-webhooks.md#assets), read with the submitter's access.                                                   |
| `{"type": "url", "url": ...}`        | An HTTP(S) URL. The Service fetches it under its [outbound policy](configuration.md#outbound-requests); the model provider never does. |
| `{"type": "json", "value": ...}`     | Structured input, nested at most 32 levels.                                                                                            |

A refused address or another definitive failure status fails the entry at once. An unreachable URL, a timeout, or a transient status (408, 429 or 5xx) instead ends the attempt so a later attempt fetches it again, within the run's attempt budget.

`agent_revision_id` pins a revision; otherwise the run uses the agent's default revision at the time it starts. `delivery` chooses what happens while a run is active:

- `steer` (the default) joins the active run at its next model request when it names the same agent, with no revision pin or the run's own one. A steer that gives no `options` joins whatever options the run started with; one that gives `options` joins only when they match the run's. Otherwise it waits for a later run.
- `next_run` always waits for the next run.

When the thread is idle, either delivery starts a run at once (trigger `input`). Messages that waited start runs in inbox order when the thread becomes idle (trigger `queued`).

A thread accepts no new run while:

- a run is active;
- its latest run is `waiting` on an approval or client tool; resume it first. If the run waits only on questions, a message starts the next run and the questions get no response;
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
- `overrides` change the revision's configuration for this run only. `toolsets` replaces whole toolsets; `model`, `retries` and each `subagents` edge change only the fields they set, and a `null` edge removes it; `instructions`, `skills`, `connection_tools`, `client_tools`, `plugins`, `reviewer`, `media_understanding` and `output_spec` replace the revision's value. The result is validated like a saved configuration, when you submit and again when the run starts, and frozen into the run.

```json
{"options": {"max_usage": {"requests": 40},
             "overrides": {"model": {"model_id": "mdl_..."}, "instructions": "Answer in German."}}}
```

A resume or a child result continues with the options of the run it follows. In Console, **Run options** sets the model, an instructions override, media understanding, a pinned revision and other configuration for the next run.

## Caller headers

A thread can pass non-credential context, such as a tenant or conversation ID, to its MCP connections as HTTP headers. Set `mcp_headers` when creating the thread, or change it with `PATCH …/threads/{thread_id}` and the thread's `If-Match`:

```json
{"mcp_headers": {"conn_...": {"x-tenant-id": "acme"}}}
```

Each key must be an enabled MCP connection of the workspace. At most 32 connections and 16 KiB of names and values are allowed; `authorization`, transport and protocol headers, and names the connection's own authentication uses are refused. Each run freezes the thread's headers when it starts, and forks and async children inherit them. Run views do not show them.

## Waits, approvals and questions

A run that needs something from outside ends `waiting`. Its `pending.items` lists what it waits for, and `wait_reason` summarizes it (`approval`, `client_tool`, `user_input`, or `multiple`):

```json
{
  "status": "waiting",
  "wait_reason": "approval",
  "pending": {"items": [{"tool_call_id": "call_...", "kind": "approval", "tool_name": "create_agent",
                         "arguments": {"key": "triage"}, "presentation": null}]}
}
```

| Kind          | Answer                                                                   |
| ------------- | ------------------------------------------------------------------------ |
| `approval`    | `approve`, or `reject` with an optional `reason`.                        |
| `client_tool` | `complete` with the tool's `result`.                                     |
| `user_input`  | Send the answer as a message; see [Submit a message](#submit-a-message). |

In Console, a pending approval offers **Approve once**, **Deny** and **Deny with reason**; a question offers **Continue without a response**.

### Resume a waiting run

`POST …/runs/{run_id}/resume` answers the wait with an `Idempotency-Key` and starts a successor run (trigger `resume`) that continues from the waiting run:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/runs/$RUN/resume" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H "Idempotency-Key: 5b1b3f0e-approve-1" \
  -d '{"answers": [{"tool_call_id": "call_...", "action": "approve"},
                   {"tool_call_id": "call_...", "action": "complete", "result": {"ticket": "T-42"}}]}'
```

The response is the successor run (`201`, or `200` for a replay). Answer every item in one request: an omitted approval is rejected, and an omitted client tool or question gets no response. Resuming with no answers therefore abandons the wait. Only the thread's latest run can be resumed, while nothing else runs; otherwise the request fails with `409 conflict` and reason `not_idle_waiting_head`.

## Interrupt, fork and archive

- **Interrupt**: `POST …/runs/{run_id}/interrupt` cancels a run. A run that has not started is `cancelled` at once; a running run is asked to stop at its next safe point and shows `cancel_requested_at` until then. Interrupting a cancelled run returns it; a completed, waiting or failed run answers `409` (`run_completed`, ...). Console's **Stop** interrupts the active run.
- **Fork**: `POST …/runs/{run_id}/fork` starts a new thread in the same session whose first run continues from a `completed` or `waiting` run, with a new message (the same body as a submission) and an `Idempotency-Key`. A fork of a waiting run closes its pending calls with the default answers. The fork shares the origin thread's mounted environments unless `fresh_environments` is `true`, and `environments` adds more. It copies the origin thread's memory mounts, and `memories` adds more. Failed and cancelled runs cannot be forked.
- **Archive**: `POST …/threads/{thread_id}/archive` with the thread's `If-Match` ends a thread permanently: pending messages are withdrawn, its mounts are removed, and an active run is interrupted. Its history stays readable.

A failed or cancelled run never becomes history: the thread's next run continues from its last completed or waiting run.

## Results

`GET …/runs/{run_id}` returns the run: `status`, `trigger`, `lineage` (`root`, `continue` or `fork`), `parent_run_id`, the `input` or `resume` that started it, `options`, `environment_mounts`, `memory_mounts`, `pending`, `output`, `failure {code, message}`, `usage_at_seal`, `labels` and timestamps. `output` is the agent's final text, or JSON matching its `output_spec`.

- `GET …/threads/{thread_id}/runs` lists a thread's runs, newest first. A thread's `head_run_id` is its latest completed or waiting run, `current_run_id` its active run.
- `GET …/runs/{run_id}/items` returns the run's display items (text and reasoning messages, tool calls and observations) with `position` and `complete`. Items of a run that ended while they were in progress read `interrupted`. A display keeps at most 4096 items; `dropped` counts the oldest items it removed beyond that limit.
- `GET …/runs/{run_id}/lineage` returns the run and its ancestors, nearest first, across forks.
- `GET …/runs/{run_id}/attempts` lists attempts with their `start_reason` (`initial`, `recovery` after a lost worker, `handoff` when a worker shuts down) and outcome. A run fails after `max_attempts` attempts that were not handoffs.

A failed run's `failure.code` names the cause, such as `usage_limit_exceeded`, `environment_unavailable`, a validation code, or `attempt_failed`.

## Follow a thread stream

`GET …/threads/{thread_id}/stream` is a server-sent event stream of the thread's live output:

```sh
curl -N "$A13N_URL/api/v1/workspaces/$WORKSPACE/threads/$THREAD/stream" -H "Authorization: Bearer $A13N_API_KEY"
```

| Event      | Data                                       | Meaning                                                                                                                       |
| ---------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `delta`    | `{run_id, attempt, sequence, event, item}` | One [AG-UI](https://docs.ag-ui.com) event of the active run, and the display item it changed (`{id, kind, state}` or `null`). |
| `boundary` | `{run_id, attempt, sequence}`              | The run committed a checkpoint; its items now cover everything up to this point.                                              |
| `changed`  | `{version}`                                | The thread changed (a run started or ended, the inbox changed): read the thread again.                                        |
| `reset`    | `{run_id}`                                 | The run moved to a new attempt: discard its live output and read its items again.                                             |
| `gap`      | `{run_id}`                                 | Live output was skipped: read the run's items again.                                                                          |

The stream is provisional; the run's items are the durable record. To render a thread:

1. Open the stream, then read `GET …/runs/{run_id}/items` for the active run.
2. Apply `delta` frames whose `attempt` and `sequence` come after the items' `position` (`"{attempt}-{sequence}"`).
3. On `reset` or `gap`, read the items again; on `changed`, read the thread.

The stream carries only live output the items do not cover yet. Consecutive text, reasoning or tool-argument deltas of one message or tool call that arrive within `worker.stream_coalesce_seconds` come as one `delta` whose event carries their text together. After each checkpoint the Service removes the entries its items now cover, once they are `worker.stream_trim_seconds` old; it also caps a stream at about `worker.stream_length` entries and drops it `worker.stream_ttl` seconds after the last output.

`delta` and `boundary` frames carry an SSE `id`. Reconnect with the last one in `Last-Event-ID` to continue after it. A connection that starts or resumes after removed entries receives a `gap` first, so reading the items again is always enough to recover. The Service sends a keep-alive comment every 15 seconds and ends the stream when your access to the workspace ends. The frames' JSON Schema is `proto/a13n-service/thread-stream.schema.json`.

## Usage

Every model request is recorded with its token counts and a snapshot of the model's pricing. `GET …/usage` sums the records per model:

```sh
curl "$A13N_URL/api/v1/workspaces/$WORKSPACE/usage?thread_id=$THREAD" -H "Authorization: Bearer $A13N_API_KEY"
```

```json
{"models": [{"model_id": "mdl_...", "requests": 12, "input_tokens": 48210, "output_tokens": 3104,
             "cache_read_tokens": 30112, "cache_write_tokens": 0, "cost": "0.0931"}]}
```

Filter by `run_id`, `thread_id`, `session_id`, and `ingested_after`/`ingested_before`. `cost` is `null` when no record of the model was priced. A run's `usage_at_seal` is its usage when it ended; reports that arrive later still count in `/usage`.

## Traces

With a trace backend configured (`telemetry.trace_backend`, see [logging and traces](configuration.md#logging-and-traces)), each attempt exports its Harness spans, and the workspace can query them. Console shows them under **Traces**.

- `GET …/runs/{run_id}/attempts/{attempt_id}/trace` lists an attempt's spans, including its inline subagents.
- `GET …/traces` lists root spans, one per attempt, filtered by `session_id`, `thread_id`, `run_id`, up to 8 `attribute=key:value` filters, and `started_after`/`started_before` (the last day by default, at most 31 days). A cursor is bound to these filters as given, including the window: changing `started_after` or `started_before` on the next page is `invalid_cursor`, and later pages keep the window the first page resolved.
- `GET …/traces/{trace_id}` returns a trace's root span, and `…/spans` its spans, each with name, kind, timing, status, model, usage, cost, input, output and attributes.
- `GET …/trace-backend` returns the backend `type` (`null` when none) and `queryable_since`.

Queries return only the workspace's spans. Without a backend they answer `503 unavailable`. How much content the spans hold depends on `telemetry.trace_content`.
