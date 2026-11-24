---
title: Tools and connections
description: Attach built-in capabilities, connect remote Model Context Protocol (MCP) servers, configure OAuth authorization, and enable dynamic tool discovery.
---

Agents in Service interact with environments, the web, and external systems through **built-in toolsets**, **platform tools**, and **Connections** (remote MCP servers and Composio SaaS connectors).

## Built-in toolsets

Inspect available toolsets and schemas via `GET /api/v1/toolsets`, and enable them per agent revision:

| Toolset    | Capability                                                     | Prerequisite                                        |
| ---------- | -------------------------------------------------------------- | --------------------------------------------------- |
| `files`    | Inspect, search, read, and edit workspace files                | Mounted [Environment](environments.md) (default on) |
| `shell`    | Run commands and manage processes                              | Mounted [Environment](environments.md) (default on) |
| `computer` | Desktop screenshot, pointer/keyboard, and accessibility        | Desktop-enabled sandbox template                    |
| `web`      | `search`, `scrape`, `fetch`, and `download`                    | Web provider for search/scrape                      |
| `memory`   | Read and update attached Memory Spaces                         | Attached [Memory](memory.md)                        |
| `assets`   | Publish environment files to the workspace asset store         | Enabled on the agent revision                       |
| `browser`  | Automate a managed or attached Chromium [Browser](browsers.md) | Thread browser binding                              |

## Platform tools

Enable granular platform groups under **Advanced / Platform Features** (`agents`, `models`, `connections`, `skills`, `environment_templates`, `schedules`, `providers`, `execution`) so an agent can inspect or manage workspace resources within the caller's permissions (reads default to `allow`; mutations prompt for approval).

## Web search and scrape

Add a search or scrape provider (Tavily, Brave Search, Firecrawl, Exa, etc.) under **Providers → Add provider**, then attach its ID in the agent's `web` toolset:

```json
{
  "toolsets": {
    "web": {
      "enabled": true,
      "tools": {
        "search": {"enabled": true, "config": {"provider_id": "wprov_search_123", "max_results": 5}},
        "fetch": {"enabled": true}
      }
    }
  }
}
```

## Connections

Register shared remote Streamable HTTP MCP servers or SaaS connectors under **Connections → New connection** or via `POST /api/v1/connections` (requires `write`; using a connection requires `run`):

```json
{
  "type": "mcp",
  "name": "Internal Knowledge Base",
  "auth": "bearer",
  "config": {
    "url": "https://mcp.internal.corp/v1",
    "tools": ["query_handbook", "search_docs"]
  },
  "credential": {"token": "secret_mcp_token_xyz"}
}
```

- **Auth modes:** `none`, `bearer`, `headers`, or `oauth` (`authorization_code` browser flow or `client_credentials` M2M, with automatic token refresh). Test health via `POST /api/v1/connections/{id}/test` and inspect schemas via `GET /api/v1/connections/{id}/tools`.
- **Bind to an agent:** Add entries to `connection_tools` with default `permission` (`"ask"`, `"allow"`, `"deny"`) and per-tool `permissions` overrides. Pass non-secret per-session headers via `mcp_headers`. Disabling a connection immediately blocks subsequent calls.
- **Tool discovery for large catalogs:** Configure `tool_search` on the agent (`{"mode": "native", "sources": {}}` or domain `groups` mapping `connections` and `capabilities`) so tool schemas load on demand. See [Embed MCP Apps](mcp-apps.md) for interactive tool UIs.

### Composio connections

Add a `composio` connector provider in workspace settings, then create a Connection for the target SaaS app (Jira, Slack, GitHub, Google Drive, etc.), complete hosted OAuth in your browser, and attach it in `connection_tools`.

### MCP server suggestions

Operators can pre-populate recommended MCP endpoints in Console via `service.toml`:

```toml title="service.toml"
[[providers.mcp_servers]]
key = "internal-handbook"
name = "Enterprise Handbook"
description = "Internal company wiki and documentation search"
url = "https://mcp.internal.corp/handbook"
auth = "oauth"
```
