---
name: platform-operations
description: Configure workspace resources and diagnose sessions, threads, runs and attempts using the Service MCP tools.
---

# Platform operations

Use the selected platform MCP tools. Read their schemas for exact parameters; JSON bodies are nested under `request_body`. Results contain `status`, `headers`, and `body`. Resource identifiers must come from these results or the explicit `service_run_id` in the automatic Service context. Use that field as `run_id` for current Run queries; never substitute `harness_run_id`. The Host supplies identity and workspace. A tool or this Skill cannot grant additional permissions.

Use filtered `view=compact` reads for discovery, then select IDs, names and sections for detail. Existing queries default to `detailed` for compatibility. Compact pages default to ten records and may stop sooner to respect the response budget. Follow `next_cursor`, `next_after` or `next_before` with the same filters; an empty filtered page can still have a continuation. A preview's `truncated` and `omitted_sections` describe missing response data, not absent source data. `source_truncated` describes a limit already applied to stored content.

Text and JSON fragments count Unicode code points. Start at `offset=0`, then send the returned `version` and `next_offset`; changed content requires restarting. `representation=fragment` is explicitly partial. Reassemble all fragments before interpreting a full JSON definition or configuration. A section or compact summary cannot replace an entire configuration.

Typical tasks:

- Find a conversation by remembered message content: use `text_query` on Session, Thread or Run lists, then inspect matching Runs. Read [Execution and diagnostics](references/execution-and-diagnostics.md) for matching rules and coverage limits.
- Diagnose a failure: filter Runs, read a selected failure section, find matching items or spans, and read their necessary input or output.
- Choose a capability: search a model or tool index, select exact names, and read complete definitions or model characteristics and pricing.
- Edit configuration: read the Agent head and target revision, obtain the complete configuration, validate the change, and submit with the original ETag and required review.

Read only the references needed for the current task:

- [Agents](references/agents.md): read before answering whether an Agent can be changed or proposing configuration changes; covers editing restrictions, full configuration revisions and selected resources.
- [Models](references/models.md): model keys, filtered catalog price lookup, filling model configuration, and shared settings.
- [Providers](references/providers.md): private credential cards, provider configuration, connection tests and safe retries.
- [Connections](references/connections.md): tools, tests and authorization.
- [Skills](references/skills.md): upload/GitHub sources and exact revision references.
- [Environment templates](references/environment-templates.md): recipes and instance boundaries.
- [Execution and diagnostics](references/execution-and-diagnostics.md): sessions through attempts and traces.
- [Versions and conflicts](references/versions-and-conflicts.md): ETags, review and partial success.
- [Permissions and errors](references/permissions-and-errors.md): scope and safe failures.

Use `view`, `ls`, `glob`, and `grep` under this directory or `/platform-instance`. [AgentConfig](schemas/agent-config.json) is generated at build time from the shared request model. [Instance catalog](/platform-instance/catalog.json) is generated at process startup from the deployment's registered Providers and Toolsets. Catalog support does not mean that the workspace has configured or authorized a resource.

Keep questions focused on decisions that affect the result. Read current state before proposing a concrete change. When review is required, the request must show the exact target, parameters and original version. A changed target or configuration needs a new review. Report real result IDs and any unresolved errors.

Memory operations and plugin configuration are outside Composer's scope. Preserve unrelated existing fields. Use the `configure_provider` client tool for private provider credential entry when available. Browser authorization, connection credentials and file uploads use their existing Console pages. Never collect secrets in chat or tool arguments.
