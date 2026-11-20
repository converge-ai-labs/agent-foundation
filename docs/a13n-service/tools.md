---
title: Tools and connections
description: Attach built-in capabilities, connect remote Model Context Protocol (MCP) servers, configure OAuth authorization, and enable dynamic tool discovery.
---

In Service, an agent interacts with code, cloud infrastructure, and enterprise data through **Tools** and **Connections**. You can enable built-in toolsets for sandbox execution and web operations, grant access to Service platform APIs, or connect remote Model Context Protocol (MCP) servers with OAuth authentication.

Choose tool integrations based on where your operational systems run:

| Integration type | Source | Configuration path |
| :--- | :--- | :--- |
| **Files, shell, or desktop UI** | [Built-in toolsets](#built-in-toolsets) | Mount an [Environment](environments.md) and enable specific action groups |
| **Web search and scraping** | [Web tools](#web-search-and-scrape) | Configure search or scraping providers in workspace settings |
| **Service management APIs** | [Platform tools](#platform-tools) | Enable granular platform groups with read/write approval controls |
| **Remote MCP servers & SaaS** | [Connections](#connections) | Register Streamable HTTP MCP endpoints or configure OAuth connectors |
| **Custom application functions** | Client tools | Define JSON schemas and resolve invocations via API batch resumption |
| **Specialized team workflows** | [Skills](skills.md) | Attach versioned procedural instructions alongside required tools |

## Built-in toolsets

Service provides foundational toolsets out of the box. Query `GET /api/v1/toolsets` to inspect the complete catalog of keys, parameter schemas, and permissions available in your deployment.

| Toolset | Capability provided | Prerequisites |
| :--- | :--- | :--- |
| `files` | Inspect, search, read, and edit project files | Enabled by default; requires a mounted user Environment |
| `shell` | Execute bash commands and manage processes | Enabled by default; requires a mounted user Environment |
| `computer` | Automated desktop mouse, keyboard, and display interactions | Requires a desktop-enabled sandbox template |
| `web` | `search`, `scrape`, `fetch`, and `download` | Select and attach a configured web search/scrape provider |
| `memory` | Read and mutate persistent Memory Space documents | Requires an active Memory Space attachment |
| `assets` | Publish environment artifacts to the workspace asset store | Enabled per agent revision |
| `browser` | Control and inspect a managed Chromium browser session | Requires browser automation bindings |

## Platform tools

Platform tools allow agents to manage Service workspace resources programmatically. You can enable them under **Advanced / Platform Features** in the agent configuration editor:

| Platform group | Managed resources & operations |
| :--- | :--- |
| `agents` | Create, inspect, update, and manage agent revisions and defaults |
| `models` | Query configured models and inspect provider catalogs |
| `connections` | Manage remote MCP servers and test tool connectivity |
| `skills` | Inspect, upload, and validate procedural skill packages |
| `environment_templates` | Create and maintain container sandbox templates |
| `schedules` | Draft cron timetables, manage paused states, and test triggers |
| `providers` | Manage external provider registrations |
| `execution` | Query sessions, audit run traces, and submit diagnostic findings |

Platform tool calls execute using short-lived credentials scoped to the caller's workspace permissions. Read actions execute automatically by default, while write and mutation actions prompt the user with interactive approval cards.

## Web search and scrape

Equip agents with real-time web retrieval capabilities:

1. In workspace settings, navigate to **Providers → Add provider** and create a search or scraping provider (e.g., Tavily, Brave Search, or custom endpoints).
2. In your agent's configuration, enable the `web` toolset and activate the specific tools you need (`search`, `fetch`, or `scrape`).
3. Attach your configured provider ID to the tool configuration:

```json title="agent_web_config.json"
{
  "toolsets": {
    "web": {
      "enabled": true,
      "tools": {
        "search": {
          "enabled": true,
          "config": {
            "provider_id": "wprov_search_123",
            "max_results": 5
          }
        },
        "fetch": {
          "enabled": true
        }
      }
    }
  }
}
```

- `search`: Queries the provider and returns structured web citations.
- `fetch` / `download`: Downloads target URLs directly using Service's internal HTTP client (respecting organizational egress policies).
- `scrape`: Renders and extracts readable Markdown from complex web pages.

## Connections

A **Connection** links your workspace to a remote Model Context Protocol (MCP) server or SaaS integration, managing shared credentials, OAuth tokens, and discovery caching centrally.

- Any team member with workspace `run` permissions can execute tools exposed by a ready connection.
- Creating or editing connections requires workspace `write` authority.
- Sensitive credentials remain encrypted in the database and are never returned in cleartext via API views.

### Remote MCP servers

To connect a remote MCP server over Streamable HTTP:

1. In Console, navigate to **Connections → New connection**.
2. Select **Custom Remote MCP** (or choose a pre-configured service).
3. Specify the server endpoint URL (e.g., `https://mcp.internal.corp/v1`).
4. Select the authentication method: `none`, `bearer`, `headers`, or `oauth`.
5. Enter the server token and click **Test connection** to verify connectivity and discover available tool schemas.

Via HTTP API, register the connection with `POST /api/v1/connections`:

```json
{
  "type": "mcp",
  "name": "Internal Knowledge Base",
  "auth": "bearer",
  "config": {
    "url": "https://mcp.internal.corp/v1",
    "tools": ["query_handbook", "search_docs"]
  },
  "credential": {
    "token": "secret_mcp_token_xyz"
  }
}
```

The optional `config.tools` array limits which tools are exposed to agents.

### MCP Apps for embedding applications

- [Embed MCP Apps guide](mcp-apps.md)

### OAuth

For connections that require user-level or service-level OAuth authorization:

| OAuth Grant | Use case & authorization flow |
| :--- | :--- |
| `authorization_code` (default) | Browser-based user authorization; user clicks **Authorize connection** and completes the login flow in their browser |
| `client_credentials` | Machine-to-machine authorization; Service uses configured client ID and secret to obtain tokens directly |

Service manages OAuth token lifecycles automatically: access tokens are refreshed transparently before expiration upon tool invocation.

### Composio connections

Service natively integrates with Composio connector providers, giving agents access to hundreds of authenticated SaaS integrations (e.g., Jira, Slack, GitHub, Google Drive):

1. Add a `composio` provider in workspace settings with your project API key.
2. In Connections, select the target application and choose your required actions.
3. Complete the hosted OAuth authorization in your browser.
4. Select the connection in your agent's configuration.

### Test and discover tools

Test connection health and discover published tool schemas at any time:

```bash
curl -X POST "$A13N_URL/api/v1/connections/$CONNECTION_ID/test" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

To inspect discovered tool schemas and argument definitions, query `GET /api/v1/connections/{id}/tools`. Results are cached in Redis according to deployment TTL policies.

### Use a connection in an agent

To grant an agent access to a connection, add it to `connection_tools`:

```json
{
  "connection_tools": [
    {
      "connection_id": "conn_knowledge_base_123",
      "tools": null,
      "permission": "ask",
      "permissions": {
        "query_handbook": "allow"
      }
    }
  ]
}
```

- `tools: null`: Exposes all tools discovered on the connection.
- `permission: "ask"`: Requires interactive approval for calls by default.
- `permissions`: Overrides specific safe operations to `"allow"` for immediate execution.

### Native tool discovery

When an agent has access to dozens of connections, listing every tool definition in the initial prompt can consume tens of thousands of context tokens.

Enable **Native tool discovery** under **Tool availability** in Console:

```json
{
  "tool_search": {
    "mode": "native",
    "sources": {}
  }
}
```

Harness leverages model-native tool search when supported by the provider (or falls back to an optimized local search tool). The model queries tools dynamically on-demand, fetching definitions only when needed.

### Grouped tool discovery

Alternatively, organize tools into logical conceptual groups using **Grouped tool discovery**:

```json
{
  "tool_search": {
    "groups": {
      "knowledge": {
        "description": "Search company policies, architecture specs, and runbooks.",
        "connections": ["conn_knowledge_base_123"]
      },
      "code_tools": {
        "description": "Execute shell commands and inspect files in the sandbox.",
        "capabilities": ["files", "shell"]
      }
    }
  }
}
```

The model sees only concise group descriptions initially. It calls `discover_tools` to expand a group's members and `call_tool` to execute them, dramatically reducing baseline context costs.

### Caller headers

To pass non-credential operational context (such as an end-user ID or correlation token) through to remote MCP servers, configure `mcp_headers` on the session or thread:

```json
{
  "mcp_headers": {
    "conn_knowledge_base_123": {
      "X-User-ID": "usr_9981",
      "X-Tenant": "apac"
    }
  }
}
```

### MCP server suggestions

Platform operators can pre-populate the connection catalog with recommended enterprise MCP servers by adding them to the Service TOML configuration:

```toml title="service.toml"
[[providers.mcp_servers]]
key = "internal-handbook"
name = "Enterprise Handbook"
description = "Internal company wiki and documentation search"
url = "https://mcp.internal.corp/handbook"
auth = "oauth"
```

Console users can click the suggestion card to pre-fill the connection form with verified organizational endpoints.
