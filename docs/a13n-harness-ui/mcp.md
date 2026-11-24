---
title: MCP servers
description: Connect stdio and Streamable HTTP MCP servers in Harness UI, configure OAuth DCR and tool filters, and manage host-owned connections and human input.
---

Connect Model Context Protocol (MCP) servers in three steps: register a file under `mcp/*.yaml` beside your root `a13n-harness-ui.yaml` (or under **Settings → Connections → MCP**), select the server on an Agent or Project, and validate with `a13n-harness-ui config validate`.

## Add an MCP server

Save either a **Streamable HTTP** remote transport or a local **stdio command** transport under `mcp/`:

```yaml title="mcp/docs.yaml — Streamable HTTP server"
schema_version: "2"
kind: mcp_server
id: mcp-docs
name: Documentation service
transport:
  url: https://mcp.example.com/mcp
  init_timeout_seconds: 20
  headers:
    Authorization:
      env: DOCS_MCP_AUTHORIZATION
```

```yaml title="mcp/local.yaml — stdio command server"
schema_version: "2"
kind: mcp_server
id: mcp-local
name: Local tools
transport:
  command: /absolute/path/to/mcp-server
  arguments: ["--readonly"]
  environment:
    API_TOKEN:
      env: LOCAL_MCP_TOKEN
```

Enable the server by adding `mcp_servers: [mcp-docs]` on an Agent (`agents/*.yaml`), in a Project's `defaults.mcp_servers`, or in conversation settings (`null` inherits defaults; `[]` selects none). You can also paste standard client `{"mcpServers": {...}}` JSON into **Settings → Connections**.

## MCP field reference

| Field                    | Purpose                                                                                                                                                  |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schema_version`, `kind` | `"2"` and `mcp_server`                                                                                                                                   |
| `id`, `name`             | Unique `mcp-` ID and display name                                                                                                                        |
| `transport`              | Either `{url, init_timeout_seconds, headers, oauth}` (HTTPS, or loopback HTTP without headers) or `{command, arguments, environment}` (launched on Host) |
| `tools`                  | `null`/omitted (all tools), `[]` (no tools), or `[exact_name, ...]` allowlist                                                                            |
| `object_ref_tools`       | Optional list/patterns of tools opted into [Object Reference Transport](../a13n-harness/capabilities.md#object-reference-transport)                      |
| `on_error`               | Startup failure handling (`fail` or `skip` where supported)                                                                                              |

### Literal values and secret references

Inside `transport.headers` and `transport.environment`, reference secrets with `{env: VAR_NAME}` or `{credential_ref: key-id}` (or `"Bearer ${TOKEN}"` string interpolation where supported) rather than committing plaintext credentials.

## OAuth authorization

For HTTPS servers supporting OAuth Dynamic Client Registration (DCR), omit `headers` and configure `transport.oauth`:

```yaml title="mcp/oauth-docs.yaml"
schema_version: "2"
kind: mcp_server
id: mcp-oauth-docs
name: OAuth documentation service
transport:
  url: https://mcp.example.com/mcp
  oauth:
    registration: dcr
    scope: read
```

Open **Settings → Connections** in WebUI, select the server, and click **Connect** to complete browser consent (for remote WebUI deployments, configure the exact public HTTPS origin in `webui.allowed_origins` and route `/oauth/mcp/callback`). Tokens refresh automatically across WebUI and TUI runs sharing the same data root.

## Connection lifetime and protocol

- **Per-Run vs. Host-owned:** By default, MCP clients open per Run. Add server IDs to `mcp.host_owned_servers` in `a13n-harness-ui.yaml` to retain warm connections across Runs (inspect or close via `mcp_status` / WebUI **Connections**).
- **Protocol overrides:** Set `mcp.protocol_overrides` per server ID (`auto`, `legacy`, or `2026-07-28`) when a server requires a specific MCP protocol revision.

## Human input from MCP servers

When an active MCP server requests a form elicitation or URL confirmation, Harness UI surfaces an interactive prompt card in WebUI or TUI outside any tool iframe (`accept` with form content, `decline`, or `cancel`).
