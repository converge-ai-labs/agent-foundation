---
title: MCP servers
description: Connect an external tool server, select it for an Agent, and verify a tool call.
---

Adding MCP tools takes three steps: **register the server, select it, test a call**. You need an existing [Agent](agents-and-subagents.md) and either a Streamable HTTP endpoint or an installed command-line MCP server.

Run `a13n-harness-ui config path`. Save server files under `mcp/` beside the root YAML shown as `configuration_path`. Choose one of the formats below for each server; do not register the same ID twice.

| Server information you have                          | Use                                                     |
| ---------------------------------------------------- | ------------------------------------------------------- |
| An HTTPS MCP endpoint                                | [Remote transport](#remote-transport)                   |
| A command that serves MCP over standard input/output | [Command transport](#command-transport)                 |
| A client configuration with `mcpServers`             | [Copy a JSON configuration](#copy-a-json-configuration) |

## Remote transport

1. Obtain the server's Streamable HTTP endpoint and authentication requirements. The example address below is a placeholder.

2. Save this complete file as `mcp/docs.yaml`, replacing the URL:

   ```yaml title="mcp/docs.yaml — complete file; replace endpoint"
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

3. Set `DOCS_MCP_AUTHORIZATION` in the process that launches Harness UI. Its value must be the entire header expected by the server, including a scheme such as `Bearer`. Omit `headers` for a server that needs none.

4. [Enable the server](#enable-a-server) and test a documented read-only tool.

Use a credential-free HTTPS URL. Plain HTTP is supported only for a literal loopback host with no configured headers. The client does not follow redirects; configure the final MCP endpoint directly. Remote transport is Streamable HTTP, not legacy SSE. Servers supporting DCR can use [OAuth](#oauth-authorization) instead of an Authorization header.

Set `transport.init_timeout_seconds` to a positive number of seconds to allow a slower handshake. For example, `20` gives the server up to 20 seconds to negotiate the protocol, including any fallback. The default is 5 seconds for Run-local connections and Settings tool discovery, and 30 seconds for Host-owned connections and MCP Apps. Tool calls have separate deadlines.

## OAuth authorization

For a server supporting OAuth dynamic client registration (DCR), omit the Authorization header and add `oauth` to the remote transport:

```yaml title="mcp/docs.yaml — replace transport"
transport:
  url: https://mcp.example.com/mcp
  oauth:
    registration: dcr
    scope: read
```

`registration` defaults to `dcr`, the only supported mode. Omit `scope` to use the server's discovered scopes; an explicit scope limits the requested and accepted permissions. In a `mcpServers` entry, put `oauth` beside `url`. OAuth always requires HTTPS, including loopback servers.

Open **Settings → Connections**, select the server and choose **Connect** under **Authorization**. Harness UI discovers the authorization server and automatically registers this instance's client. Complete consent in the browser; use **Continue authorization** if the popup was blocked. No project-wide OAuth application or manually supplied client secret is needed. Successful consent saves authorization; **Load tools** separately checks the MCP connection.

Login uses the existing WebUI listener. Local access uses the listener's actual loopback port. For remote access, configure the exact public HTTPS origin in `webui.allowed_origins` and route `/oauth/mcp/callback` to the same listener. A wildcard does not authorize OAuth callbacks. The callback requires the browser that started login. Pending attempts expire after ten minutes and are lost on App restart; completed grants survive. Use **Cancel authorization** before restarting an unfinished attempt.

Tokens, registration and absolute expiry stay in this instance's private database. Runtime requests refresh expired access without opening a browser, including TUI use with the same configuration and data roots. An uncertain or rejected refresh requires **Reconnect**; ordinary tool calls are never automatically replayed after a 401. **Disconnect** clears local authorization. Changing the transport or reconnecting invalidates old captured account bindings; start a new Run afterward.

OAuth discovery, DCR and token HTTP use the Host network independently of Run `allowed_hosts`. MCP business requests keep their existing network policy. Providers requiring manual application registration or CIMD are unsupported.

## Choose tools

In **Settings → Connections**, open an MCP server and choose **Selected tools** to load its tool list. Use **Load tools** or **Refresh** to fetch the list explicitly. Search the list, select the tools you need, then **Save**. This applies to every Agent and chat using that server, starting with subsequent Runs. Loading or refreshing the list does not execute tools. Connection credentials remain hidden and are preserved when saving tool choices.

**All tools** includes future server additions. **Selected tools** keeps new tools disabled, even if you select every currently listed tool. Clearing the selection disables all tools while retaining the connection. Missing selected tools appear as unavailable; refresh errors preserve your selections.

For a single-resource file, add `tools` beside `transport`. Use exact tool names reported by the server:

```yaml title="mcp/docs.yaml — merge fragment"
tools: [search, fetch_content]
```

| `tools` value             | Access                                |
| ------------------------- | ------------------------------------- |
| Omitted or `null`         | All tools, including future additions |
| `[]`                      | No tools                              |
| `[search, fetch_content]` | Only those exact server-native names  |

For a `mcpServers` wrapper, put `tools` inside the selected server entry. The restriction applies to direct calls, tool-search calls, and MCP Apps. [Tool search](agents-and-subagents.md#tool-search-groups) can further reduce model context without enabling excluded tools.

## Command transport

1. Install and inspect the MCP server you intend to run. Obtain its executable, arguments, and required environment variables from that server's documentation.

2. Save a complete resource such as the following as `mcp/local.yaml`. Replace the executable and arguments with your installed server's values:

   ```yaml title="mcp/local.yaml — complete file; replace executable and arguments"
   schema_version: "2"
   kind: mcp_server
   id: mcp-local
   name: Local tools
   transport:
     command: /absolute/path/to/mcp-server
     arguments: []
     environment:
       API_TOKEN:
         env: LOCAL_MCP_TOKEN
   ```

3. Set any required variables in the Harness UI process environment. Remove `environment` if the server needs none.

4. [Enable `mcp-local`](#enable-a-server) and test one read-only tool.

Harness UI launches this program on the Host. Selecting a Sandbox Environment does not sandbox the external MCP process. Use an absolute executable path when the launch process may have a different `PATH`; command, argument, and URL strings are not environment-variable templates.

## Copy a JSON configuration

If your server documentation supplies `mcpServers`, add `schema_version: "2"` beside it and save the object as `mcp/servers.json`. This example is an **alternative** to `mcp/docs.yaml`, with the same `mcp-docs` ID:

```json title="mcp/servers.json — complete file; replace endpoint"
{
  "schema_version": "2",
  "mcpServers": {
    "docs": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${DOCS_MCP_TOKEN}"
      }
    }
  }
}
```

Set `DOCS_MCP_TOKEN`, then select `mcp-docs` as below. This example inserts only the token after `Bearer`; the earlier YAML example reads an entire header value.

A wrapper can contain several servers. Names normalize to `mcp-` IDs: `My_Server` becomes `mcp-my-server`. Duplicate IDs across files reject the configuration.

| Wrapper entry | Fields                                                                                                 |
| ------------- | ------------------------------------------------------------------------------------------------------ |
| Command       | `command`, optional `args`, optional `env`; optional `type: stdio`                                     |
| Remote        | `url`, optional `headers`, `oauth`, `init_timeout_seconds`; optional `type: http` or `streamable-http` |

Both `.yaml` and `.json` accept the wrapper or the single-resource format. Use strict JSON: comments and trailing commas are invalid. Remove unsupported client-specific fields such as `disabled`.

## Enable a server

1. Edit your Agent file, for example `agents/coder.yaml`. Merge the server ID into its list, preserving other selected IDs:

   ```yaml title="Agent file — merge fragment"
   mcp_servers: [mcp-docs]
   ```

   For the command recipe, use `mcp-local`. To provide a global creation default instead, merge this into the root YAML:

   ```yaml title="a13n-harness-ui.yaml — merge into defaults"
   defaults:
     mcp_servers: [mcp-docs]
   ```

2. Run `a13n-harness-ui config validate`. With a custom tree, put `--config /absolute/path/a13n-harness-ui.yaml` before `config`.

3. Start a new conversation with the Agent. Ask it to call a known read-only tool from the server with a small input. Inspect the tool result, rather than accepting a prose answer as connection proof.

Agent `mcp_servers: null` inherits root defaults; `[]` selects none. Project defaults can supply the new Thread's selection ahead of Agent and global defaults. Registering a server or retaining its connection does not itself select tools.

For an existing TUI conversation, `/resume <id>` or a restart with `--resume <id>` refreshes the MCP list from current Project → Agent → global defaults. This replaces the conversation-specific list, including when the current default is empty. WebUI and headless runs retain their saved lists until explicitly changed.

| Symptom                             | Check                                                                        |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| No MCP tools                        | Correct resource ID, Agent/Thread selection, and any exact `tools` allowlist |
| Executable missing                  | Server installation and the Harness UI process's executable path             |
| `runtime_credential_missing`        | Named variable is non-empty in the Harness UI process environment            |
| Configuration passes but calls fail | Actual endpoint, credentials, server readiness, and transport type           |
| `mcp_source_changed`                | Start a new Run from current configuration; see captured sources below       |

## Literal values and environment references

Command `environment` (wrapper `env`) and remote `headers` accept these forms:

| Value                   | Meaning                                        |
| ----------------------- | ---------------------------------------------- |
| `"us-east-1"`           | Literal string                                 |
| `"${API_TOKEN}"`        | Read the named process environment variable    |
| `"Bearer ${API_TOKEN}"` | Substitute the token into a string             |
| `{"env": "API_TOKEN"}`  | Explicit environment reference in YAML or JSON |

Expansion happens once at fresh Run construction. Referenced variables must be non-empty. Empty literal strings and whitespace are preserved. Only `${NAME}` with a valid variable name expands; there is no shell evaluation or default-value syntax. These substitutions apply only to environment/header values.

Set variables before launching Harness UI. Exporting them in a different shell does not change an already-running process. Keep files containing literal credentials private and outside version control.

Run captures store source locations and digests instead of literal environment/header values. A captured literal or template source must remain unchanged until its client is constructed; an older Run or child continuation otherwise reports `mcp_source_changed`. A new Run captures the current source. Environment references allow credential rotation without changing the file, provided the new value reaches the Host process.

## Connection lifetime and protocol

The default client lifetime is one logical Run. To preserve a server's process-local state across Runs, merge this fragment into the root YAML:

```yaml title="a13n-harness-ui.yaml — merge fragment"
mcp:
  host_owned_servers: [mcp-docs]
  protocol_overrides:
    mcp-docs: auto
```

Keep the ordinary Agent/Thread selection from the earlier steps. `host_owned_servers` defaults to `[]`; `protocol_overrides` defaults to `{}`. All listed IDs must refer to configured servers.

Each Thread, including a child Thread, gets its own retained client. Run completion and browser disconnection leave that client open. Host shutdown, explicit close, Thread disposal, or a changed binding closes it. Connections remain process-local; continuations do not serialize client state or pending server input. Explicit close ends the current connection without changing the Thread's selected servers. A later Run that still selects the server can establish a replacement connection, including after explicit close. Old Run projections and App Views stay bound to the closed connection. Remove the server from the applicable selection to prevent activation in future Runs. Calls are not automatically replayed after disconnection. Enabled [MCP Apps](mcp-apps.md) retain their clients automatically, and App activation can establish a replacement connection.

Connection initialization retries transient timeouts, transport interruptions and temporary HTTP failures up to three attempts, waiting one second and then two seconds. Each attempt uses the configured initialization timeout. A hanging server can therefore spend three initialization timeouts plus three seconds of backoff, in addition to SDK cleanup. Authentication and protocol errors do not retry. Startup failure follows the server's [startup failure policy](#startup-failure-policy).

Each protocol override accepts `auto`, `legacy`, or `2026-07-28`; omission uses `auto`. Use `auto` unless the server requires a specific mode. This setting controls Core protocol negotiation, separately from connection lifetime and the MCP Apps UI protocol.

Harness UI fetches each MCP server's tool list once per Run and reuses it for that Run's model steps. Tool changes that a server makes during a Run appear in the next Run.

## Startup failure policy

Every MCP server defaults to `on_error: skip`, for Bot, ordinary Threads and children alike. If connection initialization fails after any applicable retries, the current Run continues with the remaining capabilities. That server's tools stay unavailable for the rest of the Run, and the model receives an unavailability notice. Host logs identify the skipped server. A later Run tries again; Bot keeps its normal Heartbeat schedule.

To make a server required, add this beside `transport` in its resource file:

```yaml title="mcp/docs.yaml — merge fragment"
on_error: raise
```

For a `mcpServers` wrapper, put `on_error` inside the selected server entry, beside `url` or `command`. `skip` and `raise` are the only values. Omission uses `skip`, including older resources and captured recipes.

An Agent's explicit tool allowlist still limits callable tools. If a skipped server has selected tools, those names are treated as temporarily unavailable. If it allows all tools, unknown-name validation waits for a healthy Run because its inventory is unavailable.

`raise` makes startup failure fail the current Run. Cancellation, transport authorization, credential-source validation and captured OAuth account checks keep their ordinary error behavior. Once connected, tool-list and business-call errors are returned normally; neither policy replays tool calls.

## Human input from MCP servers

A server can pause an operation for a form or an external URL flow. The TUI and WebUI show the server and Thread, including requests from children.

1. Inspect the request and its source. For a URL request, explicitly open the destination outside the App iframe and finish that flow before confirming.
2. Choose accept, decline, or cancel within five minutes. Never enter passwords or tokens in MCP forms.
3. After answering, inspect the tool result. Accepted input means the answer was delivered, not that the remote operation completed.

Forms support primitive strings, numbers, booleans, single-select enums, and multi-select string enums, including titled options. The Host validates answers against the original schema and rejects unsupported schemas, remote references, and declared sensitive fields.

Cancellation, expiry, connection closure, or shutdown ends the request. If answer delivery is uncertain, inspect the request or resend the identical answer; a conflicting answer is rejected. Embedded applications must enable an [input handler](embedding.md#mcp-input-and-integration-control).

## MCP field reference

Use `schema_version: "2"` for MCP resources and wrappers. A single-resource file also requires `kind: mcp_server`, a unique `mcp-` `id`, a display `name`, and exactly one transport form:

| Transport | Required  | Optional                                       |
| --------- | --------- | ---------------------------------------------- |
| Command   | `command` | `arguments: []`, `environment: {}`             |
| Remote    | `url`     | `headers: {}`, `oauth`, `init_timeout_seconds` |

Environment and header values accept strings or `{env: VARIABLE}`. In a `mcpServers` wrapper, use `args` and `env` instead of `arguments` and `environment`. Optional `tools` follows the [tool selection rules](#choose-tools).

### Compatibility

Existing v1 MCP resources and unversioned `mcpServers` wrappers remain readable with all tools allowed. Set the MCP document's `schema_version` to `"2"` before adding a tool restriction. Older Harness UI versions cannot load v2; keep the version marker so they cannot silently ignore the restriction. Root, Agent, and Model schema versions are independent and remain unchanged.
