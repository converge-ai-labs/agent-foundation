---
title: MCP servers
description: Connect stdio and streamable HTTP MCP servers in Harness UI, configure OAuth and tool filters, and manage host-owned connections and human input.
---

Connect Model Context Protocol (MCP) servers to give Agents external tools and data sources. Define servers under `mcp_servers/*.yaml` (or in **Settings → Connections → MCP**).

## Add an MCP server

Save a stdio or Streamable HTTP server definition beside your root config:

```yaml title="mcp_servers/docs.yaml — stdio server"
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Docs MCP
transport: stdio
command: npx
args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/docs"]
```

```yaml title="mcp_servers/remote.yaml — Streamable HTTP server"
schema_version: "1"
kind: mcp_server
id: mcp-remote
name: Remote MCP
transport: streamable_http
url: https://mcp.example.com/mcp
headers:
  Authorization:
    env: REMOTE_MCP_TOKEN
```

Enable the server in a Project's `defaults.mcp_servers`, an Agent's `defaults.mcp_servers`, or in conversation settings. Validate with `a13n-harness-ui config validate`.

## MCP field reference

| Field                    | Purpose                                                             |
| ------------------------ | ------------------------------------------------------------------- |
| `id`, `name`             | Unique `mcp-` ID and display name                                   |
| `transport`              | `stdio` or `streamable_http`                                        |
| `command`, `args`, `env` | Process launch settings for `stdio`                                 |
| `url`, `headers`         | Endpoint URL and HTTP headers for `streamable_http`                 |
| `tools`                  | Optional allowlist/denylist of exposed tool names                   |
| `oauth`                  | Optional OAuth / DCR configuration for authenticated remote servers |

### Literal values and environment references

Use `{env: VAR_NAME}` or `{credential_ref: key-id}` for secrets in `env` and `headers` instead of literal strings.

## OAuth authorization

For MCP servers that require OAuth (including Dynamic Client Registration), configure `oauth` on the server or add it through **Settings → Connections** in WebUI. Sign in and reconnect through the WebUI authorization flow; tokens are stored in the local credential store.

## Connection lifetime and protocol

- By default, MCP clients are scoped to each Run. Add a server ID to `mcp.host_owned_servers` in `a13n-harness-ui.yaml` to keep its client alive across Runs.
- Override protocol negotiation per server with `mcp.protocol_overrides` (`auto`, `legacy`, or `2026-07-28`).

## Human input from MCP servers

When an MCP server requests elicitation or sampling approval, Harness UI surfaces a decision card in WebUI or TUI outside any iframe. Answer or decline it there to continue the tool call.
