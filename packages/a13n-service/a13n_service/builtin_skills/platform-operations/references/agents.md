# Agents

Agent metadata (name, description, labels) belongs to the Agent head. Configuration belongs to immutable Revisions. Read the head and retain its ETag, then read the explicitly selected Revision or the current default Revision. Create a Revision with the entire AgentConfig, preserving all unrelated fields. Use the API validation operation before a complex write; validation stores nothing and does not prove that a later run will succeed.

`make_default=true` publishes the new default; use false for a candidate. Selecting an older base Revision is valid, but identify that base to the user and still use the head ETag you originally read. Setting a default does not edit the Revision. Metadata updates do not edit instructions or model settings. Labels are a replacement value.

Models use keys. Skills and subagents use IDs and exact Revision IDs after validation. Connection tools select existing Connections. `platform_tools.groups` explicitly selects platform MCP groups, with read/write review permissions. `builtin_skills=["platform-operations"]` independently enables these local instructions. A configuration change affects later runs, never the current run's frozen configuration or delegated authority.
