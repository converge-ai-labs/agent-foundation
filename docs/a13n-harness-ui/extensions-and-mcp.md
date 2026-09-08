# Extensions, tools, and Skills

Harness UI separates reusable configuration from executable integrations. A YAML file selects an installed capability or extension; it does not install Python code or invent a provider implementation.

| Mechanism                 | Adds                                                                    | Configured through                                                     |
| ------------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Capability                | Tools, instructions, hooks, settings, or lifecycle behavior on an Agent | Agent `capabilities`                                                   |
| Harness Plugin            | Installed Harness integration                                           | `extensions/*.yaml`, then an Agent/default `harness_plugins` selection |
| Environment profile       | Provider and Project adapter configuration                              | `extensions/*.yaml`, then Environment selection                        |
| Environment Run Extension | Per-Run Environment integration                                         | `extensions/*.yaml`, then `defaults.environment_run_extensions`        |
| MCP server                | External tools from a command or remote server                          | `mcp/*.yaml` or `mcp/*.json`, then Agent/default `mcp_servers`         |
| Content Plugin            | Editable Skill and Markdown subagent content, not a Python extension    | `a13n-harness-ui plugin` commands                                      |

## MCP servers

Creating a server file makes it available; selecting its ID enables it. There is no server-level `enabled` flag.

### Copy a JSON configuration

Create `mcp/servers.json` beside your selected root configuration. Common client-style `mcpServers` objects work directly:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/workspace"]
    },
    "docs": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer example-token",
        "X-Workspace": "${WORKSPACE_ID}"
      }
    }
  }
}
```

Replace the illustrative endpoint, path, and token. Enable these entries with `mcp_servers: [mcp-filesystem, mcp-docs]` on an Agent or under root `defaults`. Creating the JSON file alone does not enable them.

A file can contain multiple servers. Names become lowercase, punctuation/underscore/space runs become hyphens, and `mcp-` is added unless already present: `My_Server` becomes `mcp-my-server`. Names with no ASCII letters or digits use `mcp-` plus the first 12 hex characters of their SHA-256 digest. Conflicting IDs across YAML/JSON files or after name normalization reject the configuration; no file silently wins. Names are limited to 256 characters and resulting IDs to 128.

For a command entry, use `command`, optional `args`, and optional `env`. For a remote entry, use `url` and optional `headers`. Optional `type` accepts `stdio` for commands and `http` or `streamable-http` for remote endpoints. Remote connections use Streamable HTTP, not legacy SSE. OAuth login, JSONC comments, trailing commas, `disabled`, and unrelated client-specific fields are not supported. `mcpServers` is a common client convention, not a universal MCP protocol configuration standard.

Both `.yaml` and `.json` can also contain the single-resource format shown below; both accept the `mcpServers` wrapper. Existing YAML files continue to work without migration.

### Literal values and environment references

Command `env` (or canonical `transport.environment`) and remote `headers` accept:

| Value                   | Behavior                                                        |
| ----------------------- | --------------------------------------------------------------- |
| `"example-token"`       | A literal string, including ordinary non-secret settings        |
| `"${API_TOKEN}"`        | Read an environment variable when a Run starts                  |
| `"Bearer ${API_TOKEN}"` | Substitute `${NAME}` occurrences in a string                    |
| `{"env": "API_TOKEN"}`  | Existing explicit environment reference; works in YAML and JSON |

Empty literal strings and whitespace are preserved. References require non-empty variables in the **Harness UI process** environment; exporting in another shell does not change an already-running process. Expansion is one pass, only for `${NAME}` with a valid environment-variable name; there is no shell execution or default-value syntax. These substitutions apply only to environment/header values, not commands, arguments, or URLs.

Direct token configuration is supported; environment references are optional. Keep credential-bearing source files private and out of version control. Harness UI does not copy MCP source text or literal environment/header values into `config show`, accepted generations, or Run compositions: it retains source locations and digests, then reads values at Run startup. A captured Run requires that literal-bearing source file to remain present and byte-identical until client construction. Editing it is supported for newly captured Runs, but an older captured Run or child continuation may fail with `mcp_source_changed`; use current configuration for a new Run. Already constructed clients keep their Run-local values. Environment references can rotate without editing the source file.

After editing, run `a13n-harness-ui config validate` and start a new session if you changed default MCP selections. Validation does not connect to servers or verify credentials.

### Command transport

Create `mcp/github.yaml`:

```yaml
schema_version: "1"
kind: mcp_server
id: mcp-github
name: GitHub
transport:
  command: npx
  arguments: ["-y", "@modelcontextprotocol/server-github"]
  environment:
    GITHUB_TOKEN:
      env: GITHUB_TOKEN
