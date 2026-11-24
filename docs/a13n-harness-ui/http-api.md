---
title: HTTP API
description: Integrate scripts and applications with Harness UI's WebUI HTTP API and Management MCP endpoint to create threads, steer runs, post Inbox items, and stream updates.
---

The WebUI listener exposes a JSON HTTP API, WebSocket channels, and a Management MCP endpoint for programmatic automation. Authenticate with the instance key (`Authorization: Bearer $HUI_API_KEY`) and inspect `/api/openapi.json` (or `/reference/harness-ui-openapi.json`) for the complete schema.

## Manage Projects, Threads, and Inbox over MCP

Connect a Streamable HTTP MCP client to `$HUI_URL/api/automation/mcp` with `Authorization: Bearer <instance-key>`:

- **Projects & Threads:** Call `list_projects`, `get_project`, and `create_project` to manage Projects; `list_threads` and `get_thread(thread_id)` to inspect conversations; `create_thread(prompt, project_id?)` to start one; and `send_thread_message(thread_id, message)` to start a Run (when idle) or steer the active Run.
- **Post to Inbox:** Call `post_to_inbox(thread_id, kind, title, message)` using an existing ordinary, Coordinator, or Bot `thread_id`, `kind` (`result`, `needs_human`, or `milestone`), and standalone Markdown `message` to return an `item_id` in [Inbox](webui.md#read-your-inbox) without adding a chat turn or starting a Run (requires `inbox.enabled`; see [HTTP Inbox routes](#inbox)).

## Create a Thread and submit input

Save this as `conversation.sh` (requires `HUI_URL` and `HUI_API_KEY`):

```bash title="conversation.sh"
#!/usr/bin/env bash
set -euo pipefail
: "${HUI_URL:?Set the listener origin}"
: "${HUI_API_KEY:?Set the instance key}"

api() {
  local path=$1
  shift
  curl --silent --show-error --fail-with-body \
    -H "Authorization: Bearer $HUI_API_KEY" \
    "$HUI_URL$path" "$@"
}

api /api/setup | jq .
api /api/selectors | jq .
thread=$(api /api/threads -H 'Content-Type: application/json' \
  --data '{"title":"API conversation"}')
thread_id=$(jq -er '.thread_id' <<<"$thread")
printf 'Thread: %s\n' "$thread_id"

receipt=$(api "/api/threads/$thread_id/submit" \
  -H 'Content-Type: application/json' \
  --data '{"parts":["Reply with one short greeting. Do not use tools."]}')
receipt_id=$(jq -er '.receipt_id' <<<"$receipt")
printf 'Receipt: %s\n' "$receipt_id"

while true; do
  operation=$(api "/api/operations/$receipt_id")
  status=$(jq -er '.status' <<<"$operation")
  case "$status" in
    preparing|running) sleep 1 ;;
    completed|suspended|failed|cancelled) break ;;
    *) printf 'Unexpected status: %s\n' "$status" >&2; exit 1 ;;
  esac
done

jq '{status, outcome, failure}' <<<"$operation"
api "/api/threads/$thread_id/transcript" | jq .
if [[ "$status" == suspended ]]; then
  api "/api/threads/$thread_id/decisions" | jq .
fi
```

### Give the conversation a Project

Pick a `project_id` from `GET /api/selectors` and pass it when creating the Thread:

```json
{"title":"Repository conversation","defaults":{"project_id":"project-main"}}
```

Inspect `GET /api/threads/{thread_id}/configuration` to verify the resolved working directories before submitting work.

### Change a running operation

- **Steer:** `POST /api/operations/{receipt_id}/steer` with `{"parts":["Focus on the configuration files."]}`.
- **Cancel:** `POST /api/operations/{receipt_id}/cancel`.
- **Tool executions:** List via `GET /api/threads/{thread_id}/tool-executions` and cancel via `POST /api/threads/{thread_id}/tool-executions/{execution_id}/cancel`.

## Run-only execution environment

Override the environment, model, or reasoning settings for a single `/submit` call without modifying the Thread's saved configuration:

```json
{
  "parts": ["Review this project"],
  "environment": {
    "environment_profile_id": "environment-sandbox",
    "local_roots": ["/absolute/path/to/code"],
    "environment_bindings": [],
    "default_environment": "workspace"
  }
}
```

`/submit` also accepts optional `model_id`, `thinking`, `fast`, and `reasoning_mode`.

### Ordered input bodies

Root `/submit` and `/steer` accept ordered strings and uploaded attachment IDs in `parts`:

```json
{
  "parts": [
    "Compare ",
    {"attachment_id": "attachment-first"},
    " with ",
    {"attachment_id": "attachment-second"}
  ]
}
```

Upload attachments first via `POST /api/threads/{thread_id}/attachments`.

## Common endpoints

### Decisions, history, and lifecycle

| Route                                           | Purpose                                              |
| ----------------------------------------------- | ---------------------------------------------------- |
| `GET /api/threads/{id}/decisions`               | List pending questions, approvals, or external tools |
| `POST /api/threads/{id}/decisions`              | Submit decision responses                            |
| `GET /api/threads/{id}/inputs` / `/transcript`  | Paginate user turns and full message history         |
| `DELETE /api/threads/{id}?expected_version=<v>` | Permanently delete an idle root Thread               |

### Edit reusable resources

Read and update YAML files under `GET/PUT /api/configuration/sources/{path}` (for example, `devices/build.yaml` or `web_providers/research.yaml`), and manage secrets via `/api/auth/keys`.

### Change saved Thread selections

Update `PATCH /api/threads/{id}/configuration` or preview/apply Project defaults.

### Bot

Manage the always-on Bot conversation and status under `/api/bot/*`. See [Bot](bot.md).

### Inbox

Read and resolve agent-posted items via `/api/inbox/*` (see [Read your Inbox](webui.md#read-your-inbox)).

### Coordinators, MCP, and Host tools

- **MCP & Connectors:** Inspect/close retained MCP clients, answer [MCP human input](mcp.md#human-input-from-mcp-servers), and manage OAuth Connectors via `/api/mcp/*` and `/api/connectors/*`.
- **Host Files, Git, & Terminal:** When computer sharing is enabled, browse files (`/api/files/*`), inspect Git diffs (`/api/git/*`), and open terminals (`/api/terminals/*`).

## Memory observation

Inspect Global and Project memory files and automatic organization runs under `/api/memory/*`.
