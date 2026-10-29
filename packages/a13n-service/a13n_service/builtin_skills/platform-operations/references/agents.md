# Agents

## Check whether the Agent can be changed

Read the Agent head and check `source` and `archived_at` before promising a change, asking for change details or requesting write approval.

- `source=builtin` with `preset_kind=composer` or `finding`: users may change the model, `thinking`, `max_tokens`, `temperature` and `top_p` model settings, and supplemental instructions through ordinary Revisions. They may select an existing default Revision. Official instructions, tools, permissions and other configuration fields remain protected. Name, description, labels, avatar and archive/unarchive operations remain forbidden (`409 builtin`). For changes outside these fields, offer to duplicate it into a custom Agent.
- Other builtin presets, including Memory Organizer, remain read-only. Approval and a correct ETag do not remove their restrictions.
- `source=custom` with `archived_at` set: restore it through the unarchive operation before editing or duplicating it, subject to the user's request and write review.
- An unarchived custom Agent can be edited within the current user's authority and tool review requirements.

Preparing an existing Composer or Finding preserves its selected model and default Revision. Changed protected configuration appears as candidate Revisions without selecting them. Official instructions come from the current Worker capability and do not require a new Revision. A disabled selected model requires an explicit model change; preparation never silently chooses a replacement.

## Change an editable Agent

Agent metadata (name, description, labels) belongs to the Agent head. Configuration belongs to immutable Revisions. Retain the head's ETag, then read the explicitly selected Revision or the current default Revision. Create a Revision with the entire AgentConfig, preserving all unrelated fields. Use the API validation operation before a complex write; validation stores nothing and does not prove that a later run will succeed.

`make_default=true` publishes the new default; use false for a candidate. Selecting an older base Revision is valid, but identify that base to the user and still use the head ETag you originally read. Setting a default does not edit the Revision. Metadata updates do not edit instructions or model settings. Labels are a replacement value.

For Composer or Finding, read the selected default Revision and keep every protected config field unchanged. Send the edited model/settings and complete desired supplement in ordinary `config.instructions` to both validation and creation; validation also needs `agent_id`. Preserve existing `config.instructions` unless asked to replace it. Never copy `builtin_instructions` into the supplement or submit the response-only instruction fields. Official text comes from the Worker on each execution attempt, including recovery and waiting continuation. Clearing the supplement sends an empty string. Re-read after conflicts instead of replacing the original ETag automatically. Duplicate/export materializes current official text and the supplement into an independent custom configuration.

Models use keys. Skills and subagents use IDs and exact Revision IDs after validation. Connection tools select existing Connections. Platform tools are toolsets named by group (`agents`, `models`, `connections`, `skills`, `environment_templates`, `providers`, `execution`, `schedules`), off by default; each tool is one operation keyed by its MCP tool name, with its own `enabled` and `permission`. `inherit` allows reads and asks before writes. `builtin_skills=["platform-operations"]` independently enables these instructions. Custom Agents, including copies of builtin Agents, need a primary Environment and synchronize these files with their `skill_sync` policy. Only server-owned builtin Agents keep Host-mounted instructions. A configuration change affects later runs, never the current run's frozen configuration or delegated authority.

`memory` sets new-session defaults: `enabled` and `spaces[{name, space_id, access}]`, selecting existing workspace Memory Spaces with read or write access. The first accepted business run fills omitted session fields from its Agent Revision; the session then keeps those choices. Each Space owns its organization policy and model. `memory_mounts` selects standalone memories for new threads.

## Configure context compaction

`summarize` defaults to false. Enable it to offer the explicit `summarize` tool, which lets the Agent write a continuation summary and resume from fresh context. Each child uses its own Revision's setting. `model_characteristics.proactive_context_management_threshold` controls the reminder (default 0.65; null disables reminders while keeping the tool). Automatic compaction is independent. An enabled tool reserves `summarize`, so a client tool cannot use that name.

`background_compaction` belongs to the Agent Revision and defaults to `{enabled: false, blocking_threshold: 0.95}`. Enable it for background summaries on ordinary threads. Inline and asynchronous children compact synchronously. The blocking threshold accepts values greater than zero and at most one; counted input at or above this share of the context window waits for existing work or compacts synchronously if none exists. Output reserve can require blocking earlier. The common compaction trigger remains independent. Disabling generation preserves delivery of retained results.

## Configure tool discovery

`tool_search` groups tools the Agent already selects. Each group names a `description`, `connections` by Connection ID, `plugins` by instance name, local `capabilities` (`environment`, `web`), and optionally `platform: true`. Each source belongs to one group. Omit `config.mode` or set it to `proxy`; control names and discovery limits are configurable. The model uses fixed discovery, description, invocation, tool-list, and group-list controls. It can enumerate the complete directory with `list_tools(detail="full")` and `next_offset` without promoting member schemas into model requests.

Ungrouped sources remain direct. Grouping preserves source selection, permissions, credentials, and execution checks; it does not activate disabled capabilities. `client_tools` keep their flat external-call contract. A Run override replaces the entire `tool_search` block; `groups: {}` disables grouping. Each child Agent uses its own Revision's plan.

Connection selections configure execution through `connection_id`, `tools`, `permission`, and `permissions`. Unknown fields are ignored.

## Configure desktop coordinates

The Computer toolset requires explicit enablement and an authorized desktop Environment. `toolsets.computer.config.coordinate_space` defaults to `pixels`. Select `normalized_1000` only when the chosen model supplies relative positions on a 0–1000 scale for each axis. The tool converts pointer positions using the referenced screenshot; model instructions must not ask the model to multiply by image dimensions. Scroll distances retain their declared units. Do not infer or change this setting from the Environment Provider or template. Check the deployed toolset catalog for supported configuration before creating a Revision.

## Execution policy

`execution.environment` sets ordinary command variables, not secrets. Thread and Run maps override per key; an empty string is an explicit empty value. Thread PATCH replaces the whole supplied map, with null/empty clearing that layer. Accepted Runs freeze the overlay and inject it at command dispatch above recipe defaults.

`execution` also sets sparse control defaults for new Runs: `tools.mode` is `inline` or `auto`, `tools.foreground_wait_seconds` is 0–300, and `steering` is `boundary` or `immediate`. Omitted/null leaves inherit the deployment defaults (`auto`, 5 seconds unless configured otherwise, `immediate`). Thread and single-Run choices override each leaf independently. Publishing a Revision affects future ordinary Runs; accepted Runs and their automatic continuations keep their frozen policy. Runtime capacity, result retention and shutdown limits remain deployment settings.

## Read only the needed configuration

Revision lists support `view=compact`; selected revision reads accept `section=all` or a named configuration field. Fragment reads preserve the revision's content version. `/agents/{agent_id}/revisions/{revision_id}/compare` compares against `base_revision_id`, optionally for one section. Before publishing, obtain and preserve the complete base config; a diff or section alone is insufficient.

Search `/toolsets/index` by `query` and follow its cursor. Read `/toolsets/definitions` with repeated `name` values (up to five). Index descriptions omit schemas. Definitions report `missing` names and `oversized` references explicitly; use the same definition endpoint with one name and `offset=0` to read an oversized definition in fragments. Supported tool counts reflect deployment registration, not workspace authorization.
