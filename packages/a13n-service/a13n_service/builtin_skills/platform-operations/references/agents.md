# Agents

## Check whether the Agent can be changed

Read the Agent head and check `source` and `archived_at` before promising a change, asking for change details or requesting write approval.

- `source=builtin` with `preset_kind=composer` or `finding`: users may change the model, `thinking`, `max_tokens`, `temperature` and `top_p` model settings, and supplemental instructions through ordinary Revisions. They may select an existing default Revision. Official instructions, tools, permissions and other configuration fields remain protected. Name, description, labels, avatar and archive/unarchive operations remain forbidden (`conflict` with reason `builtin`). For changes outside these fields, offer to duplicate it into a custom Agent.
- Other builtin presets, including Memory Organizer, remain read-only. Approval and a correct ETag do not remove their restrictions.
- `source=custom` with `archived_at` set: restore it through the unarchive operation before editing or duplicating it, subject to the user's request and write review.
- An unarchived custom Agent can be edited within the current user's authority and tool review requirements.

Preparing an existing Composer or Finding preserves its selected model and default Revision. Changed protected configuration appears as candidate Revisions without selecting them. Official instructions come from the current Worker capability and do not require a new Revision. A disabled selected model requires an explicit model change; preparation never silently chooses a replacement.

## Change an editable Agent

Use `create_agent` with direct `name`, `model`, `instructions` and any advanced capability fields for one atomic initial configuration. `update_agent_metadata` changes only name, description and labels. `update_agent_configuration` creates a Revision from a typed delta: read `get_agent`, retain `id` and `version`, then submit `agent_id`, `expected_version` as `id:version`, and only the configuration fields being changed.

For example, changing only a model needs `{"agent_id":"agt_FROM_READ","expected_version":"agt_FROM_READ:7","model":"model-from-list"}`. The Service preserves all omitted fields, validates the combined configuration and publishes it. Returned `agent`, `revision` and `changed_fields` identify the outcome. A no-op retains the version and default Revision. Supplied collections and structured sections replace those sections whole; inspect and preserve unrelated values within a section you deliberately replace. Null clears only nullable fields; it is not omission.

`make_default=true` selects the new Revision; false publishes a candidate. `base_revision_id` explicitly chooses a historical base while still requiring the originally read head version and an explicit `make_default` choice. Omitting `make_default` with a historical base returns `invalid_argument`; choose true only when replacing the active default is intended. `set_default_agent_revision` selects an existing Revision without editing it. `compare_agent_revisions` compares selected revisions. `validate_agent_configuration` validates a complete candidate configuration without storing it; it is optional for complex authoring, not a requirement to reconstruct configuration for ordinary delta edits.

For Composer or Finding, submit only the allowed model/settings or desired supplemental instructions. Keep protected fields unchanged. Never copy response-only `builtin_instructions` into `instructions`; official text is supplied by the Worker. An empty string clears the supplement. `duplicate_agent` materializes official text and supplement into an independent custom Agent. After a conflict, reconcile the new state and review a new change; never replace the version inside an already approved call.

Models use keys. Skills and subagents use IDs and exact Revision IDs after validation. Connection tools select existing Connections. Platform tools are toolsets named by group (`agents`, `models`, `connections`, `skills`, `environment_templates`, `providers`, `execution`, `schedules`), off by default; each tool is one operation keyed by its MCP tool name, with its own `enabled` and `permission`. `inherit` allows reads and asks before writes. `builtin_skills=["platform-operations"]` independently enables these instructions. Custom Agents, including copies of builtin Agents, need a primary Environment and synchronize these files with their `skill_sync` policy. Only server-owned builtin Agents keep Host-mounted instructions. A configuration change affects later runs, never the current run's frozen configuration or delegated authority.

`memory` sets new-session defaults: `enabled` and `spaces[{name, space_id, access}]`, selecting existing workspace Memory Spaces with read or write access. The first accepted business run fills omitted session fields from its Agent Revision; the session then keeps those choices. Each Space owns its organization policy and model. `memory_mounts` selects standalone memories for new threads.

## Configure context compaction

`summarize` defaults to false. Enable it to offer the explicit `summarize` tool, which lets the Agent write a continuation summary and resume from fresh context. Each child uses its own Revision's setting. `model_characteristics.proactive_context_management_threshold` controls the reminder (default 0.65; null disables reminders while keeping the tool). Automatic compaction is independent. An enabled tool reserves `summarize`, so a client tool cannot use that name.

`background_compaction` belongs to the Agent Revision and defaults to `{enabled: false, blocking_threshold: 0.95}`. Enable it for background summaries on ordinary threads. Inline and asynchronous children compact synchronously. The blocking threshold accepts values greater than zero and at most one; counted input at or above this share of the context window waits for existing work or compacts synchronously if none exists. Output reserve can require blocking earlier. The common compaction trigger remains independent. Disabling generation preserves delivery of retained results.

