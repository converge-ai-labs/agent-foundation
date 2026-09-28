# Tools and extension types

Choose an integration by what it provides, then select its installed key or resource ID in configuration. YAML selects executable integrations but does not install their Python code.

| Mechanism                 | Adds                                                                    | Configured through                                                     |
| ------------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Capability                | Tools, instructions, hooks, settings, or lifecycle behavior on an Agent | Agent `capabilities`                                                   |
| Harness Plugin            | Installed Harness integration                                           | `extensions/*.yaml`, then an Agent/default `harness_plugins` selection |
| Environment profile       | Provider and Project adapter configuration                              | `extensions/*.yaml`, then Environment selection                        |
| Environment Run Extension | Per-Run Environment integration                                         | `extensions/*.yaml`, then `defaults.environment_run_extensions`        |
| MCP server                | External tools from a command or remote server                          | `mcp/*.yaml` or `mcp/*.json`, then Agent/default `mcp_servers`         |
| Content Plugin            | Editable Skill and Markdown subagent content, not a Python extension    | `a13n-harness-ui plugin` commands                                      |

For provider-native tools and built-in search/scrape, use [Native tools and Web providers](native-and-web-tools.md).

## Native search and image generation

Follow the [native search and image-generation recipe](native-and-web-tools.md#native-search-and-image-generation); these are Agent tool choices, separate from Host Web search.

## MCP servers

[Configure an MCP server](mcp.md), then select its ID in the Agent or defaults.

### MCP field reference

The complete fields are in [MCP field reference](mcp.md#mcp-field-reference).

## Agent capabilities

Each Agent selection is `{capability: <catalog-key>, configuration: <JSON mapping>}`. Harness UI exposes its built-ins and configurable installed/native capabilities. There is no arbitrary module import field in a resource file.

Common built-in keys are `dynamic_environment`, `documents`, `web`, `skills`, `working_state`, `user_interaction`, `runtime_context`, `handoff`, `compaction`, and `codeact`. Permission and review Capabilities are intentionally absent from ordinary catalog choices; use root `security.shell_review` instead. Existing raw Agent selections remain compatible. Not every capability is automatically enabled.

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

Configure shell review in the selected root `a13n-harness-ui.yaml`, not an Agent capability picker:

```yaml
security:
  shell_review:
    enable: true
    model: model-review
    risk_threshold: high
```

`model` is a configured **Model resource ID**, not a subagent reference or ambient provider route. The shortcut merges permissions and optional review into one `ToolPermissionsCapability` for shell launches, with explicit root fields taking precedence and unrelated Agent rules preserved. `enable: false` leaves explicit Agent policies untouched. See the [complete shell-review recipe](configuration-recipes.md#configure-tool-review) for defaults, inheritance, errors, and usage. Review is not filesystem or network isolation and is independent of the `code-reviewer` child.

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

See [Skills and Content Plugins](skills-and-content-plugins.md) for explicit and automatic source precedence, the built-in offline configuration Skill, file access, and catalog capture. Selecting Skills adds knowledge discovery; it does not grant execution permissions.

## Content Plugins

See [Content Plugin installation](skills-and-content-plugins.md#install-a-content-plugin), [destructive uninstall behavior](skills-and-content-plugins.md#remove-a-content-plugin), and the [complete repository format](skills-and-content-plugins.md#author-a-content-plugin-repository). These are editable content bundles, not installed Python Harness Plugins.

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

| Resource kind                                 | Complete fields beyond shared `schema_version`, `kind`, `id`, `name`                                            |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `harness_plugin` (`plugin-` ID)               | Required `plugin_key`; `configuration` defaults to `{}`                                                         |
| `environment_run_extension` (`extension-` ID) | Required `extension_key`; `configuration` defaults to `{}`                                                      |
| `environment_profile` (`environment-` ID)     | Required `provider_key` and `adapter_key`; `provider_configuration` and `adapter_configuration` default to `{}` |

Run Extensions are selected through root `defaults.environment_run_extensions`. Provider/adapter and extension-specific configuration belongs to the installed implementation, with credential references rather than literal secret fields. See [custom Environment profiles](environments-and-projects.md#custom-environment-profiles) before selecting a provider.
