# Configuration fields

AgentConfig is a complete structured configuration. `model.model_key` selects an existing authorized Model; `model.settings` contains settings supported by that Model's calling API. Never enter API keys or credential-bearing URLs. Provider setup belongs to the scoped settings page and requires the user's normal rights.

`instructions` supplies business behavior. `input_adapter` uses `native` with an empty config. `protocol` includes `public_name`, optional description, output modes, client tool policy, schemas and limits. Preserve unsupported editor fields.

`toolsets` selects files, shell, web and assets; each has `enabled`, `config` and per-tool selections. Each tool selection has `enabled`, `permission` and `config`. Web search and scrape need existing authorized Web Provider references. Shell and writable files need an appropriate managed Environment at business Run admission. The assistant's read-only knowledge mount is never a test sandbox.

`skills` references managed `skill_key` values and optional exact versions. `connection_tools` selects authorized Connection IDs and tool names; inspect safe dynamic contracts first. `secret_requirements` contains reference keys, purpose and required flags, never values. `memory` selects an authorized memory provider and subject policy. Installed `plugins` are deployment-owned registrations; knowledge files cannot install code. Missing capabilities must be reported explicitly.

`subagents` is a named finite graph of existing Agent identities, optional exact versions, context, budgets and Environment policy. `subagent_mode` is `inline` or `async`. `client_tools`, `output_spec`, `retries` and optional `reviewer` retain their complete schema. Target lifecycle, default Environment, owner, version and system purpose are not editable configuration paths.

For create mode, supply creation metadata with a name and optional description. For update mode, name and description remain business Agent metadata.
