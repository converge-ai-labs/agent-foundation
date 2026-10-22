# Agents

## Check whether the Agent can be changed

Read the Agent head and check `source` and `archived_at` before promising a change, asking for change details or requesting write approval.

- `source=builtin`: the deployment owns the Agent's definition. This includes Agent Composer itself and Finding Agent. Workspace operations cannot change its name, description, labels or avatar, create configuration Revisions, set its default Revision, or archive/unarchive it. Configuration restrictions include instructions, model, model settings, tools and permissions. These writes fail with `409 builtin`; user approval and a correct ETag do not remove the restriction. Explain the restriction immediately; do not attempt a write to test it. For customization, offer to duplicate it into a custom Agent and edit that copy, leaving the builtin Agent intact.
- `source=custom` with `archived_at` set: restore it through the unarchive operation before editing or duplicating it, subject to the user's request and write review.
- An unarchived custom Agent can be edited within the current user's authority and tool review requirements.

Builtin definition updates are deployment work. Preparing the preset synchronizes the deployment's definition; it does not accept user configuration changes. A valid AgentConfig describes configuration structure, not whether the target Agent permits a write. Configuration validation does not prove that a builtin Agent can be edited.

## Change an editable Agent

Agent metadata (name, description, labels) belongs to the Agent head. Configuration belongs to immutable Revisions. Retain the head's ETag, then read the explicitly selected Revision or the current default Revision. Create a Revision with the entire AgentConfig, preserving all unrelated fields. Use the API validation operation before a complex write; validation stores nothing and does not prove that a later run will succeed.

`make_default=true` publishes the new default; use false for a candidate. Selecting an older base Revision is valid, but identify that base to the user and still use the head ETag you originally read. Setting a default does not edit the Revision. Metadata updates do not edit instructions or model settings. Labels are a replacement value.

Models use keys. Skills and subagents use IDs and exact Revision IDs after validation. Connection tools select existing Connections. Platform tools are toolsets named by group (`agents`, `models`, `connections`, `skills`, `environment_templates`, `providers`, `execution`, `schedules`), off by default; each tool is one operation keyed by its MCP tool name, with its own `enabled` and `permission`. `inherit` allows reads and asks before writes. `builtin_skills=["platform-operations"]` independently enables these local instructions. A configuration change affects later runs, never the current run's frozen configuration or delegated authority.

`memory` sets new-session defaults: `enabled` and `spaces[{name, space_id, access}]`, selecting existing workspace Memory Spaces with read or write access. The first accepted business run fills omitted session fields from its Agent Revision; the session then keeps those choices. Each Space owns its organization policy and model. `memory_mounts` selects standalone memories for new threads.

## Configure tool discovery

`tool_search` groups tools the Agent already selects. Each group names a `description`, `connections` by Connection ID, `plugins` by instance name, local `capabilities` (`environment`, `web`), and optionally `platform: true`. Each source belongs to one group. Omit `config.mode` or set it to `proxy`; control names and discovery limits are configurable. The model uses fixed discovery, description, invocation, tool-list, and group-list controls. It can enumerate the complete directory with `list_tools(detail="full")` and `next_offset` without promoting member schemas into model requests.

Ungrouped sources remain direct. Grouping preserves source selection, permissions, credentials, and execution checks; it does not activate disabled capabilities. `client_tools` keep their flat external-call contract. A Run override replaces the entire `tool_search` block; `groups: {}` disables grouping. Each child Agent uses its own Revision's plan. Migrate old native configurations to `mode: proxy`, remove `strategy` and connection-level `defer_loading`, and put selected connections into groups when proxy discovery is wanted.

## Configure desktop coordinates

The Computer toolset requires explicit enablement and an authorized desktop Environment. `toolsets.computer.config.coordinate_space` defaults to `pixels`. Select `normalized_1000` only when the chosen model supplies relative positions on a 0–1000 scale for each axis. The tool converts pointer positions using the referenced screenshot; model instructions must not ask the model to multiply by image dimensions. Scroll distances retain their declared units. Do not infer or change this setting from the Environment Provider or template. Check the deployed toolset catalog for supported configuration before creating a Revision.

## Read only the needed configuration

Revision lists support `view=compact`; selected revision reads accept `section=all` or a named configuration field. Fragment reads preserve the revision's content version. `/agents/{agent_id}/revisions/{revision_id}/compare` compares against `base_revision_id`, optionally for one section. Before publishing, obtain and preserve the complete base config; a diff or section alone is insufficient.

Search `/toolsets/index` by `query` and follow its cursor. Read `/toolsets/definitions` with repeated `name` values (up to five). Index descriptions omit schemas. Definitions report `missing` names and `oversized` references explicitly; use the same definition endpoint with one name and `offset=0` to read an oversized definition in fragments. Supported tool counts reflect deployment registration, not workspace authorization.
