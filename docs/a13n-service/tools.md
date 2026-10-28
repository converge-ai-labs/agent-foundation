---
title: Tools and connections
description: Select tools for an agent, connect an external service, and verify a tool call.
---

Start with an agent that can [answer a message](use-platform.md). Add one tool source, save the agent's [revision](agents-and-runs.md#agent-configuration), then ask it to perform a small task and inspect the tool result.

Choose the source by where the operation runs:

| Task                                      | Source                            | Setup                                                                                                                     |
| ----------------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Read files, run commands or use a desktop | Built-in toolsets                 | Mount an [environment](environments.md) and enable the required actions                                                   |
| Search or retrieve web content            | Web tools                         | Select a web provider for search and scrape                                                                               |
| Read or change Service resources          | [Platform tools](#platform-tools) | Enable the relevant group and set tool permissions                                                                        |
| Call a remote MCP server or connected app | [Connections](#connections)       | Create, authorize and test a workspace connection                                                                         |
| Call a function in your application       | Client tools                      | Declare its schema, handle `pending.calls`, then [resume with the results](agents-and-runs.md#client-tools-and-questions) |
| Teach an agent a workflow                 | [Skills](skills.md)               | Attach instructions and files alongside the tools the workflow needs                                                      |

Set [tool permissions](agents-and-runs.md#tool-permissions) in the revision: allow, ask for approval, use model review, or deny. A selected tool also needs its underlying resource and runtime permissions.

## Built-in toolsets

Use `GET /api/v1/toolsets` to inspect your deployment's tool keys, model-visible names, defaults and configuration schemas. Select tools by those keys; for example, the terminal's `exec` key exposes `shell_exec` to the model.

| Toolset    | Use                                              | Default and prerequisite                                               |
| ---------- | ------------------------------------------------ | ---------------------------------------------------------------------- |
| `files`    | Read, search, write and organize files           | Enabled; requires a mounted environment                                |
| `shell`    | Start commands and inspect or control processes  | Enabled; requires a mounted environment with the relevant actions      |
| `computer` | Observe and interact with a desktop              | Disabled; requires a compatible desktop and explicit mount permissions |
| `web`      | `search`, `scrape`, `fetch`, `download`          | Each tool disabled; enable individually                                |
| `memory`   | Read and update file or record memories          | Enabled; requires a matching memory mount                              |
| `assets`   | Publish an environment file with `publish_asset` | Disabled; produces a workspace asset                                   |

After selecting file or terminal tools, mount an [environment](environments.md) before testing the agent. For desktop setup and screenshot previews, follow [Computer Use](environments.md#computer-use). A [memory](memory.md) mounted as `read` exposes only viewing, listing and searching; writable mounts offer the matching mutation tools. Published files become [assets](files-and-webhooks.md#assets).

## Platform tools

Enable the required group under **Advanced / Platform Features** in the agent's tools. Each group starts disabled. Enabling it enables its operations, so turn off operations the agent does not need.

| Group                   | Resources and operations                                             |
| ----------------------- | -------------------------------------------------------------------- |
| `agents`                | Agents, revisions and the toolset catalog                            |
| `models`                | Models and the model catalog                                         |
| `connections`           | Connections, connection tools and connector apps                     |
| `skills`                | Skills and their versions                                            |
| `environment_templates` | Environment templates                                                |
| `schedules`             | Create and manage schedules, dispatch them, and read trigger history |
| `providers`             | Providers and tests of saved connections                             |
| `execution`             | Read sessions, threads, runs, traces and findings; submit findings   |

Platform calls use the run's short-lived credential within the authority of the person or key that started it. Each operation uses its MCP tool name as its configuration key. The `inherit` permission means `allow` for reads and `ask` for writes. The only write in `execution` is `POST /api/v1/findings`; choose one primary [finding category](agents-and-runs.md#find-and-improve-execution-issues) per issue.

Use the [Agent Composer example](agent-composer.md#use-the-toolset-in-your-own-agents) to pair platform tools with their operating skill. Verify a read first, then test the approval path for any writes you enabled.

## Web search and scrape

To add search to an agent:

1. Create a [web provider](resources.md#providers) in workspace settings. Choose a type that supports search and supply its credential when required.
2. Enable the `web` toolset and its `search` tool. Select the saved provider in the tool configuration.
3. Save the revision and ask the agent to search for a specific topic. Inspect the returned sources.

This agent-configuration fragment enables search and direct fetching. Replace `wprov_...` with your provider ID:

```json
{
  "toolsets": {
    "web": {
      "enabled": true,
      "tools": {
        "search": {
          "enabled": true,
          "config": {"provider_id": "wprov_...", "max_results": 5}
        },
        "fetch": {"enabled": true}
      }
    }
  }
}
```

`search` and `scrape` need a provider serving that operation; saving the revision checks this selection. `fetch` and `download` use Service's HTTP client and need no provider. Enabling only the toolset leaves all four tools disabled. All requests follow the deployment's [outbound policy](configuration.md#outbound-requests).

| Tool     | Configuration limit                |
| -------- | ---------------------------------- |
| `search` | `max_results`: 1–10                |
| `scrape` | `max_content_bytes`: up to 4 MiB   |
| `fetch`  | `max_content_bytes`: up to 256 KiB |

`allow_domains` and `deny_domains` constrain supported operations. A bare hostname includes its subdomains; empty lists add no domain restriction. Built-in scrape providers do not support domain restrictions, so `scrape` rejects nonempty domain lists. Use the provider catalog and tool schemas for the options available in your deployment.

![Console search and scrape provider catalog](../../.github/assets/console-search-providers.webp)

## Connections

A connection gives agents access to one remote MCP server or one connector app account. It belongs to a workspace and holds one shared credential. Anyone with workspace `run` permission can use that account through an agent; keep a private account in a personal workspace. Creating or changing a connection requires `write`.

The setup path is: create the connection, authorize it if needed, [test its tools](#test-and-discover-tools), then [select it in an agent](#use-a-connection-in-an-agent). A connection is usable when its status is `ready`; `pending` means it lacks a usable credential, and `reauthorization_required` means authorization must be restored. Credentials are write-only; views report `credential_configured` and `client_secret_configured`.

To retire a connection, disable it with `PATCH {"enabled": false}` or revoke its credential with `POST …/revoke`. There is no delete operation. Disabling blocks subsequent use, including calls from active runs. Revoking clears local credentials immediately and reports best-effort remote revocation as `revoked`, `failed` or `skipped`. Revocation is also available for disabled connections and archived workspaces.

Read the connection's current ETag and send it as `If-Match` for updates, authorization and revocation. Configuration changes and completed or lost authorizations change the version; routine token renewal and test results leave it unchanged.

### Remote MCP servers

1. Open **Connections → New connection** in Console.
2. Choose a server from the directory, or **Custom Remote MCP** for your own Streamable HTTP endpoint.
3. Choose the server's authentication: `none`, `bearer`, `headers` or `oauth`. Supply credentials or complete the [OAuth flow](#oauth).
4. Test discovery and confirm the tool names you want to expose.

![Console remote MCP server directory](../../.github/assets/console-mcp-connection.webp)

For an HTTP integration, send this body to `POST /api/v1/connections` using the [workspace authentication setup](connect-application.md#direct-http-integration). Replace the example endpoint, token and tool names with your server's values:

```json
{
  "type": "mcp",
  "name": "Docs search",
  "auth": "bearer",
  "config": {
    "url": "https://mcp.example.com/mcp",
    "tools": ["search_docs", "read_doc"]
  },
  "credential": {"token": "REPLACE_WITH_SERVER_TOKEN"}
}
```

`config.tools` limits the tools exposed by the connection. Omitting it exposes all listed tools; more than 128 exposed tools makes a run fail with `connection_tools_exceeded`. Put credentials in the authentication fields, not the URL. For static headers, list names in `config.headers` and supply values in `credential.headers`; transport and protocol headers are reserved.

Changing the endpoint, authentication or OAuth settings clears the saved credential. Changing only the name, enabled state or tool selection preserves it. The endpoint must pass the [outbound policy](configuration.md#outbound-requests).

Service keeps the MCP client for one execution attempt and checks current connection availability and run authority before each call. For a durable human response, use client tools or agent questions; Service's MCP connection has no elicitation response channel.

### MCP Apps for embedding applications

An embedding application can render an MCP App from a tool result. Service saves the original arguments, declaration and full result, including `_meta`; it fetches HTML when the application opens the App. Console does not render Apps.

For live output:

1. Subscribe to `GET /api/v1/sessions/{session_id}/stream`.
2. Treat a delta containing `a13n.mcp_apps` as a loading reference. Even `unavailable: false` is provisional here.
3. Wait for `app_ready`, which confirms that the original data and reference are committed. Identify each App by `(run_id, app_id)`; readiness events can repeat after reconnect.
4. Open the App using the event's `run_id` and `apps[].app_id`.

The event follows committed `items` after a checkpoint containing the App; the run need not have finished. Failed publication produces an unavailable reference and no ready event. Available committed references loaded from `GET /api/v1/runs/{run_id}/items` can be opened directly, including after a disconnected run has ended. The thread stream does not emit `app_ready`.

| Request                                                   | Body                                  | Result                                          |
| --------------------------------------------------------- | ------------------------------------- | ----------------------------------------------- |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/open`           | None                                  | Saved original data and current HTML            |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/tools/call`     | `{"name":"increment","arguments":{}}` | Raw MCP result under `result`                   |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/resources/read` | `{"uri":"ui://counter/app.html"}`     | Current HTML and metadata for the saved App URI |

Each request uses a fresh MCP client and current access checks. Opening never repeats the original tool; servers requiring the worker's original MCP session are unsupported. Button calls must satisfy the original agent's tool selection and permission, the current connection's selection, and App visibility. Operations requiring approval or model review are refused. Deployments with an admission policy also need explicit App admission support.

Create the iframe, enforce resource CSP and isolation, and supply the original arguments and result through your MCP App bridge. Your application owns view state and submits context or messages through the existing run API. Follow-up tool results stay with the caller; they do not replace the original or enter model history. Calls are not retried automatically: inspect external state before retrying `outcome_unknown`. HTML is fetched afresh and may differ from the version that produced an old result. When upgrading to checkpoint format 4, upgrade control processes and readers before starting new workers.

### OAuth

Choose the grant for the account you want the workspace to share:

| Grant                          | How to authorize                                                                              |
| ------------------------------ | --------------------------------------------------------------------------------------------- |
| `authorization_code` (default) | Use a login session and finish authorization in the same browser                              |
| `client_credentials`           | Configure a client ID and secret authentication; `authorize` obtains a machine token directly |

For browser authorization, choose **Authorize connection** in Console. An integrating application calls `POST /api/v1/connections/{connection_id}/authorize` with the current `If-Match` and an optional `return_url`, then opens `redirect_url` before `expires_at`. API keys cannot start browser flows. A different browser fails with `browser_mismatch`; an expired flow fails with `authorization_expired`. Starting a new flow keeps the current credential until the replacement completes.

Without `config.oauth.client_id`, authorization dynamically registers a public client. For a pre-registered client, set `client_id` and `token_endpoint_auth_method` (`none`, `client_secret_basic` or `client_secret_post`), supplying `client_secret` for a secret method. Register the callback returned by `GET /api/v1/connections/redirect-uri`. A `client_credentials` grant requires a secret method, accepts API-key authorization, and returns `redirect_url: null`.

`config.oauth.scopes` selects scopes; an empty list requests those advertised by the server. The optional `return_url` must share the origin of [`server.public_url`](configuration.md#required-infrastructure) or exactly match an entry in [`providers.return_urls`](configuration.md#outbound-requests).

Tokens renew on use when supported by the grant. Concurrent callers share the renewal operation. If status becomes `reauthorization_required`, authorize again; inspect `failure` for the reason. A refused grant or an uncertain refresh outcome can clear the credential. An unsent request, such as one blocked by outbound policy, preserves it. A failed client-credentials renewal also requires authorization again.

### Composio connections

A Composio connection binds one app account; Composio holds and refreshes that account's tokens.

1. Add a `composio` connector provider with your Composio project API key in workspace settings.
2. Browse `GET /api/v1/connector-providers/{provider_id}/apps`, then the app's details and `/actions`.
3. Create a connection with `type: "composio"`, `auth: "account"`, and `connector_provider_id`. In `config`, specify `app`, 1–128 pinned `actions`, and `setup` with `auth_config_id` and a pinned `toolkit_version` (`YYYYMMDD_NN`). Use an existing auth config or a creation value such as `create:OAUTH2`.
4. Authorize from a login session and finish the hosted account setup in the same browser.
5. Test discovery and select the connection in the agent.

Create separate connections for separate accounts. Each connection exposes its pinned actions. Tool calls carry a stable request ID; if an action's outcome is unknown, inspect the external state before calling it again.

### Test and discover tools

Call `POST /api/v1/connections/{connection_id}/test` to discover tools with the current configuration and credentials. Inspect the returned `status` and `message`: a failed test can return a successful HTTP response with `status: "failed"`. The connection view records `last_test`.

Use `GET /api/v1/connections/{connection_id}/tools` to inspect names, input/output schemas and annotations. Results are cached for `providers.discovery_ttl`. Both operations require `run`. Before a connector account is bound, `/tools` shows the app's action catalog and `test` fails. Read-only annotations describe tools; they do not grant permission.

### Use a connection in an agent

Add `connection_tools` to the agent configuration. This fragment allows document search and asks for approval for other exposed tools. Replace `conn_...` with your connection ID and use tool names returned by discovery:

```json
{
  "connection_tools": [{
    "connection_id": "conn_...",
    "tools": null,
    "permission": "ask",
    "permissions": {"search_docs": "allow"}
  }]
}
```

`tools: null` selects all tools the connection exposes; a list selects at most 128 names. A revision can select up to 128 connections. Saving checks that each connection is enabled and exposes the selected tools. Per-tool `permissions` override the connection-wide `permission`.

Save the revision, start a run, and ask for a document search. Inspect the tool result before testing a write. The connection must be `ready`; every call checks that it is still enabled and authorized and that the run can dispatch. Calls use `providers.tool_call_seconds` and are never retried automatically.

### Grouped tool discovery

For a large tool collection, select **Grouped proxy search** under **Tool discovery** in Console. Add a group and select its sources. Grouping changes how the model finds tools; first enable and configure those sources normally.

This fragment groups a selected connection. Replace `conn_...` in both places:

```json
{
  "connection_tools": [{"connection_id": "conn_...", "permission": "allow"}],
  "tool_search": {
    "groups": {
      "knowledge": {
        "description": "Search the team handbook and project documentation",
        "connections": ["conn_..."]
      }
    }
  }
}
```

The model searches with `discover_tools`, reads definitions with `describe_tool`, and calls members through `call_tool`. `list_tool_groups` lists groups; `list_tools(detail="full")` and `next_offset` page through definitions. Member schemas stay out of later model requests, while execution keeps the original credentials, caller headers and permissions.

Groups accept selected `connections`, `plugins` by instance name, `capabilities` (`environment`, `web`), and `platform: true` for enabled platform tools. A source belongs to at most one group. Names start with a letter, contain at most 32 letters, digits, underscores or hyphens, and exclude `__`.

Ungrouped sources stay directly exposed. Keep tools requiring approval/resume direct; `client_tools` retain their external-call protocol. Each child agent uses its own revision's groups. A run override replaces the whole `tool_search` block; `groups: {}` disables grouping. See [Harness ToolSearch](../a13n-harness/tool-search.md) for optional `config`, control names and pagination limits.

### Caller headers

Put non-credential context, such as a conversation ID, in the thread's `mcp_headers`: `{connection_id: {header: value}}`. A run captures the map when accepted; edits affect later runs. Connection authentication headers cannot be overridden. Follow the [caller headers example](agents-and-runs.md#caller-headers).

## MCP server suggestions

Operators can prefill the connection form with their own server. Add an entry to deployment configuration; its `key` adds to or replaces the packaged catalog:

```toml
[[providers.mcp_servers]]
key = "handbook"
name = "Team handbook"
description = "Search the internal handbook."
url = "https://mcp.example.com/handbook"
auth = "oauth"
```

`GET /api/v1/mcp-servers?query=...` exposes the suggestions to Console and other clients. Replace the example endpoint with your server. A suggestion prefills a form; creating the connection still validates the endpoint and authentication.
