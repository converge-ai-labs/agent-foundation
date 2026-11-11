---
title: Connectors
description: Connect accounts and Remote MCP tools once for WebUI conversations and Bot.
---

Connectors add instance-wide accounts and tools to WebUI conversations and [Bot](bot.md). Each connection has its own configuration, private credentials and status. You can connect several accounts of the same type. Terminal conversations, Live and child agents do not automatically receive Connector tools.

## Connect GitHub

1. Open **Settings → Connections → Connectors**.
2. Choose **Add connection** beside GitHub.
3. Open **Create a personal access token** if you need a token. Choose repository and organization permissions for the work you intend to authorize.
4. Enter the token and select **Save connection**.
5. Start a new deliberate chat Run to use the connection. An existing Run keeps its captured tools.

GitHub uses its official Remote MCP endpoint, `https://api.githubcopilot.com/mcp/`. Token type, organization approval and GitHub permissions determine which upstream tools work. Saving stores configuration locally; it does not contact GitHub or verify permissions. Reopen setup and choose **Test saved connection** for an explicit connectivity check.

A blank token field retains the saved token. **Clear the saved token** removes it. Credentials are plaintext in this instance's private database, protected by Host filesystem access. Never paste a token into chat, Agent instructions or a tool call.

The GitHub Connector provides MCP tools. It does not poll notifications, create automatic Bot inputs or implement a separate GitHub OAuth application.

## Connect another Remote MCP server

Add a **Remote MCP** connection and enter its Streamable HTTP endpoint. HTTPS is required outside header-free local loopback development. Add private authentication headers as repeatable rows. Leave a header value blank to keep its saved value; remove the row to clear that header.

Leave **Tools** unset to allow all upstream tools. Enable the selection with no entries to disable every tool. Add names to allow only those native upstream tools. The initialization timeout controls MCP connection setup.

Remote MCP uses the same native MCP client and transport rules as [file-defined MCP servers](mcp.md). Connector accounts are instance-wide and managed in WebUI. File-defined servers remain Agent-selected resources usable through the TUI as well.

## Configure from a conversation

An agent can offer a connection setup card. The offer accepts no credentials and creates no account or setup session by itself. Open the card, choose an existing account or deliberately create a new connection, then use the same setup form as Settings.

Ordinary chat cards remain visible outside collapsed work, including offers made through CodeAct. Bot publishes an intentional setup card in its normal conversation rather than exposing its internal tool stream. Shared guests can view an intentionally published card but cannot configure the instance.

Setup success updates saved configuration. It starts no Run and replays no blocked operation. Send a new deliberate message when you want the agent to use the connection.

## Change, disable or delete

**Manage** opens connection setup and common settings. Names distinguish accounts. Disabling stops new operations and holds pending external input; it does not undo upstream writes. Deleting clears the connection's private credentials, state and pending input. Deleted IDs cannot be reused.

If setup or another browser changes saved settings, your open settings draft remains intact. Choose **Reload saved settings** before saving. Private setup fields clear when dispatched. After an unknown action response, use **Refresh status** before taking another action. The client never automatically replays that action. Setup sessions expire and do not survive App restart; saved configuration does.

Only one WebUI App owns the data root's Bot and Connector runtime. Another App can inspect saved status but cannot operate accounts. A missing plugin preserves its connection data as unavailable.

## Event rules and Bot delivery

A plugin may declare events, conditions and optional **Deliver external input to Bot**. Settings renders the plugin's conditions as structured fields. The plugin decides what matches; the Host does not interpret a universal rule language.

Collection and matching are independent of Bot's activation and pause state. Accepted input uses Bot's shared bounded inbox. Bot off, pause, missing Model and pending human decisions hold delivery. Rule edits preserve already-retained matches; disabling holds them and deletion clears them. GitHub and generic Remote MCP declare no event source.

## Develop a trusted Connector

Import the public SDK from `a13n_harness_ui.connectors`. A `Definition` supplies a safe declaration, Pydantic configuration/condition validators and an async context that yields a `Controller`. The Controller supplies status, optional setup, native Capability/capture, services and a configuration-generation producer. Register explicit definitions through `HarnessUiIntegrations(connectors=(definition,))`, or publish a no-argument factory under the `a13n_harness_ui.connectors` Python entry-point group.

```toml
[project.entry-points."a13n_harness_ui.connectors"]
example = "example_connector:Example"
```

Use the bound Context for private credentials, scoped state, atomic state/input commits and typed service lookup. External I/O happens before the short commit. Include a codec version in your own state document. When a selected event is retained, commit its input and cursor together; a rejected commit must not advance the cursor. Respect the captured configuration version and configured Bot-delivery policy.

The form profile supports objects, strings/passwords, booleans, numbers, enums, nullable fields, arrays/rows and acyclic local references. Use `writeOnly` on secret setup fields. Never return secrets in status, context, captures, setup values, logs or model-visible input. Unsupported schemas are unavailable rather than rendered as raw JSON.

Controllers own shared resources until admitted root users drain. Native tools remain per-Run Capabilities. A background producer is restarted on configuration changes. Setup actions are explicit and serialized; reads observe the current Step, and an optional flow worker advances external waits. Close optional flow resources after cancellation. Plugins are trusted Host Python code, not sandboxed scripts.

Remote MCP exposes a typed programmatic service without exporting credentials:

```python
from a13n_harness_ui.connectors.remote_mcp import RemoteMcpService

service = context.service(
    "connector-account-id", "mcp", api_version=1, expected_type=RemoteMcpService
)
tools = await service.list_tools()
```

The provider checks enabled state, current configuration and tool selection internally. Agent-facing MCP tools continue through the native transport and ToolSearch path. See [the runnable Connector example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/harness-ui-connectors) for declarations, setup, plugin-owned rule matching and atomic ingress.
