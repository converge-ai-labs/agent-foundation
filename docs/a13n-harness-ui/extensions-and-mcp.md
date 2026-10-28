---
title: Tools and extension types
description: Add tools to an Agent, check that they are active, and choose the right integration type.
---

To give an Agent a new tool, first identify who provides it. Most built-in features are **Capabilities** selected in the Agent YAML. External servers use **MCP**. The Environment determines where file and shell operations run.

| I need to…                                           | Configure…                         | Follow…                                                     |
| ---------------------------------------------------- | ---------------------------------- | ----------------------------------------------------------- |
| Read files or run commands                           | `dynamic_environment` on the Agent | [Files and shell](#files-and-shell)                         |
| Search or generate images through the Model provider | Native tool Capabilities           | [Native tools](native-and-web-tools.md)                     |
| Connect an external tool server                      | An MCP resource and its selection  | [MCP setup](mcp.md)                                         |
| Add reusable written instructions                    | Skills or Markdown subagents       | [Skills and Content Plugins](skills-and-content-plugins.md) |
| Change the execution machine or isolation            | An Environment profile             | [Environments](environments-and-projects.md)                |
| Add installed Python middleware                      | A Harness Plugin resource          | [Plugin files](#harness-plugin-and-run-extension-files)     |

## Agent Capabilities

Use an existing [Agent](agents-and-subagents.md#create-an-agent-from-files). Run `a13n-harness-ui config path` to find its configuration tree. Edit the appropriate file under `agents/`, beside the root YAML shown as `configuration_path`.

1. Add the needed entry to the Agent's `capabilities` list. Preserve unrelated entries, and edit an existing entry instead of adding the same Capability twice.
2. Put that Capability's settings under its own `configuration` mapping.
3. Run `a13n-harness-ui config validate`. Inspect `capability_warnings` as well as `valid`. An unavailable or invalid ordinary Capability can be skipped while the rest of the configuration remains usable.
4. Start a new conversation with the Agent and ask it to use the feature. Inspect the actual tool result. Configuration acceptance alone does not test a connection or a Model's tool support.

The installed Capability owns its settings schema. YAML selects the implementation by catalog key; it does not install Python code or import arbitrary modules. Permission and review Capabilities are excluded from the ordinary picker; configure shell review through the root shortcut below. Existing explicitly authored permission policies remain supported.

### Files and shell

Merge this fragment into the Agent file:

```yaml title="Agent file — capabilities fragment"
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: true
```

Select an [Environment](environments-and-projects.md) with the required file or shell operations. Then ask the Agent to list the working directory before requesting changes. Tools require both the Capability and an Environment that supports the operation; root tool switches can disable them globally.

An Agent `tools` list further filters visible tools by exact name. Use the [read-only helper example](agents-and-subagents.md#write-a-markdown-subagent) to expose only `glob`, `grep`, `ls`, and `view`.

### Shell review

To review shell requests, merge this fragment into the **root** `a13n-harness-ui.yaml`. `model-review` must identify an existing Model:

```yaml title="a13n-harness-ui.yaml — merge fragment"
security:
  shell_review:
    enable: true
    model: model-review
    risk_threshold: high
```

Validate, then use a new Run to exercise the policy. Root review fields override the corresponding shell-review settings while preserving unrelated Agent rules. Setting `enable: false` leaves explicitly authored Agent policies unchanged. Review evaluates requests; the Environment supplies isolation. Follow the [shell-review recipe](configuration-recipes.md#configure-shell-review) for approval behavior, error policy, and Guardian linking.

### Context management

Add these entries to the Agent when it should monitor context, summarize for handoff, and compact long histories:

```yaml title="Agent file — capabilities fragment"
capabilities:
  - capability: runtime_context
    configuration: {}
  - capability: handoff
    configuration: {}
  - capability: compaction
    configuration: {}
```

Set the context budget once in the Model's `model_characteristics`; see [Model budgets](models-and-authentication.md). The Capabilities derive their normal defaults from that budget. For a specific override, configure `runtime_context.context_window_tokens`, `handoff.summary_reminder_tokens`, or `compaction.trigger_tokens` inside the respective Capability's `configuration`. Explicit values take precedence.

### Tasks, questions, and CodeAct

Harness UI adds `file_context` and `working_state` by default. Root switches also add `user_interaction` and `codeact` when enabled. Other features, such as Skills, Web access, handoff, and compaction, need their Agent selections.

| Feature              | Configuration                                                                            | Check                                                                      |
| -------------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Tasks and notes      | Default `working_state`; explicit `configuration: {notes_enabled: false}` disables notes | Ask for a task list; F2 shows saved task facts                             |
| Structured questions | Root `tools.enable_ask_user_question`                                                    | A question can pause for your answer                                       |
| CodeAct              | Root `tools.enable_codeact`                                                              | The Agent can compose eligible tool calls with `run_code` or `run_program` |

The question and CodeAct switches also gate explicit Agent selections. CodeAct runs restricted Python and uses tools for side effects, with their existing permissions. `store`, `load`, and `forget` manage explicit JSON state; stored key names are shown in context. Configure `max_state_bytes` and `max_state_entries` under the `codeact` Capability when you need different state limits. See [root tool settings](configuration.md#built-in-tools-and-subagents) and [question handling](everyday-use.md#approvals-and-questions).

## Native search and image generation

Use the [native search and image-generation recipe](native-and-web-tools.md#native-search-and-image-generation) when the Model provider supplies the tool. These Agent selections are separate from Host-side Web search and scrape providers.

## MCP servers

Use [MCP setup](mcp.md) to create a server resource, select its ID, and test one tool call. Creating the file and selecting the server are separate steps.

### MCP field reference

See the [MCP field reference](mcp.md#mcp-field-reference) for command, HTTP, authentication, and connection-lifetime settings.

## Skills

Use [Skills and Content Plugins](skills-and-content-plugins.md) to add a reusable procedure or reference material. Select the `skills` Capability and make the source available through the Environment. Skill discovery gives the Agent material to read; execution still uses its configured tools and permissions.

## Content Plugins

Content Plugins package editable Skills and Markdown subagents. [Install a bundle](skills-and-content-plugins.md#install-a-content-plugin), select the needed content, and test it in a new Run. [Uninstalling](skills-and-content-plugins.md#remove-a-content-plugin) removes the installed directory, including local edits. To distribute your own bundle, use the [repository format](skills-and-content-plugins.md#author-a-content-plugin-repository).

## Harness Plugin and Run Extension files

These resources select installed Python integrations. Before writing a file, install the integration into the same Python environment as Harness UI and obtain its factory key and configuration schema from the integration's documentation.

1. Create a resource file under `extensions/`, beside the selected root YAML.
2. Select the resource ID in the appropriate Agent, Project, or root defaults.
3. Run `a13n-harness-ui config validate`; repair missing factory or invalid configuration errors.
4. Start a new Thread with that selection and test the integration's documented behavior.

This is a **template**, not a built-in memory plugin. Replace `vendor.memory` and its configuration with values from your installed integration:

```yaml title="extensions/memory.yaml — template; replace factory key"
schema_version: "1"
kind: harness_plugin
id: plugin-memory
name: Memory integration
plugin_key: vendor.memory
configuration: {}
```

For this resource, merge `harness_plugins: [plugin-memory]` into the Agent, preserving other selected IDs. Existing Threads retain their saved Plugin selections; changing a creation default does not rewrite them.

| Resource `kind`             | ID prefix      | Required implementation keys  | Settings                                                             | Selection                                                             |
| --------------------------- | -------------- | ----------------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `harness_plugin`            | `plugin-`      | `plugin_key`                  | `configuration`, default `{}`                                        | Agent or root `defaults.harness_plugins`                              |
| `environment_run_extension` | `extension-`   | `extension_key`               | `configuration`, default `{}`                                        | Project or root `defaults.environment_run_extensions` for new Threads |
| `environment_profile`       | `environment-` | `provider_key`, `adapter_key` | `provider_configuration`, `adapter_configuration`, each default `{}` | Environment selection                                                 |

All three also require `schema_version: "1"`, `kind`, `id`, and `name`. Use the integration's credential-reference fields rather than storing secrets in YAML. See [custom Environment profiles](environments-and-projects.md#custom-environment-profiles) for a provider configuration workflow.
