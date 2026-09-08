# External tools

a13n Service selects external tools by managed ConnectorConnection or MCPConnection. Create the connection resource in the same Workspace before selecting it in an Agent configuration. Its ConnectorProvider may belong to that Workspace or its parent Organization. Organization Providers are automatically available to child Workspaces, while connections and authorized external accounts remain isolated by Workspace.

```json
{
  "connector_tools": [
    {
      "connector_connection_id": "cconn_1234567890abcdef",
      "tools": ["search"],
      "defer_loading": false
    }
  ],
  "mcp_tools": [
    {
      "mcp_connection_id": "mcpc_1234567890abcdef",
      "defer_loading": true
    }
  ]
}
```

Tool names in `tools` are exact source names. Omit `tools` or use `null` to allow all tools from that source. Use `[]` to allow none. A connection can appear only once in each list. Source aliases, inline URLs or credentials, and `exposure` are rejected.

`defer_loading` defaults to `false`, which makes selected definitions immediately visible. With `true`, the Harness exposes the group through its built-in `load_capability` tool. This changes model visibility; discovery and authorization still run during preparation.

A Run override inherits an omitted `connector_tools` or `mcp_tools` list. A supplied list replaces that entire category, and `[]` clears it. A null category is invalid. For example, `{"mcp_tools": []}` removes remote MCP selections while inheriting Connector selections.

## Connector Providers: Composio and OpenConnector

Create a Provider under `/api/v1/workspaces/{workspace_id}/connector-providers` or the parent Organization, supply its service credentials, discover its Connectors, and create a Workspace ConnectorConnection. Complete external authorization before selecting that connection in `connector_tools`. Both Providers support the same tool selection and deferred-loading fields shown above.

For Composio, choose `type: "composio"`, configure `enabled_toolkits`, and supply the write-only `api_key`. Configure the Service public origin and Composio's hosted OAuth identity verification callback.

For managed OpenConnector, choose `type: "openconnector"` and configure `enabled_services`, for example `["github", "gmail"]`. Supply `project_api_key` (OOMOL Project key) and `catalog_api_key` (OOMOL API key for catalog reads). Service uses the Project key for Workspace-isolated authorization and exact-account execution; third-party credentials stay with OOMOL. The returned authorization URL opens the external OAuth flow, and Control polls for completion. The Provider test response lists `verified_access`; OpenConnector's catalog test does not verify the Project key, which is checked during setup and account inspection.

Preview tool definitions before linking an account with `GET /api/v1/connector-providers/{provider_id}/connectors/{connector_key}/tools`. Preview does not grant execution access. A verified Connection keeps its own account binding, so clearing completed setup history does not break tool calls or re-enabling a disabled connection.

OOMOL's published Project API has no remote revoke operation. Service revoke/delete still disables the connection immediately and reports remote cleanup as failed; finish remote account removal in OOMOL. An initial setup interrupted by a lost response, cancellation, or process failure is not automatically retried because the Project API does not promise idempotent link creation. Concurrent retries return the same pending setup without a redirect until the active sender finishes; retry the same command afterward to resume its authorization URL.

## Application Accounts and event reception

An Application Account represents one provider account, Bot, or concrete application installation. Configure its credentials through `/api/v1/workspaces/{workspace_id}/application-accounts`. Credentials are write-only and encrypted on the Account.

Reception is disabled by default. To receive events, set `receive_enabled: true`, `default_agent_id`, and a same-Workspace `execution_service_account_id` on the Account. Configure the provider webhook at `/connectivity/v1/accounts/{account_id}/events`. A tool-only Account needs neither an Agent nor an execution Service Account.

Use `/api/v1/application-accounts/{account_id}/targets` to configure an exact Slack channel, Lark chat, or GitHub repository ID. Targets can override the Agent and only `model`, `skills`, `connector_tools`, and `mcp_tools`. Omitted categories inherit and empty lists clear. Optional batching and provider policy inherit Account defaults. Target updates replace the full configuration; disabled targets do not fall through to defaults.

The first event is submitted immediately; subsequent ordered batches obey the configured interval. An active or selected waiting Run receives Steer without changing its tools. The next ordinary Run uses the current Agent and override while retaining the same Thread. Closing reception stops new admission while already acknowledged batches continue processing. Disabling or deleting the Account also blocks pending execution and subsequent outbound calls.

Account tools are injected automatically from the Run's trusted execution context. There is no Account selection in Agent configuration or Run overrides. Inbound admission supplies only its admitted reply actions and target. A trusted independent entry can supply explicit proactive actions and destinations. With no such context, the Run gets no Account tools; creating an Account alone does not expose it to every Agent. Clearing `connector_tools` or `mcp_tools` does not remove these default tools.

