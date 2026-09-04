# External tools

Foundation Service selects external tools by managed Application Account, ConnectorConnection, or MCPConnection. Create the resource in the same Workspace before selecting it in an Agent configuration.

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

## Application Accounts and event reception

An Application Account represents one provider account, Bot, or concrete application installation. Configure its credentials through `/api/v1/workspaces/{workspace_id}/application-accounts`. Credentials are write-only and encrypted on the Account.

Create an optional Ingress using `account_id`, `provider_config: {"events_transport": "http"}`, its execution Service Account, allowed Agents, and default Agent. Each account has at most one Ingress. Routes beneath that Ingress select how events reach Agents. Pausing Ingress stops new input; already accepted Runs can still reply within their admitted scope. Disabling the Account blocks both new input and subsequent outbound calls.

For proactive operations, select the Account independently:

```json
{
  "account_tools": [
    {
      "account_id": "acct_1234567890abcdef",
      "tools": ["slack.send_message"],
      "target_scope": {"channel_ids": ["C123"]},
      "defer_loading": false
    }
  ]
}
```

The Slack tool accepts `channel_id` and `text`; only configured channel IDs are allowed. Lark uses `lark.send_message`, a scope of `chat_ids`, and `chat_id` plus typed `content`. GitHub scopes contain `repositories`, each with `repository_id`, `owner`, and `repository`; selected comment/read tools accept a permitted repository ID, issue/PR number, and target kind. Tools cannot choose another account or credential. Empty tool or target lists grant no operations or destinations respectively.

`account_tools` follows the same category replacement rules as other selections, but requires an explicit tool list and target scope. It does not inherit a destination from arbitrary input text. Inbound reply tools remain bound to the admitted conversation, and proactive sends do not automatically create an Ingress Thread binding.

## Discovery and execution

Acceptance validates managed resources and permissions without contacting external servers. The executing Worker discovers the selected sources. A missing explicitly selected tool fails preparation. Tool schemas can change between Attempts; acceptance does not retain schema snapshots. Use explicit names to limit which tools a source can contribute.

Every connection receives a separate capability and stable, system-generated tool names. Names are normalized and shortened for model APIs; the source-native name remains the value used in `tools` selections and provider calls. Two accounts at the same remote URL remain independent. Connector tools use a local in-process MCP server. Remote MCP uses the upstream client directly from the Worker. Credentials are resolved from current managed state before outbound requests, and each dispatch checks the current Attempt and resource authority.

Inbound Slack, Lark, and GitHub reply actions use the protected target admitted with the input event. The model supplies action content, not a destination, connection, or credential. Receive-only input contributes no native tools. Replies preserve the admitted conversation binding. An uncertain write remains an unknown outcome and is not automatically repeated.

Discovery is bounded per source to 128 pages, 2,048 tools, and 16 MiB of tool definitions. Each result is bounded to 1 MiB. Invalid schemas, external schema references, excessive nesting, and oversized definitions or results fail explicitly.

## Host integration

Worker composition provides `WorkerRuntime.external_tools`. A host constructing `HarnessDriver` passes this collaborator as `external_tools`; the driver creates and closes fresh capabilities around each Attempt's Harness execution. Admission integrations construct protected metadata with `IngressRunContext.from_batch()` and retain its JSON in the accepted Run's `ingress_context`. This context must come from trusted admission, never a public Run override.

Control owns Account and connection management, setup, OAuth refresh, and cleanup. Connectivity owns inbound delivery and admission. Workers own tool discovery and outbound execution. No local MCP listener or separate MCP service is needed.

## Development database setup

The initial migrations create the current schema directly, without persisted tool catalogs or Run tool snapshots. If a local database was created from the previous schema, recreate it and reauthor Agent configurations with connection-selection lists. Existing revision stamps cannot update rewritten initial migrations. Skill, Plugin, and Environment locks keep their existing contracts.
