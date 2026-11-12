---
title: Connectors
description: Connect service accounts once for WebUI conversations and Bot.
---

Connectors add instance-wide accounts and tools to WebUI conversations and [Bot](bot.md). Each connection has its own configuration, private credentials and status. You can connect several accounts of the same type. Terminal conversations, Live and child agents do not automatically receive Connector tools.

## Connect GitHub

1. Open **Settings → Connections → Connectors**.
2. Choose **Add connection → GitHub**, name the account, then select **Continue**. Cancelling before Continue creates no account.
3. Open **Create a personal access token** if you need a token. Choose repository and organization permissions for the work you intend to authorize.
4. Enter the token and select **Save connection**. Saving completes setup and closes the dialog.
5. Send a new deliberate message to use the connection. An existing Run keeps its captured tools.

Connections lists your saved accounts first, with separate readiness states. If you leave a new account's setup incomplete, choose **Keep for later** or **Remove connection**. A kept account offers **Continue setup**.

Token type, organization approval and GitHub permissions determine which GitHub operations work. Saving stores configuration locally; it does not contact GitHub or verify permissions. Choose **Test connection** in the account menu for an explicit connectivity check. GitHub's connection details are built into the provider; setup only asks for your token.

The first save requires a non-blank token. Leading and trailing spaces are removed; control characters are rejected. A blank token field retains an existing saved token. The **Saved** hint confirms a stored token; **Remove saved token** removes it. A replacement token and removal cannot be submitted together. Credentials are plaintext in this instance's private database, protected by Host filesystem access. Never paste a token into chat, Agent instructions or a tool call.

The GitHub Connector provides MCP tools. It does not poll notifications, create automatic Bot inputs or implement a separate GitHub OAuth application.

## Connectors and MCP servers

Connectors present concrete services such as GitHub, with account setup defined by that service's provider. The Add connection chooser lists installed service integrations.

Configure an arbitrary MCP server through the separate [MCP servers](mcp.md) settings. These remain Agent-selected resources usable through the TUI as well. Remote MCP support in the Connector SDK is a code library for developers implementing concrete service integrations, not another generic MCP setup screen.

## Agent tool access

Connector tools are discovered through `list_connectors`, `list_connector_tools`, `discover_connector_tools` and `describe_connector_tool`, then executed with `call_connector_tool`. Each account uses its saved **Group name**, such as `github` or `work-github`; the description names the account. Creation suggests a normalized name, which you can edit before continuing or later in **Manage → Account settings**. Group names must be unique across connections and use at most 32 lowercase letters, digits and single hyphens, starting with a letter. Changing the display name leaves the saved group unchanged. Changing the group requires a new Run to use its tools.

On upgrade, existing accounts receive readable group names automatically, with numeric suffixes when needed. Account IDs, credentials and saved configuration remain intact. Captures from before explicit group names were introduced are unsupported; start a new Run.

With CodeAct enabled, an agent can await those same controls inside `run_code` or `run_program`. GitHub and other providers built on the Remote MCP library support this path. Upstream methods stay behind `call_connector_tool`; they do not become separate Python functions. Other plugins declare their own CodeAct policy, reported as `codeact_eligible` in discovery results. Tool permissions and captured account checks still apply. Calls requiring an approval that cannot resolve inline should use the ordinary tool-call path; CodeAct does not save a suspended Python execution point or automatically replay earlier calls.

## Configure from a conversation

An agent can offer a connection setup card. The offer accepts no credentials and creates no account or setup session by itself. Choose an existing account or **Add another account**, then open the same setup dialog as Settings. Forms never expand inside the transcript. A ready account resolves the card to a compact confirmation with a **Manage** link.

Ordinary chat cards remain visible outside collapsed work, including offers made through CodeAct. Bot publishes an intentional setup card in its normal conversation rather than exposing its internal tool stream. Shared guests can view an intentionally published card but cannot configure the instance.

Setup success updates saved configuration. It starts no Run and replays no blocked operation. Send a new deliberate message when you want the agent to use the connection.

## Change, disable or delete

The account menu's **Manage** opens a read-only connection summary, saved-credential hints and common account settings. **Edit connection** opens the separate setup dialog; **Set up again** is also available in the account menu. Names distinguish accounts. Disabling stops new operations and holds pending external input; it does not undo upstream writes. Deleting clears the connection's private credentials, state and pending input. Deleted IDs cannot be reused.

If setup or another browser changes saved settings, an untouched settings dialog updates automatically. An edited draft remains intact and shows a conflict; choose **Reload saved settings** before saving. Closing a dirty dialog asks before discarding unsaved changes. GitHub setup reopens with current saved settings after an account change. An already-open stale setup rejects actions; choose **Refresh status**, then **Start again** to reload it. Private setup fields clear when dispatched. After an unknown action response, use **Refresh status** before taking another action. The client never automatically replays that action. Setup sessions expire and do not survive App restart; saved configuration does.