```

This illustrative server requires its executable/package and access token to be available. Harness UI does not validate external service entitlement by making a test call during file parsing. Review the command and package before enabling it.

### Remote transport

Create `mcp/docs.yaml` using your actual MCP endpoint:

```yaml
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Documentation service
transport:
  url: https://mcp.example.com/mcp
  headers:
    Authorization:
      env: DOCS_MCP_AUTHORIZATION
```

The environment variable contains the entire header value expected by that server, for example a bearer value. Alternatively, write the header value directly or use `"Bearer ${DOCS_MCP_TOKEN}"`. URLs must be credential-free HTTPS; plain HTTP is allowed only for a literal loopback host with no configured headers. Authenticated redirects cannot weaken the transport or leak headers to another origin.

### Enable a server

In an Agent file:

```yaml
mcp_servers: [mcp-github, mcp-docs]
```

Or in root YAML:

```yaml
defaults:
  mcp_servers: [mcp-github]
```

An Agent's `mcp_servers: null` inherits the root defaults; `[]` explicitly selects none. Existing sessions retain exact sticky selections. Clients/processes are constructed fresh for Runs and are not saved into continuations. Model Sandbox selection does not imply that an arbitrary external MCP command or remote service is sandboxed.

### MCP field reference

The shared fields are `schema_version: "1"`, `kind: mcp_server`, unique `mcp-` `id`, and `name`. `transport` accepts exactly one form:

| Form    | Fields                                                                                                                    |
| ------- | ------------------------------------------------------------------------------------------------------------------------- |
| Command | Required `command`; `arguments` defaults to `[]`; `environment` defaults to `{}`, values are strings or `{env: VARIABLE}` |
| Remote  | Required `url`; `headers` defaults to `{}`, values are strings or `{env: VARIABLE}`                                       |

## Agent capabilities

Each Agent selection is `{capability: <catalog-key>, configuration: <JSON mapping>}`. Harness UI exposes its built-ins and configurable installed/native capabilities. There is no arbitrary module import field in a resource file.

Common built-in keys are `dynamic_environment`, `documents`, `web`, `skills`, `working_state`, `user_interaction`, `runtime_context`, `handoff`, `compaction`, and `codeact`. Native declarative keys such as `ShellReviewCapability` are also supported. Not every capability is automatically enabled.

The complete capability-specific schemas are owned by the installed Harness/native implementation, rather than flattened into Harness UI YAML. Consult the [Harness capability reference](https://github.com/converge-ai-labs/agent-foundation/tree/main/packages/a13n-harness/a13n_harness/capabilities) and the implementation matching your installed version. `a13n-harness-ui config validate` checks selected keys and their configuration.

### Files and shell

```yaml
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: true
```

This enables native Environment tools, not a second local runner. Use `tools` on the Agent for an exact additional visibility filter and an Environment profile for execution isolation.

### Shell review

Setup enables review for subscription starters. To configure it yourself, first create a reviewer Model resource, then select:

```yaml
capabilities:
  - capability: ShellReviewCapability
    configuration:
      model: model-review
      risk_threshold: high
      on_flagged: approval_required
      on_error: skip
```

`model` here is a **Model resource ID** for an auxiliary reviewer with no execution tools, not a subagent reference or ambient provider route. Explicit capability `model_settings` can override its captured Model settings. With this configuration, flagged commands require approval and non-timeout review errors add no restriction; invocation-policy denial and approval requirements still apply. A review timeout always denies the command before execution, regardless of `on_error`. Omitting `on_error` retains the Harness library's `approval_required` default. Review does not provide filesystem or network isolation. This is independent of the `code-reviewer` built-in child, which reviews changes when delegated work.

### Context management

```yaml
capabilities:
  - capability: runtime_context
    configuration: {}
  - capability: handoff
    configuration: {}
  - capability: compaction
    configuration: {}
