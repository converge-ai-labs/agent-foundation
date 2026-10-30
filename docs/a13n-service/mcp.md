---
title: Manage Service through MCP
description: Connect an external client, update workspace resources, and investigate runs through MCP.
---

Connect an MCP-capable client to Service to manage workspace resources and inspect execution. Start by listing agents or searching the installed documentation. Then use the same connection to read and update a resource with its returned ETag.

## Connect with a workspace key

You need an API-serving Service process (`control` or `all`), a reachable URL and a [workspace API key](identity.md#api-keys) with permission for your intended operations. Worker-only processes do not serve MCP.

1. Add a **Streamable HTTP** MCP connection in your client.
2. Set its URL to your Service origin followed by `/api/v1/mcp/`.
3. Store the workspace key in the client's secret storage and send it as a Bearer header.
4. Connect and discover tools. Call `search_documents` or an agent-listing tool to confirm the connection works.

The connection information looks like this; translate it into your client's configuration format and replace the placeholder key:

```json
{
  "url": "https://service.example.com/api/v1/mcp/",
  "headers": {"Authorization": "Bearer REPLACE_WITH_WORKSPACE_KEY"}
}
```

Tool discovery and calls both require the key. Browser cookies are not accepted. The key retains its workspace scope and permissions; an optional `X-Workspace-ID` must name that same workspace. Credentials belong in the connection, not tool arguments.

For a deployment behind a proxy, set `server.public_url` to the externally reachable Service URL and preserve its public Host. MCP checks Host and Origin against the configured ingress. Requests are stateless, so replicas need no sticky MCP sessions or second Service runtime.

## What MCP exposes

Choose the transport for the task:

| Task                                                                                                                               | Transport                                                                         |
| ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Manage agents, revisions, models, providers, skills, connections, environments, memories, asset metadata and webhook subscriptions | MCP                                                                               |
| Configure schedules, preview times, enable/pause/archive, dispatch manually and read trigger history                               | MCP; see [schedules](schedules.md)                                                |
| Read sessions, threads, inbox entries, runs, attempts, display content, usage, traces and spans                                    | MCP                                                                               |
| Submit findings and read findings or analysis history                                                                              | MCP; see [execution issues](agents-and-runs.md#find-and-improve-execution-issues) |
| Search bundled documentation                                                                                                       | MCP `search_documents`                                                            |
| Submit agent input, resume/cancel/wait for execution, or follow live output/SSE                                                    | Direct HTTP                                                                       |
| Upload or download files and raw binary content                                                                                    | Direct HTTP                                                                       |
| Administer organizations, members or permissions; browser login and OAuth flows                                                    | Direct HTTP                                                                       |
| Review findings, prepare the Finding Agent or start analysis                                                                       | Direct HTTP                                                                       |

Discover exact tool names and schemas from your deployment. Tools come from explicitly admitted operations in its assembled OpenAPI, including admitted Distribution extensions. Names derive from operation IDs; newly added HTTP routes appear only when explicitly admitted. MCP adds no permissions or approval authority: subscription management still needs workspace `admin`, for example.

## Read and update resources

Practice with a standalone memory you can edit:

1. Discover the tools for reading and updating memories.
2. Read the memory by ID. Save `body.id` and the exact quoted value in `headers.etag`.
3. Call its update tool with the ID, that ETag as `expected_version`, and the change under `request_body`.
4. Read the memory again and confirm the saved guide.

For example, these update arguments restore inheritance of the deployment's default guide. Replace both placeholders with values from the read response:

```json
{
  "memory_id": "mem_REPLACE_WITH_RETURNED_ID",
  "expected_version": "mem_REPLACE_WITH_RETURNED_ID:1",
  "request_body": {"guide": null}
}
```

Path and query arguments keep their HTTP names. Header arguments use lowercase snake_case: `Idempotency-Key` becomes `idempotency_key`. The `If-Match` precondition uses `expected_version`, accepting `{id}:{version}` (`{key}:{version}` for a model) with or without quotes. HTTP header spellings are no longer accepted as tool arguments; an unknown argument produces an error listing accepted names.

JSON bodies are nested under `request_body`; omission, an empty object and explicit `null` keep their distinct meanings. A body accepting only an empty object, such as a schedule `enable` command, can be omitted; omission sends `{}`.

API tool results contain `status`, `headers` and the unchanged JSON `body`. Headers include lowercase `etag`, `x-request-id` and `retry-after` when present; an empty `204` response has `body: null`. Pagination stays in `body`. HTTP failures become tool errors with the same Service error envelope.

A missing `expected_version` returns `428`; a stale one returns `412`. Read the changed resource and reconcile your edit before trying again. MCP neither retries writes nor creates idempotency keys. After a lost response, inspect the resource to determine whether the write took effect.

## Inspect a failed run

1. Discover the execution query tools and read the run by ID. Use session or thread queries if you need to locate it first.
2. Read attempts and display content to find where execution stopped. Check the run's status too: a failed attempt can belong to a run that recovered.
3. Query the run's trace and spans for diagnostic evidence. Keep the run ID and relevant error when asking an operator for help.

These queries retain HTTP's workspace permissions, tenancy filtering and redaction. Use direct HTTP to resume or cancel work and SSE to follow live output. [Monitoring](monitoring.md#troubleshoot-a-request-or-run) explains the diagnostic fields.

## Search the installed documentation

Call `search_documents` with keywords, a `limit` from 1 to 10 and `language` (`en` by default, or `zh-CN`):

```json
{"query": "upload skill", "limit": 3, "language": "en"}
```

Read the result's installed version, source document, heading path, source lines and text excerpt. `truncated` signals omitted matches or excerpt text; an empty `results` array means no match. Refine the keywords when results are too broad.

Search runs offline over titles, headings and Markdown bundled with the installed Service release. It needs no model or web search. Links outside `docs/a13n-service/` may lead to another version. The bundle includes the API-reference landing page, but not the website's generated operation pages. Use the deployment's `/api/v1/openapi.json` for its full HTTP schema.

## Upload and download through HTTP

For a skill import, use HTTP for the ZIP bytes, then MCP for the JSON resource operation. Your client needs its own HTTP or shell capability and a key with the required permissions.

Set `A13N_URL` to the Service origin, `A13N_API_KEY` to the workspace key, and `UPLOAD_KEY` to a fresh key for this upload. Keep it for retries. With a valid local `release-notes.zip` [skill package](skills.md#add-a-skill), run:

```sh
curl --fail-with-body "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Idempotency-Key: $UPLOAD_KEY" -F file=@release-notes.zip
```

Take `upload_id` from the response and pass it to the discovered create-skill MCP tool:

```json
{"request_body": {"source": {"kind": "upload", "upload_id": "upl_REPLACE_WITH_UPLOAD_ID"}}}
```

To download a revision, set `SKILL_ID` and `REVISION_ID` to its returned identifiers. Choose an output filename that will not overwrite your source ZIP:

```sh
curl --fail-with-body \
  "$A13N_URL/api/v1/skills/$SKILL_ID/revisions/$REVISION_ID/content" \
  -H "Authorization: Bearer $A13N_API_KEY" -o downloaded-release-notes.zip
```

See [uploads and assets](files-and-webhooks.md#uploads) for upload limits and message attachments.

## Execute an Agent and observe its result through HTTP

Use MCP to select an agent; use HTTP to submit its input. Set `AGENT_ID` to that agent's ID and `MESSAGE_KEY` to a new key for this message. The workspace key must authorize execution. Reuse `MESSAGE_KEY` only when retrying the same submission:

```sh
curl --fail-with-body "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"payload\":{\"content\":[{\"type\":\"text\",\"text\":\"Summarize these notes.\"}]}}"
```

Save `thread.id` and `entry.id` as `THREAD_ID` and `ENTRY_ID`. While input is queued, the response's `run` may be null. Read the entry until `assigned_run_id` identifies its run, then set `RUN_ID` and read that run:

```sh
curl --fail-with-body "$A13N_URL/api/v1/threads/$THREAD_ID/inbox/$ENTRY_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
curl --fail-with-body "$A13N_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

An assignment identifies the run handling the input; it does not confirm completion. For `completed`, read `output`. For `waiting`, collect the requested answer, approval or tool result and resume explicitly. Handle `failed` and `cancelled` separately. Follow-ups and resumes can create successor runs, so track the input and its run rather than only the conversation's first run. Client disconnection leaves execution running.

For live output, follow the [session stream](agents-and-runs.md#follow-a-session-stream). Continue with [application integration](connect-application.md) for the full HTTP workflow and SDK options.
