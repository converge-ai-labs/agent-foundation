---
title: MCP servers
description: Configure command and Streamable HTTP MCP servers and enable them on an Agent.
---

Configure a command or Streamable HTTP server, then select its resource ID on an Agent. Creating a server file registers it; selecting its ID on an Agent or in defaults enables it. Use [Command reference](command-reference.md) for Harness UI shell commands. MCP command transports launch external programs.

## Copy a JSON configuration

Create `mcp/servers.json` beside your selected root configuration. Common client-style `mcpServers` objects work directly:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/workspace"]
    },
    "docs": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer example-token",
        "X-Workspace": "${WORKSPACE_ID}"
      }
    }
  }
}
```

Replace the illustrative endpoint, path, and token. Enable these entries with `mcp_servers: [mcp-filesystem, mcp-docs]` on an Agent or under root `defaults`. Creating the JSON file alone does not enable them.

A `mcpServers` file can define several servers. Its names normalize to `mcp-` IDs: `My_Server` becomes `mcp-my-server`. Duplicate IDs across files reject the configuration, so check the normalized IDs before adding a second file.

For a command entry, use `command`, optional `args`, and optional `env`. For a remote entry, use `url` and optional `headers`. Optional `type` accepts `stdio` for commands and `http` or `streamable-http` for remote endpoints. Remote connections use Streamable HTTP, not legacy SSE. OAuth login, JSONC comments, trailing commas, `disabled`, and unrelated client-specific fields are not supported. `mcpServers` is a common client convention, not a universal MCP protocol configuration standard.

Both `.yaml` and `.json` can also contain the single-resource format shown below; both accept the `mcpServers` wrapper. Existing YAML files continue to work without migration.

## Literal values and environment references

Command `env` (or canonical `transport.environment`) and remote `headers` accept:

| Value                   | Behavior                                                        |
| ----------------------- | --------------------------------------------------------------- |
| `"example-token"`       | A literal string, including ordinary non-secret settings        |
| `"${API_TOKEN}"`        | Read an environment variable when a Run starts                  |
| `"Bearer ${API_TOKEN}"` | Substitute `${NAME}` occurrences in a string                    |
| `{"env": "API_TOKEN"}`  | Existing explicit environment reference; works in YAML and JSON |

Empty literal strings and whitespace are preserved. References require non-empty variables in the **Harness UI process** environment; exporting in another shell does not change an already-running process. Expansion is one pass, only for `${NAME}` with a valid environment-variable name; there is no shell execution or default-value syntax. These substitutions apply only to environment/header values, not commands, arguments, or URLs.

Literal tokens and environment references are both supported. Keep credential-bearing files private and out of version control. Harness UI stores source locations and digests rather than literal environment/header values in configuration and Run captures. Keep a captured source unchanged until its client is constructed; otherwise an older Run or child continuation can fail with `mcp_source_changed`. New Runs use current configuration. Environment references can rotate without editing the source file.

After editing, run `a13n-harness-ui config validate` and start a new conversation (a new root Thread) if you changed default MCP selections. Validation does not connect to servers or verify credentials.

## Command transport

Create `mcp/github.yaml`:

```yaml title="mcp/github.yaml"
schema_version: "1"
kind: mcp_server
id: mcp-github
name: GitHub
transport:
  command: npx
  arguments: ["-y", "@modelcontextprotocol/server-github"]
  environment:
    GITHUB_TOKEN:
      env: GITHUB_TOKEN
```

Install the required executable/package and provide its token. Review the command and package before enabling the server.

## Remote transport

Create `mcp/docs.yaml` using your actual MCP endpoint:

```yaml title="mcp/docs.yaml"
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Documentation service
transport:
  url: https://mcp.example.com/mcp
  headers:
    Authorization:
      env: DOCS_MCP_AUTHORIZATION
```

The environment variable contains the entire header value expected by the server, including any authentication scheme. Alternatively, use `"Bearer ${DOCS_MCP_TOKEN}"` to interpolate only the token. URLs must be credential-free HTTPS; plain HTTP is allowed only for a literal loopback host with no configured headers. Authenticated redirects cannot weaken transport or leak headers to another origin.

## Enable a server

In an Agent file:

```yaml
mcp_servers: [mcp-github, mcp-docs]
```

Or in root YAML:

```yaml
defaults:
  mcp_servers: [mcp-github]
```

An Agent's `mcp_servers: null` inherits the root defaults; `[]` explicitly selects none. Existing conversations retain exact sticky selections. By default, clients/processes are constructed fresh for logical Runs. Root lifetime policy can retain selected clients, and enabled [MCP Apps](mcp-apps.md) automatically retain theirs. Neither client state nor pending MCP input is serialized into continuations. Selecting the Sandbox Environment mode does not sandbox an external MCP command or remote service.

## Connection lifetime and protocol

To preserve a server's process-local state between Runs, configure the root document:

```yaml
mcp:
  host_owned_servers: [mcp-docs]
  protocol_overrides:
    mcp-docs: auto
```

`host_owned_servers` defaults to `[]`; it retains clients but does not select tools. Select the server on the Agent or Thread as usual. Each Thread has its own client, including child Threads. Run completion and browser disconnection leave retained clients open. Host shutdown, explicit close, Thread disposal, or a changed binding ends the connection. Connections are process-local. After disconnection, calls are not automatically replayed; activating an MCP App can establish a replacement.

`protocol_overrides` defaults to `{}`. Each configured server ID accepts `auto`, `legacy`, or `2026-07-28`; an omitted ID uses `auto`. The override controls the upstream SDK's Core negotiation, independently of connection lifetime and the [MCP Apps UI wire protocol](mcp-apps.md). All policy IDs must name existing MCP resources. Existing YAML/JSON and saved recipes without these fields retain Run-local lifetime and automatic negotiation.

## Human input from MCP servers

Answer MCP form and URL requests in the TUI or WebUI to continue the original operation. The conversation also shows requests from child Threads, labelled with their Thread, server, and available Run/tool/App source.

Forms support primitive string, numeric and boolean fields, single-select enums and multi-select string enums, including titled options. The Host validates answers against the original schema. Unsupported schemas, remote references and declared sensitive fields are rejected; never enter passwords, tokens or other secrets into MCP forms. URL requests show the destination and require an explicit browser action outside the App iframe; complete the external flow before confirming.

Choose accept, decline, or cancel within five minutes. Cancellation, expiry, connection closure, or shutdown ends the request. If delivery is uncertain, inspect the request or resend the exact answer; conflicting answers are rejected. Input acceptance does not confirm that the remote operation finished. Embedded interfaces must enable their input handler; see [embedding](embedding.md#mcp-input-and-integration-control).

## MCP field reference

The shared fields are `schema_version: "1"`, `kind: mcp_server`, unique `mcp-` `id`, and `name`. `transport` accepts exactly one form:

| Form    | Fields                                                                                                                    |
| ------- | ------------------------------------------------------------------------------------------------------------------------- |
| Command | Required `command`; `arguments` defaults to `[]`; `environment` defaults to `{}`, values are strings or `{env: VARIABLE}` |
| Remote  | Required `url`; `headers` defaults to `{}`, values are strings or `{env: VARIABLE}`                                       |