```

Normally configure `model_characteristics` on the Model so reminder and compaction defaults stay aligned. Advanced explicit settings include `runtime_context.configuration.context_window_tokens`, `handoff.configuration.summary_reminder_tokens`, and `compaction.configuration.trigger_tokens`; explicit values take precedence over derived values.

### Tasks, questions, and CodeAct

Task and note tools are available by default through native working state, including bounded note-context injection. `working_state` accepts native configuration such as `notes_enabled: false` to disable notes. F2 displays committed task facts, not a separate CLI checklist store.

Root `tools.enable_ask_user_question` gates `ask_user_question`; `tools.enable_codeact` gates CodeAct. Both switches also gate explicitly authored capability selections. CodeAct also exposes `store`, `load`, and `forget` for explicit JSON values saved with the Harness continuation; only stored key names are projected into context. Agent capability configuration accepts `max_state_bytes` and `max_state_entries` to bound this state. Its `run_code` and `run_program` execute restricted Python; host effects use eligible tools and their existing policy, not unrestricted Python filesystem or network access. See the [root reference](configuration.md#built-in-tools-and-subagents) for defaults and the [decision guide](everyday-use.md#approvals-and-questions) for question timeouts.

## Skills

An Agent must select the `skills` capability to expose Skills:

```yaml
capabilities:
  - capability: skills
    configuration:
      roots: []
```

`roots` is Harness UI's optional ordered unique list of **Environment paths**, up to 128 entries, scanned before automatic sources. It is not an arbitrary host path escape hatch. Automatic sources come from Project, installed Content Plugin, and user Skill locations through the selected Environment's exposed paths. Automatic directories are `<project-root>/.agents/skills` and `~/.agents/skills`, plus installed plugin Skill roots. Explicit roots must be canonical absolute Environment paths, not `~` or relative paths. In Sandbox, a host path that is not mapped into the Environment is not made available by writing it here.

Discovered Skill names appear in slash completion. Skill instructions are task-specific; adding a source does not mean every Skill should run on every prompt. Exact source precedence, path mapping, and native discovery follow the [Environment Skill Sources contract](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness-ui/02b-environment-skill-sources.md).

## Content Plugins

Content Plugins package editable Skills and Markdown subagents. They are stored under the selected data root, separate from root YAML and Python Harness Plugins.

```console
a13n-harness-ui plugin install /path/to/plugin-repository
a13n-harness-ui plugin install https://github.com/example/agent-content --plugin plugin-review --ref v1.0.0
a13n-harness-ui plugin list --format json
a13n-harness-ui plugin uninstall plugin-review
```

The repository/ref and plugin ID must exist and follow the [Content Plugin layout](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-harness-ui/01b-content-plugin-repositories.md). Inspect and trust repository content before installing it. Installation does not automatically enable every child in every Agent.

Select a contributed child normally:

```yaml
subagents:
  - markdown: subagent-investigator
```

Local `subagents/*.md` overrides a Content Plugin child with the same ID. Plugin-to-plugin duplicate IDs use deterministic precedence and produce diagnostics. Invalid optional plugin content is skipped with a diagnostic; a selected missing child still fails composition. Package-owned built-in IDs are reserved and cannot be overridden. `config validate`, `config show`, and terminal logs expose diagnostics.

## Harness Plugin and Run Extension files

A Harness Plugin resource selects an **installed** factory. Replace this illustrative key and configuration with those documented by your integration:

```yaml
schema_version: "1"
kind: harness_plugin
id: plugin-memory
name: Memory integration
plugin_key: vendor.memory
configuration: {}
```

Save it under `extensions/`, then select `harness_plugins: [plugin-memory]` in the Agent or root defaults. Unknown/uninstalled keys fail validation; writing the file is not installation.

| Resource kind                                 | Complete fields beyond shared `schema_version`, `kind`, `id`, `name`                                                                    |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `harness_plugin` (`plugin-` ID)               | Required `plugin_key`; `configuration` defaults to `{}`                                                                                 |
| `environment_run_extension` (`extension-` ID) | Required `extension_key`; `configuration` defaults to `{}`                                                                              |
| `environment_profile` (`environment-` ID)     | Required `provider_key`, `provider_schema_version`, `adapter_key`; `provider_configuration` and `adapter_configuration` default to `{}` |

Run Extensions are selected through root `defaults.environment_run_extensions`. Provider/adapter and extension-specific configuration belongs to the installed implementation, with credential references rather than literal secret fields. See [custom Environment profiles](environments-and-projects.md#custom-environment-profiles) before selecting a provider.
