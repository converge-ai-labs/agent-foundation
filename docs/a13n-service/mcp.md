---
title: Manage Service through MCP
description: Connect an external agent to workspace management, trace queries, and bundled documentation.
---

Service exposes a Streamable HTTP MCP endpoint at **`/api/v1/mcp/`** in `control` and `all` processes. Connect an MCP-capable client to manage workspace resources without maintaining API wrappers. Worker-only processes do not serve MCP.

## Connect with a workspace key

Create a [workspace API key](identity.md#api-keys). Configure your client's HTTP MCP connection with:

```json
{
  "url": "https://service.example.com/api/v1/mcp/",
  "headers": {"Authorization": "Bearer a13n_REPLACE_WITH_YOUR_WORKSPACE_KEY"}
}
```

This is connection information, not a universal client configuration format. Use your client's equivalent URL and header fields, keeping the key in its secret storage. Both discovery and calls require the key; browser login cookies are not accepted. The key keeps its existing workspace confinement and permissions. An optional `X-Workspace-ID` header may name only that workspace. Authentication is connection context, never a tool argument.

Set `server.public_url` to the externally reachable Service URL, and have the proxy preserve its public Host. MCP checks Host and Origin using the configured public ingress. It uses stateless HTTP requests: replicas need no sticky MCP sessions, and MCP starts no second Service runtime.

## What MCP exposes

| Available through MCP                                                                                                                                              | Use direct HTTP instead                                                                 |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| Agents and revisions; models and provider configuration; skills; connections; environment templates and instances; memories; asset metadata; webhook subscriptions | Submit input, start/resume/cancel/wait for execution, read live output and thread SSE   |
| Read-only traces, spans, and Run/Attempt trace lookup                                                                                                              | Multipart upload, archive/image/file download, raw binary content                       |
| Local documentation search                                                                                                                                         | Organization/member/permission administration, browser login, OAuth authorization flows |

Tools are generated from explicitly admitted operations in the deployment's assembled OpenAPI, including admitted Distribution extensions. New HTTP routes are not automatically exposed. Tool names derive from operation IDs; discover the exact names and parameter schemas from your deployment. No tool accepts an arbitrary URL, method, or operation name.

Read-only [Findings and analysis history](agents-and-runs.md#find-and-improve-execution-issues) are also available through MCP. Submit/review findings, prepare Finding Agent and start analysis through their direct HTTP APIs.

MCP does not grant additional permissions or approval authority. For example, managing subscriptions still requires workspace `admin`, and trace queries retain the HTTP API's tenancy filtering and redaction.

## Read and update resources

API tools take their ordinary path, query, and declared header arguments. JSON bodies stay under `request_body`; an empty object and explicit `null` are distinct from omission. For example, a memory update can use:

```json
{
  "memory_id": "mem_REPLACE_WITH_RETURNED_ID",
  "If-Match": "\"mem_REPLACE_WITH_RETURNED_ID:1\"",
  "request_body": {"guide": null}
}
```

Each API result contains `status`, `headers`, and the unchanged JSON `body`. `headers` includes `etag`, `x-request-id`, and `retry-after` when present. A `204` response has `body: null`. Pagination stays in the API body. HTTP failures are MCP tool errors with the same structured envelope, including Service error codes and details.

Read the resource first and pass its returned `headers.etag` as the **`If-Match`** argument. Missing preconditions produce `428`; stale ones produce `412`. Do not replace a stale ETag without inspecting the changed resource. MCP does not retry writes or manufacture idempotency keys. After a lost response, inspect existing resource state before deciding whether to submit another write.

## Search the installed documentation

Call `search_documents` with keywords, a `limit` from 1 to 10, and `language` (`en` by default, or `zh-CN`):

```json
{"query": "upload skill", "limit": 3, "language": "en"}
```

The tool searches titles, headings, and Markdown text bundled with the installed Service release. Results identify the installed version, source document, heading path, source lines, and a bounded text excerpt. `truncated` indicates omitted matches or excerpt text; no matches returns an empty `results` array. Use more specific keywords to narrow long results.

Search works offline and neither queries a model nor performs a business operation. Links outside `docs/a13n-service/` point outside the bundle and may describe another version. The Markdown API-reference landing page is bundled; the website's generated operation pages are not. Use this deployment's `/api/v1/openapi.json` for its full HTTP schema.

## Upload and download through HTTP

Your external agent needs its own HTTP or shell capability and the appropriate credential to follow these examples. Documentation supplies guidance, not execution capability or permission.

Set `A13N_URL` to your deployment and `A13N_API_KEY` to your workspace key. Upload a skill ZIP directly, retaining the idempotency key if retrying the same upload:

```sh
UPLOAD_KEY=$(uuidgen)
curl --fail-with-body "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Idempotency-Key: $UPLOAD_KEY" -F file=@release-notes.zip
```

Take `upload_id` from the response. The generated **create skill** MCP tool can then perform the JSON management operation with:

```json
{"request_body": {"source": {"kind": "upload", "upload_id": "upl_REPLACE_WITH_UPLOAD_ID"}}}
```

To download a revision, use the returned skill and revision IDs through HTTP:

```sh
curl --fail-with-body \
  "$A13N_URL/api/v1/skills/$SKILL_ID/revisions/$REVISION_ID/content" \
  -H "Authorization: Bearer $A13N_API_KEY" -o release-notes.zip
```

See [skill packages](skills.md#add-a-skill) and [uploads and assets](files-and-webhooks.md#uploads) for limits and other content types.

## Execute an Agent and observe its result through HTTP

Select an agent through MCP, then submit its input through HTTP. Generate one key for this message; reuse it only when retrying that same submission:

```sh
MESSAGE_KEY=$(uuidgen)
curl --fail-with-body "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"payload\":{\"content\":[{\"type\":\"text\",\"text\":\"Summarize these notes.\"}]}}"
```

Save the returned `thread.id` and `entry.id`. A returned `run` may be null while input is queued. Follow the [thread stream](agents-and-runs.md#follow-a-thread-stream), or read that entry until `assigned_run_id` identifies the run that consumed it:

```sh
curl --fail-with-body "$A13N_URL/api/v1/threads/$THREAD_ID/inbox/$ENTRY_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
curl --fail-with-body "$A13N_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

Handle `completed`, `waiting`, `failed`, and `cancelled` separately. A completed run's answer is in `output`; a waiting run needs an explicit answer, approval, or tool result. Follow-ups and resumes can create successor runs: track the input and its assigned run, not only the first run of the conversation. Client disconnection does not cancel execution. See [application integration](connect-application.md) for SDKs and the complete workflow.