For proactive sends, Slack accepts `channel_id` and `text` within the entry-authorized `channel_ids`; Lark accepts `chat_id` and typed `content` within `chat_ids`. GitHub scope contains exact repositories, and its tools accept a permitted repository ID, issue/PR number, and target kind. Tools cannot choose another Account or credential. Proactive sends do not automatically create an external Thread binding.

## Discovery and execution

Acceptance validates managed resources and permissions without contacting external servers. The executing Worker discovers the selected sources. A missing explicitly selected tool fails preparation. Tool schemas can change between Attempts; acceptance does not retain schema snapshots. Use explicit names to limit which tools a source can contribute.

A subagent uses its own selected Connector and MCP tools. Parent Run acceptance freezes the complete child graph. With `subagent_mode: "inline"`, each child's tools live in the parent Attempt; with `subagent_mode: "async"`, its independently scheduled Run prepares them. Children receive no parent native ingress context. Attempt cleanup closes all prepared tool sessions, including when preparation only partly succeeds.

Every connection receives a separate capability and stable, system-generated tool names. Names are normalized and shortened for model APIs; the source-native name remains the value used in `tools` selections and provider calls. Two accounts at the same remote URL remain independent. Connector tools call source-bound local Toolsets through the Harness MCP capability. Remote MCP uses the upstream client directly from the Worker. Credentials are resolved from current managed state before outbound requests, and each dispatch checks the current Attempt and resource authority.

Inbound Slack, Lark, and GitHub reply actions use the protected target admitted with the input event. The model supplies action content, not a destination, connection, or credential. Receive-only input contributes no native tools. Replies preserve the admitted conversation binding. An uncertain write remains an unknown outcome and is not automatically repeated.

Discovery is bounded per source to 128 pages, 2,048 tools, and 16 MiB of tool definitions. Each result is bounded to 1 MiB. Invalid schemas, external schema references, excessive nesting, and oversized definitions or results fail explicitly.

## Host integration

Worker composition provides `WorkerRuntime.external_tools`. A host constructing `HarnessDriver` passes this collaborator as `external_tools`; the driver creates and closes fresh capabilities around each Attempt's Harness execution. Admission integrations use `connectivity.native_context.InboundRunContext.from_batch()` and retain its JSON as one entry in the accepted Run's `native_tool_contexts`. For proactive operations, a trusted entry calls `bind_account_tools()` inside its short acceptance transaction with the execution actor, Workspace, exact Account, allowed actions, and authorized target scope, then persists the returned context in that tuple. The entry owns target authorization; the helper checks Account use and scope validity. Neither context comes directly from public input or a model argument.

Replacement Attempts and inherited continuations retain these contexts and resolve fresh credentials. New child Runs receive no parent native contexts by default. The Harness sees ordinary MCP capabilities and needs no Account-specific configuration.

Control owns Account and connection management, setup, and short-lived OAuth state expiration. OAuth refresh is demand-driven before authenticated use; interrupted exchanges require new authorization. Connection deletion first invalidates locally, then makes one bounded remote cleanup attempt. Failed or unknown cleanup is reported honestly and has no background retry. Connectivity owns inbound delivery and admission. Workers own tool discovery and outbound execution. No local MCP listener or separate MCP service is needed. `A13N_SERVICE_CONNECTIVITY_PUBLIC_ORIGIN` is required for interactive callback flows, not for noninteractive management or OpenConnector polling. Remote MCP must negotiate protocol `2025-11-25`; authenticated MCP endpoint redirects are rejected.

## Development database setup

The initial migrations create the current schema directly, without persisted tool catalogs or Run tool snapshots. If a local database was created from the previous schema, recreate it and reauthor Agent configurations with connection-selection lists. Existing revision stamps cannot update rewritten initial migrations. Skill, Plugin, and Environment locks keep their existing contracts.

## Retrying management commands

Use an `Idempotency-Key` containing 1–512 visible ASCII bytes for retryable management commands. If a response is lost, repeat the same key and request. For 24 hours from the original successful commit, an authorized replay returns the original accepted result, even if the resource has since advanced. Changing the request while reusing that key returns a conflict. Replay does not renew the window.

Read an MCP connection after a mutation to observe current discovery status; the mutation receipt records its accepted state. Supply credentials through the owning Account, Provider, or MCP connection. Run configuration selects those managed resources and does not accept direct credential overrides.