Only one WebUI App owns the data root's Bot and Connector runtime. Another App can inspect saved status but cannot operate accounts. A missing plugin preserves its connection data as unavailable.

## Event rules and Bot delivery

A plugin may declare events, conditions and optional **Deliver external input to Bot**. Settings renders the plugin's conditions as structured fields. The plugin decides what matches; the Host does not interpret a universal rule language.

Collection and matching are independent of Bot's activation and pause state. Accepted input uses Bot's shared bounded inbox. Bot off, pause, missing Model and pending human decisions hold delivery. Rule edits preserve already-retained matches; disabling holds them and deletion clears them. The GitHub provider declares no event source.

## Develop a trusted Connector

Import the public SDK from `a13n_harness_ui.connectors`. A `Definition` supplies a safe declaration, Pydantic configuration/condition validators and an async context that yields a `Controller`. The Controller supplies status, optional setup, native Capability/capture, services and a configuration-generation producer. Register explicit definitions through `HarnessUiIntegrations(connectors=(definition,))`, or publish a no-argument factory under the `a13n_harness_ui.connectors` Python entry-point group.

```toml
[project.entry-points."a13n_harness_ui.connectors"]
example = "example_connector:Example"
```

Use the bound Context for private credentials, scoped state, atomic state/input commits and typed service lookup. External I/O happens before the short commit. Include a codec version in your own state document. When a selected event is retained, commit its input and cursor together; a rejected commit must not advance the cursor. Respect the captured configuration version and configured Bot-delivery policy.

The form profile supports objects, strings/passwords, booleans, numbers, enums, nullable fields, arrays/rows and acyclic local references. Use `writeOnly` on secret setup fields. A root `readOnly: true` configuration schema keeps the account summary visible while delegating edits to setup. Optional form hints are `x-a13n-saved` (root array of JSON Pointer paths identifying stored secret fields), `x-a13n-null-label`, `x-a13n-selected-label`, `x-a13n-empty-label` and `x-a13n-add-label` (field-level display strings). These hints carry presence and copy only, never credential values. Never return secrets in status, context, captures, setup values, logs or model-visible input. Unsupported schemas are unavailable rather than rendered as raw JSON.

Controllers own shared resources until admitted root users drain. Native tools remain per-Run Capabilities. A background producer is restarted on configuration changes. Setup actions are explicit and serialized; reads observe the current Step, and an optional flow worker advances external waits. Close optional flow resources after cancellation. Plugins are trusted Host Python code, not sandboxed scripts.

### Presentation conventions

All Connectors use the same account list and setup renderer. In `Declaration`, optional `brand="github"` selects a shared brand mark; optional `logo_url="https://example.com/logo.png"` supplies a public image for an unregistered service. No frontend provider registration is required. Use a stable credential-free URL: the browser loads it without a referrer. Missing, unsupported or failed images fall back to a generic mark. Keep a short service name and one concise description.

`SetupAction(icon="save", ...)` declares a semantic hint. Supported tokens are `edit`, `test`, `save`, `continue`, `done`, `refresh`, `open_url` and `copy`. The Host chooses the glyph and layout. Secondary `edit` and `test` submit actions become icon buttons with tooltips and accessible labels; primary and confirmation actions keep their text. Unknown or omitted hints keep text, and URL/copy actions default to their kind's glyph. Never derive behavior from an icon, put secrets in logo URLs, or depend on action IDs to select presentation. Keep permissions, saved-versus-tested results and recovery instructions visible.

### Implement a service using Remote MCP

Subclass `a13n_harness_ui.connectors.remote_mcp.RemoteMcp` to reuse native tools and typed programmatic access. This is a Controller base, not a registered Definition. Your Definition owns the concrete service's name, configuration model and setup. Its `open()` yields your Controller. Implement `connection(configuration)` to return a safe `ResolvedMcpRecipe`, or `None` while the account is incomplete. Fix the service endpoint and transport policy in code, and use `McpPrivateValueSource` for private headers. Never put credential values in the recipe.

The library supplies native MCP capture, Capability creation, credential resolution and generation/enabled-state checks. A provider can select native tools in its recipe (`None` permits all; an empty tuple permits none) without exposing MCP transport controls in the frontend. See the [GitHub provider](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-harness-ui/a13n_harness_ui/connectors/github.py) for a concrete implementation.

The Controller also publishes version 1 of a typed `mcp` service without exporting credentials:

```python
from a13n_harness_ui.connectors.remote_mcp import RemoteMcpService

service = context.service(
    "connector-account-id", "mcp", api_version=1, expected_type=RemoteMcpService
)
tools = await service.list_tools()
```

The provider checks enabled state, current configuration and tool selection internally. Agent-facing MCP tools continue through the native transport and ToolSearch path. See [the runnable Connector example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/harness-ui-connectors) for declarations, setup, plugin-owned rule matching and atomic ingress.
