---
title: Agents and subagents
description: Create an Agent, run it in a repository, and add helpers with inherited or independent Models.
---

An **Agent** saves a Model choice, instructions, tools, and optional helpers. Start by creating one Agent and testing it. Add a subagent when you have a specific task to delegate.

| Task                                        | Start here                                                                |
| ------------------------------------------- | ------------------------------------------------------------------------- |
| Create an Agent interactively               | `a13n-harness-ui add agent`                                               |
| Create an Agent in YAML                     | [Create an Agent from files](#create-an-agent-from-files)                 |
| Add a shipped helper                        | [Built-in subagents](#built-in-subagents)                                 |
| Write a helper that uses the parent's Model | [Write a Markdown subagent](#write-a-markdown-subagent)                   |
| Give a helper its own Model or tools        | [Reference an existing Agent](#reference-an-existing-agent-as-a-subagent) |
| Look up configuration fields                | [Agent file reference](#agent-file-reference)                             |

## Create an Agent from files

This recipe creates a coding Agent with file and shell tools. You need an installed Harness UI and a working Model connection. Complete [setup](setup.md) first if you have neither.

Run `a13n-harness-ui config path`. All paths below are relative to the parent directory of the root YAML shown as `configuration_path`, normally `~/.a13n-harness-ui/`. For a custom tree, put `--config /absolute/path/a13n-harness-ui.yaml` before each command's subcommand.

### 1. Create a Model

If setup already created a Model, reuse its `id` in step 2. Otherwise save this complete file as `models/primary.yaml`:

```yaml title="models/primary.yaml — complete file"
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

Set `OPENAI_API_KEY` in the process that launches Harness UI. The YAML stores the variable name, not the secret. For a stored key or subscription connection, use [Models and authentication](models-and-authentication.md).

### 2. Create the root Agent

Save this complete file as `agents/coder.yaml`. Replace `model-primary` if you reused another Model.

```yaml title="agents/coder.yaml — complete file"
schema_version: "1"
kind: agent
id: agent-coder
name: Coding agent
model: model-primary
instructions: |
  Make focused changes. Read the relevant code first, validate the changed
  behavior, and report the result.
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: true
subagents: []
```

`dynamic_environment` supplies file and shell tools supported by the selected Environment. Environment access and root tool switches still apply. The default Full Control profile executes with the host account's authority; choose a [Sandbox or custom Environment](environments-and-projects.md) when you need isolation.

Start with this small configuration. Add [Skills](skills-and-content-plugins.md) or other [Capabilities](extensions-and-mcp.md) when the Agent needs them. Harness UI supplies its application infrastructure, including configured task and question tools.

### 3. Select and validate it

Validate the configuration, then launch a new conversation from your repository:

```console
a13n-harness-ui config validate
cd /absolute/path/to/your-repository
a13n-harness-ui --agent agent-coder
```

Send: “List the top-level files and explain where the source code lives. Do not change files.” Check that the response includes a file-tool result from the intended repository. `/status` shows the selected Agent and Model. A validation success checks configuration; the prompt exercises the Model and Environment connection.

To make this Agent the default for new conversations, merge the following fields into the root YAML, preserving other settings:

```yaml title="a13n-harness-ui.yaml — merge into existing defaults"
defaults:
  agent: agent-coder
```

Use `--agent` for a new conversation, not with `--resume`. In the common single-directory workflow, the launch directory supplies the Project and working directory without a separate Project file. See [Projects and threads](environments-and-projects.md) for explicit multi-root Projects.

## Built-in subagents

The package ships three helpers. Choose the ones you want in the **root YAML**, not in the Agent's `subagents` list.

| Name            | Task to give it                                                    |
| --------------- | ------------------------------------------------------------------ |
| `explorer`      | Locate a specific implementation and return file references        |
| `code-reviewer` | Review a stated change and report material defects, or no findings |
| `executor`      | Complete a bounded task with a clear expected result               |

1. Merge this fragment into `a13n-harness-ui.yaml`:

   ```yaml title="a13n-harness-ui.yaml — merge fragment"
   subagents:
     include: [explorer, code-reviewer]
   ```

2. Inspect the catalog and validate:

   ```console
   a13n-harness-ui config subagents
   a13n-harness-ui config validate
   ```

3. Start a new conversation with `agent-coder`. Ask it to delegate a narrow repository question to `explorer`. Use `/subagents` to inspect the child execution and its result.

Normal setup selects all three roles. Omit `include`, or set it to `[]`, to disable automatic inclusion. `setup --advanced` offers all or none; edit the list to select a subset.

Built-ins inherit the parent's Model, Capabilities, and visible tools. Their role instructions do not make a helper read-only. Use the explicit filter in the next recipe to restrict file tools.

The selected helpers are appended after the root Agent's authored children, in list order. They are leaves; inclusion does not recursively attach them to every child Agent. To attach just one package role to a particular Agent, add this entry to that Agent's roster:

```yaml title="Agent file — subagents fragment"
subagents:
  - markdown: subagent-builtin-explorer
```

An explicit reference to the same built-in ID is deduplicated. A different child also named `explorer` is a conflict: rename your child or remove the built-in selection. The `subagent-builtin-*` IDs belong to the package. Package upgrades and configuration edits affect subsequent Runs; active Runs retain their captured definitions.

## Write a Markdown subagent

Use Markdown for a helper that needs different instructions but the same Model and Capabilities as its parent.

1. Save this complete file as `subagents/investigator.md` beside the selected root YAML:

   ```markdown title="subagents/investigator.md — complete file"
   ---
   name: investigator
   description: Trace a bounded repository question and report evidence.
   instruction: Use for focused read-only discovery before implementation.
   tools: [glob, grep, ls, view]
   ---

   Find the relevant implementation and tests. Return exact file paths and explain
   how the pieces connect. Do not modify files. Stop at the assigned scope.
   ```

2. Add its ID to `agents/coder.yaml`. Preserve any children already in the list:

   ```yaml title="agents/coder.yaml — subagents fragment"
   subagents:
     - markdown: subagent-investigator
   ```

3. Run `a13n-harness-ui config validate`, launch `a13n-harness-ui --agent agent-coder`, and ask it to delegate a file-reading task to **`investigator`**. Inspect the result with `/subagents`.

The parent must contribute the named tools. Unknown allowlist names fail composition. This example exposes read-only file tools; mandatory Harness infrastructure is retained separately.

| Frontmatter field | Required | Purpose                                                       |
| ----------------- | -------- | ------------------------------------------------------------- |
| `name`            | Yes      | Delegate name; also derives the default ID `subagent-<name>`  |
| `description`     | Yes      | Short role description shown to the parent                    |
| `id`              | No       | Explicit stable `subagent-` resource ID                       |
| `instruction`     | No       | Extra routing guidance shown to the parent                    |
| `tools`           | No       | Exact visible-tool filter, as a list or comma-separated names |

The body supplies the child's instructions. Without `tools`, the child inherits the parent's visible-tool filter. Markdown children have no nested roster and always inherit the parent's Model. Remove legacy `model` fields, including `model: inherit`; use an Agent reference for independent settings.

## Reference an existing Agent as a subagent

Use an Agent reference when the helper needs its own Model, Capabilities, MCP selection, tool groups, or children. Continue from the `agent-coder` recipe above.

### 1. Define the reviewer Model

Save this complete file as `models/review.yaml`, or reuse an existing Model ID:

```yaml title="models/review.yaml — complete file"
schema_version: "1"
kind: model
id: model-review
name: Review API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
settings:
  thinking: low
```

The example uses the same credential as the parent with different reasoning settings. Choose settings supported by your provider and model.

### 2. Define the child Agent

Save this complete file as `agents/reviewer.yaml`:

```yaml title="agents/reviewer.yaml — complete file"
schema_version: "1"
kind: agent
id: agent-reviewer
name: Independent reviewer
model: model-review
instructions: |
  Review the assigned change. Report concrete material defects with file paths.
  Return findings to the parent without editing files.
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: false
tools: [glob, grep, ls, view]
harness_plugins: []
mcp_servers: []
subagents: []
```

The tool allowlist limits this Agent to read-only file tools plus mandatory Harness infrastructure. Empty Plugin and MCP lists select no sources from those categories.

### 3. Add the reference to the parent's roster

Add the following entry to `agents/coder.yaml`, retaining any existing children:

```yaml title="agents/coder.yaml — subagents fragment"
subagents:
  - agent: agent-reviewer
```

Run `a13n-harness-ui config validate` and start a new `agent-coder` conversation. Ask the parent to delegate a focused review to **`agent-reviewer`**. This child uses `model-review`. You can also launch it directly with `a13n-harness-ui --agent agent-reviewer`.

Agent and Markdown entries can share a roster. Each immediate delegate name must be unique, and Agent references must not form cycles. Delegation uses the application's existing Project, Environment, and authorization rules.

## Names and references at a glance

| Value              | Example                     | Where to use it                                        |
| ------------------ | --------------------------- | ------------------------------------------------------ |
| Filename           | `agents/reviewer.yaml`      | File organization; it can differ from the ID           |
| Agent `id`         | `agent-reviewer`            | `--agent`, `defaults.agent`, `- agent:`, delegate name |
| Agent `name`       | `Independent reviewer`      | Human-facing label                                     |
| Markdown `id`      | `subagent-investigator`     | `- markdown:` reference                                |
| Markdown `name`    | `investigator`              | Delegate name                                          |
| Built-in name      | `explorer`                  | Root `subagents.include` and delegate name             |
| Built-in source ID | `subagent-builtin-explorer` | Explicit `- markdown:` reference                       |

## Tool search groups

Use groups when an Agent has a large tool collection. The model discovers and calls group members through a fixed proxy instead of receiving every member schema up front.

1. Configure and select the sources first. The example below assumes an existing, enabled MCP resource `mcp-docs`; see [MCP servers](mcp.md).

2. Merge this fragment into the **Agent YAML**:

   ```yaml title="agents/coder.yaml — merge fragment"
   tool_search:
     groups:
       knowledge:
         description: Search project documents
         mcp_servers: [mcp-docs]
   ```

3. Run `a13n-harness-ui config validate` and `a13n-harness-ui config show --format json`. Inspect `tool_search_previews`. These commands inspect the plan without connecting to MCP.

4. In a new conversation, ask for a task served by the group. The model uses `discover_tools`, `describe_tool`, and `call_tool`. `list_tools(detail="full")` with `next_offset` lists the directory across pages.

Groups can also select `harness_plugins` by resource ID and `capabilities` by enabled Capability key. Each source belongs to one group. Disabled sources stay disabled; enabled sources outside groups remain direct. Groups with no prepared tools expose no controls. Content Plugins supply Skills and subagents, not tool sources.

| Setting                       | Rule                                                                               |
| ----------------------------- | ---------------------------------------------------------------------------------- |
| Group name                    | Starts with a letter; at most 32 letters, digits, underscores, or hyphens; no `__` |
| `description`                 | Nonblank; at most 512 characters                                                   |
| Members                       | Existing exact source IDs or Capability keys; distinct tool names within a group   |
| Optional `tool_search.config` | Proxy control names and result limits; `mode` omitted or `proxy`                   |
| Agent `tools` allowlist       | Canonical targets such as `knowledge__lookup`, not only the proxy's `call_tool`    |

Renaming a group changes its canonical target names; update matching allowlists. Grouping preserves tool execution permissions and CodeAct eligibility. Discovery does not add member schemas to later requests. Independent Agent children use their own groups; Markdown children inherit the parent's plan. Edit groups in YAML; WebUI group editing is unavailable. Changes apply to later Runs.

See [ToolSearch](../a13n-harness/tool-search.md) for proxy controls and execution details. For older configurations, replace `mode: native` with `proxy` and remove `strategy`.

## Agent file reference

Every file needs `schema_version: "1"`, `kind: agent`, a unique `agent-` ID, and a display `name`.

| Field             | Default | Meaning                                                                                |
| ----------------- | ------- | -------------------------------------------------------------------------------------- |
| `model`           | `null`  | Model ID; required before this Agent can run                                           |
| `instructions`    | `""`    | Instructions added to the package system prompt                                        |
| `capabilities`    | `[]`    | Ordered `{capability, configuration}` entries from the installed catalog               |
| `harness_plugins` | `null`  | Inherit root defaults; `[]` selects none; a list selects exact IDs                     |
| `mcp_servers`     | `null`  | Inherit root defaults; `[]` selects none; a list selects exact IDs                     |
| `tools`           | `null`  | Default contributed surface; `[]` removes optional tools; a list is an exact allowlist |
| `tool_search`     | `null`  | Tool groups and optional proxy configuration                                           |
| `subagents`       | `[]`    | Ordered Agent or Markdown references                                                   |

Plugin and MCP defaults initialize a new Thread's selections. Existing Threads keep their saved lists until explicitly changed. Resource edits are resolved for later Runs; active and saved executions keep their captured configuration.

Put Capability settings inside that Capability's `configuration`, not at the Agent's top level. Root tool-disable switches remain effective even when an Agent selects the corresponding Capability.

## Instructions and guidance

Put reusable role instructions in Agent `instructions` or a Markdown child's body. Put repository conventions in the working directory's `AGENTS.md`. Put cross-project guidance in `AGENTS.md` beside the root YAML.

These additions coexist with the package-owned base system prompt. Each Run captures its instructions. Guidance supplies context, not execution permissions.

Harness UI reads the working directory's `AGENTS.md` through the Environment. It also lists guidance entrypoints in immediate child directories and nearby `.agents/skills` directories, so the Agent can inspect relevant guidance on demand. This bounded discovery does not load those child bodies or change the selected Skill catalog. It does not scan ancestors or use `RULES.md`/`AGENTS.override.md` fallbacks. Global and working-directory guidance remain in native history and are hidden from ordinary TUI history display.

To disable project-entrypoint hints, add an explicit `file_context` Capability with `configuration: {include_project_guidance: false}` while preserving your other Capability entries.

## Import external definitions

For guided import, use `/import` in the TUI. Select Codex or Claude Code, choose the source scope, inspect the preview, and confirm **import and enable**. The imported helper inherits the parent Model and tools.

For standalone conversion:

1. Preview the selected product and scope:

   ```console
   a13n-harness-ui import subagents --product claude-code --scope user
   ```

2. Read the target paths and diagnostics. Apply the same selection when the preview is correct:

   ```console
   a13n-harness-ui import subagents --product claude-code --scope user --apply
   ```

3. Add each imported `subagent-` ID to your Agent's Markdown roster, then validate and test delegation as above. Standalone import writes definitions; it does not enroll them.

The CLI also accepts `--product codex` or `--product cursor`. For repository definitions, use `--scope project --project-root /absolute/path/to/repository`.

Standalone import preserves representable tool names and diagnoses unsupported Model, permission, hook, and MCP settings. `--apply` writes the previewed definitions into local configuration and can replace existing targets; it leaves the source product's files unchanged. Import is a one-time conversion. Guided import reports definition-save and roster-update results separately, so a partial success tells you which step remains.
