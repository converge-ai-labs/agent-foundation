# Configuration fields

AgentConfig is a complete structured configuration. `model.model_key` selects an existing authorized Model; `model.settings` contains settings supported by that Model's calling API. Never enter API keys or credential-bearing URLs. Provider setup belongs to the scoped settings page and requires the user's normal rights.

`media_understanding` selects optional image, video, and audio fallback Model keys. Explicit choices require the matching declared media capability; omitted or null kinds inherit Workspace defaults. Native-capable primary Models still receive files directly. Run requests can override these choices through `config_override.media_understanding` without changing the Agent.

`instructions` supplies business behavior. `input_adapter` uses `native` with an empty config. `protocol` includes `public_name`, optional description, output modes, client tool policy, schemas and limits. Preserve unsupported editor fields.

`toolsets` selects files, shell, web and assets; each has `enabled`, `config` and per-tool selections. Each tool selection has `enabled`, `permission` and `config`. Web search and scrape need existing authorized Web Provider references. Shell and writable files need an appropriate managed Environment at business Run admission. The assistant's read-only knowledge mount is never a test sandbox.

`skills` references managed `skill_key` values and optional exact versions. `connection_tools` selects authorized Connection IDs and tool names; inspect safe dynamic contracts first. `secret_requirements` contains reference keys, purpose and required flags, never values. Installed `plugins` are deployment-owned registrations; knowledge files cannot install code. Missing capabilities must be reported explicitly.

`memory` accepts a single authorized Provider selection or independent named `entries`. Each entry declares its purpose, backend, mode and subject policy. Mem0 entries use record mode; file memory uses document mode with an authorized Provider or an inline `filesystem` backend in an attached Environment. Purposes guide the Agent's tool choice; entry names do not classify content. Files persist according to the Environment's storage lifetime. Preserve existing entries when changing one memory option, and consult the schema for exact nested fields.

`subagents` is a named finite graph of existing Agent identities, optional exact versions, context, budgets and Environment policy. `subagent_mode` is `inline` or `async`. `client_tools`, `output_spec`, `retries` and optional `reviewer` retain their complete schema. Target lifecycle, default Environment, owner, version and system purpose are not editable configuration paths.

For create mode, supply creation metadata with a name and optional description. For update mode, name and description remain business Agent metadata.

## Enable Web search

Set `toolsets.web.enabled` and `toolsets.web.tools.search.enabled` to true. In the same update, set `toolsets.web.tools.search.config` to `{"provider_id": "<authorized Web Provider ID>", "max_results": 5}`. Scrape uses `toolsets.web.tools.scrape.config.provider_id`. There is no `toolsets.web.config.web_provider_key`. Enabling search or scrape without its Provider ID fails validation; do not remove the Provider reference to isolate an enablement error.

Omit `creation_metadata` entirely in update mode, including after the first application of a create draft. A retained creation metadata value in a read response does not make it editable.

On a failed update, use the returned error code, field path, validation reason or operation index to correct the request. Validation issue paths are relative to config; operation indexes are zero-based. `extra_forbidden` means remove or relocate an unsupported field; `missing` means supply a required field. Consult the schema before retrying. For version/digest conflicts, reread the draft. Do not repeat unchanged invalid requests or guess by stripping required resource references.