Context recovery is enabled by the Host runtime independently of background generation and the compaction threshold. It first tries normal compaction after native context-window overflow. Only overflow in both the ordinary and applicable summary requests permits bounded, lossy history reconstruction, at most once per logical Run, including across Worker takeover. Every reconstruction requires immediate investigation of compaction configuration and unbounded tool output, even when the Run completes. History retention is best effort and follows the selected Environment's lifetime.

## Configure tool discovery

`tool_search` groups tools the Agent already selects. Each group names a `description`, `connections` by Connection ID, `plugins` by instance name, local `capabilities` (`environment`, `web`), and optionally `platform: true`. Each source belongs to one group. Omit `config.mode` or set it to `proxy`; control names and discovery limits are configurable. The model uses fixed discovery, description, invocation, tool-list, and group-list controls. It can enumerate the complete directory with `list_tools(detail="full")` and `next_offset` without promoting member schemas into model requests.

Ungrouped sources remain direct. Grouping preserves source selection, permissions, credentials, and execution checks; it does not activate disabled capabilities. `client_tools` keep their flat external-call contract. A Run override replaces the entire `tool_search` block; `groups: {}` disables grouping. Each child Agent uses its own Revision's plan.

Connection selections configure execution through `connection_id`, `tools`, `permission`, and `permissions`. Unknown fields are ignored.

## Configure managed browser tools

The `browser` toolset is disabled by default. It selects `observe`, `read`, `navigate`, `act`, `wait`, `tabs`, `dialog`, `files` and `screenshot`, each with ordinary tool permissions. Enabling it does not allocate a browser. An integrating application binds a managed Browser to the Thread before accepting a Run; the Run freezes that binding. Browser resources, takeover and control-lease renewal are not platform operations available to Composer. The integrating application decides human activity and renews finite control through direct HTTP; expiry starts safe handback, not immediate tool availability. Check installed supplier support before promising browser execution. A blocked human-control call registers for an internal handback notification: continue other work or finish, without polling or replaying the blocked action. Uploads require a supported authorized transfer boundary and are currently unavailable.

## Configure desktop coordinates

The Computer toolset requires explicit enablement and an authorized desktop Environment. `toolsets.computer.config.coordinate_space` defaults to `pixels`. Select `normalized_1000` only when the chosen model supplies relative positions on a 0–1000 scale for each axis. The tool converts pointer positions using the referenced screenshot; model instructions must not ask the model to multiply by image dimensions. Scroll distances retain their declared units. Do not infer or change this setting from the Environment Provider or template. Check the deployed toolset catalog for supported configuration before creating a Revision.

## Execution policy

`execution.environment` sets ordinary command variables, not secrets. Thread and Run maps override per key; an empty string is an explicit empty value. Thread PATCH replaces the whole supplied map, with null/empty clearing that layer. Accepted Runs freeze the overlay and inject it at command dispatch above recipe defaults.

`execution` also sets sparse control defaults for new Runs: `tools.mode` is `inline` or `auto`, `tools.foreground_wait_seconds` is 0–300, and `steering` is `boundary` or `immediate`. Omitted/null leaves inherit the deployment defaults (`auto`, 5 seconds unless configured otherwise, `immediate`). Thread and single-Run choices override each leaf independently. Publishing a Revision affects future ordinary Runs; accepted Runs and their automatic continuations keep their frozen policy. Runtime capacity, result retention and shutdown limits remain deployment settings.

## Read only the needed configuration

Use `list_agent_revisions` for history and `get_agent_revision` for a selected revision or configuration section. Follow the reader's versioned fragment contract for large sections. Use `compare_agent_revisions` with explicit base and target revisions. Reading the whole base is unnecessary for a one-field delta; inspect every section that you explicitly replace.

Use `search_toolsets` for bounded discovery and `get_toolset_definition` for up to five exact names. Check `missing` and `oversized`; read oversized definitions using that tool's version and fragment fields. `list_toolsets` retains the complete catalog. Deployment support does not imply workspace authorization.

Historical revisions retain their stored tool names for inspection. A revision containing removed platform contracts, including disabled entries, cannot execute or serve as a delta-edit base: Service returns `conflict` with reason `configuration_unavailable`. Reauthor a complete configuration using the current catalog through the external revision API; Service does not translate or discard obsolete selections.

Before Service 0.1, Service may introduce breaking changes and developers migrate their own data. The platform-tool replacement breaks old normalized configurations, even when removed entries were disabled. Developers must reauthor affected complete configurations through trusted management; delta edits cannot repair an invalid base. Do not translate obsolete selections or promise recovery of old Runs.
