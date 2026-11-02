---
name: platform-operations
description: Configure workspace resources, manage one-time and recurring schedules, and diagnose executions using the Service MCP tools.
---

# Platform operations

Use the selected internal platform tools and their current schemas. Inputs name business fields directly; results expose resources, versions, states, pagination and structured errors. The Host supplies identity and workspace. These tools are independent of the external management MCP interface, and neither a tool nor this Skill grants additional permissions. Resource identifiers must come from authorized results or the explicit `service_run_id` in automatic Service context. Never substitute `harness_run_id` for a Service Run ID.

Use filtered lists and bounded summaries to discover resources. Follow cursors with unchanged filters; an empty filtered page can still have a continuation. Preview truncation and omitted sections describe missing response data, not absent source data. Execution detail readers return a `summary` with a `content` map of opaque references. Pass a selected reference unchanged to `read_content` or `search_content`; follow its returned `continuation`. References bind source, workspace, Run and content version and never grant access. A content change requires a new parent read. Do not construct field/ordinal/offset arguments or decode a reference.

Resource definition readers that expose `offset` and `version` retain their declared fragment contract. Reassemble all fragments before treating a definition as a complete invocation schema. Configuration editing uses typed deltas; a summary is never a replacement configuration.

In user-facing answers, prefer readable user, organization, workspace and resource names over bare IDs. Use names from available context or authorized tool results; never invent a name or use it as authorization. For message senders, use `get_message_authors` as described in [Execution and diagnostics](references/execution-and-diagnostics.md). Include an email when relevant to the request or needed to distinguish people with the same name. Keep IDs for precise references, troubleshooting and tool arguments. If a name cannot be resolved with the available tools, show its ID and state the limitation.

Typical tasks:

- Find a conversation by remembered message content: use `text_query` on Session, Thread or Run lists, then inspect matching Runs. Read [Execution and diagnostics](references/execution-and-diagnostics.md) for matching rules and coverage limits.
- Identify who sent a message: obtain its inbox entry ID and call `get_message_authors` for that Session to read the sender's name, email and other profile fields.
- Diagnose a failure: filter Runs, read a selected failure section, find matching items or spans, and read their necessary input or output.
- Choose a capability: search a model or tool index, select exact names, and read complete definitions or model characteristics and pricing.
- Edit configuration: read the resource version, inspect the fields needed for the decision, and submit only intended changes with that original version and required review.

Read only the references needed for the current task:

- [Agents](references/agents.md): read before answering whether an Agent can be changed or proposing configuration changes; covers editing restrictions, atomic configuration deltas, revisions and selected resources.
- [Models](references/models.md): model keys, filtered catalog price lookup, filling model configuration, and shared settings.
- [Providers](references/providers.md): private credential cards, provider configuration, connection tests and safe retries.
- [Connections](references/connections.md): tools, tests and authorization.
- [Skills](references/skills.md): upload/GitHub sources and exact revision references.
- [Environment templates](references/environment-templates.md): recipes and instance boundaries.
- [Schedules](references/schedules.md): read before creating, changing or dispatching scheduled work; covers time preview, durable delegation, approval and trigger results.
- [Execution and diagnostics](references/execution-and-diagnostics.md): sessions through attempts, retained tool executions and traces.
- [Versions and conflicts](references/versions-and-conflicts.md): observed versions, review and partial success.
- [Permissions and errors](references/permissions-and-errors.md): scope and safe failures.

Use `view`, `ls`, `glob`, and `grep` under this directory or `/platform-instance`. [AgentConfig](schemas/agent-config.json) is generated at build time from the shared request model. Use the enabled tools' discovered schemas for exact fields and required versions. Release bundles also include `schemas/platform-tools.json` for offline inspection, generated from the same explicit definitions during asset preparation. [Instance catalog](/platform-instance/catalog.json) is generated at process startup from the deployment's registered Providers and Toolsets. Catalog support does not mean that the workspace has configured or authorized a resource.

Keep questions focused on decisions that affect the result. Read current state before proposing a concrete change. When review is required, the request must show the exact target, parameters and original version. A changed target or configuration needs a new review. Report real result IDs and any unresolved errors.

Memory operations and plugin configuration are outside Composer's scope. Preserve unrelated existing fields. Use the `configure_provider` client tool for private provider credential entry when available. Browser authorization, connection credentials and file uploads use their existing Console pages. Never collect secrets in chat or tool arguments.
