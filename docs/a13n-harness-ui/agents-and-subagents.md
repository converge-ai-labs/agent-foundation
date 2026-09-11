# Agents and subagents

An **Agent** is a reusable YAML configuration: it chooses a Model, instructions, tools, extensions, and a child roster. A **Markdown subagent** is a lightweight child role that inherits the parent's model. An existing Agent can also be referenced as a child when it needs an independent configuration.

You do not need a separate executor or daemon. All three forms use the same Harness execution and captured Run lifecycle.

| I want to…                               | Read…                                                                     |
| ---------------------------------------- | ------------------------------------------------------------------------- |
| Add an Agent interactively               | Run `a13n-harness-ui add agent`                                           |
| Write a working Agent YAML               | [Create an Agent from files](#create-an-agent-from-files)                 |
| Look up every Agent field                | [Agent file reference](#agent-file-reference)                             |
| Give a child its own model               | [Reference an existing Agent](#reference-an-existing-agent-as-a-subagent) |
| Give a child different instructions only | [Write a Markdown child](#write-a-markdown-child)                         |
| Enable the shipped helper roles          | [Built-in subagents](#built-in-subagents)                                 |
| Add repository or global guidance        | [Instructions and guidance](#instructions-and-guidance)                   |

## Create an Agent from files

This complete example creates a coding Agent and a separately configured reviewer. Paths below are relative to the directory containing your selected `a13n-harness-ui.yaml`, normally `~/.a13n-harness-ui/`.

### 1. Create a Model

Create `models/primary.yaml`:

```yaml
schema_version: "1"
kind: model
id: model-primary
name: Primary API model
route: openai-responses:gpt-5
authentication:
  kind: api_key
  env: OPENAI_API_KEY
```

The environment variable must be set in the process launching Harness UI. Alternatively, use a stored `credential_ref` or a subscription Model as described in [Models and authentication](models-and-authentication.md). Do not put an API key into this file.

### 2. Create the root Agent

Create `agents/coder.yaml`:

```yaml
schema_version: "1"
kind: agent
id: agent-coder
name: Coding assistant
model: model-primary
instructions: |
  Make focused changes, validate the affected behavior, and report limitations.
capabilities:
  - capability: dynamic_environment
    configuration:
      files_enabled: true
      shell_enabled: true
  - capability: skills
    configuration: {}
  - capability: runtime_context
    configuration: {}
  - capability: handoff
    configuration: {}
  - capability: compaction
    configuration: {}
harness_plugins: null
mcp_servers: null
tools: null
subagents: []
```

The listed capabilities enable file/shell work, Skill discovery, context reminders, handoff, and compaction. The CLI also supplies default task tools, configured questions, and other native application infrastructure. `tools: null` leaves contributed tools visible; it does not create tools missing from your capabilities.

### 3. Select and validate it

Edit the root `a13n-harness-ui.yaml`, preserving other settings:

```yaml
schema_version: "1"
defaults:
  agent: agent-coder
  environment_profile: environment-native
subagents:
  include: [code-reviewer, executor, explorer]
```

Then run:

```console
a13n-harness-ui config validate
cd /absolute/path/to/your-repository
a13n-harness-ui --agent agent-coder
```

The explicit `--agent` is optional once `defaults.agent` selects it. It selects a new session; do not combine it with `--resume`. The launch directory supplies the terminal workspace. You do not need a Project file for the common single-directory case.

## Reference an existing Agent as a subagent

Use an Agent reference when the child needs its own model, reasoning, capabilities, MCP selection, or nested roster. This is the only child configuration form that selects an independent model.

### 1. Define the reviewer Model

Create `models/review.yaml`:

```yaml
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

This can use the same provider route with different settings, or a different supported Model. Harness UI uses `thinking` for supported reasoning settings; whether the selected provider/model supports a requested effort is still provider-specific.

### 2. Define the child Agent

Create `agents/reviewer.yaml`:

```yaml
schema_version: "1"
kind: agent
id: agent-reviewer
name: Independent reviewer
model: model-review
instructions: |
  Review the assigned change. Report only concrete material defects with file paths.
  Do not edit files; return findings to the parent.
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

The tool filter makes this example a read-only file explorer. Instructions alone are not a permissions boundary. An exact allowlist must match contributed tool names; unknown names fail composition. Mandatory Harness infrastructure is retained independently of the optional tool filter.

### 3. Add the reference to the parent's roster

Replace `subagents: []` in `agents/coder.yaml` with:

```yaml
subagents:
  - agent: agent-reviewer
```

That is the whole reference: **use the existing Agent's `id`, not its filename or display name**. It does not copy `reviewer.yaml`, convert it to Markdown, or inherit the parent's model over `model-review`.

```console
a13n-harness-ui config validate
a13n-harness-ui --agent agent-coder
```

The parent can now delegate to **`agent-reviewer`**. The child's independent Agent ID is its delegate roster name. You can also run that same resource directly with `a13n-harness-ui --agent agent-reviewer`.

An Agent can reference multiple Agents and Markdown children:

```yaml
subagents:
  - agent: agent-reviewer
  - markdown: subagent-investigator
```

A referenced Agent can have its own explicit children. Cycles such as `agent-coder → agent-reviewer → agent-coder` are invalid. The same immediate roster name cannot appear twice. Child work stays within the application's existing Project, Environment, lifecycle, and authorization rules; an Agent reference is not an escalation mechanism.

## Tool proxy groups

Group large MCP and Harness Plugin tool collections without loading every tool schema into the model context. Edit **the Agent file**, not root defaults or a separate group resource:

```yaml
# In agents/assistant.yaml; referenced resources must already exist.
tool_proxy:
  groups:
    knowledge:
      description: Search project documents and organizational memory
      mcp_servers: [mcp-docs]
      harness_plugins: [plugin-memory]
  config:
    search_name: search_proxy_tools
    call_name: call_proxy_tool
    max_results: 10
    max_search_bytes: 32768
```

`config` is optional. Group names start with a letter, contain up to 32 letters, digits, underscores or hyphens, and cannot contain `__`. Descriptions must be nonblank and at most 512 characters. Select exact resource IDs, including distinct IDs for multiple instances of the same plugin. A source belongs to one group only. Different sources in a group must have distinct tool names; collisions fail rather than inventing aliases.

**Grouping does not enable sources.** Agent `mcp_servers` and `harness_plugins` remain creation defaults, and existing Threads retain their sticky source selections. A referenced disabled source is dormant. Enabled sources not listed in a group remain direct. Empty groups produce no discovery controls. Content Plugins provide skills and subagents; they are not Harness Plugin tool sources.

In the browser, choose **Configure tool groups**, select the Agent, and add, edit, rename, or remove groups. Select sources and review the grouped, dormant, and direct counts. These are static source counts using Agent defaults, not live tool counts or a particular Thread's selection. **Save groups** validates the complete configuration, writes the existing Agent source, and reads it back. A failed save retains the draft. Other Agent settings remain intact. Changes apply to subsequent Runs, never the active Run.

```console
a13n-harness-ui config validate
a13n-harness-ui config show --format json
```

`config show` includes `tool_proxy_previews` for configured Agents. Preview and validation do not connect to MCP or discover tools.

When using an exact `tools` allowlist, list the canonical target names such as `knowledge__lookup`, not just `call_proxy_tool`. Proxy controls do not authorize every member. Renaming a group changes those canonical names, so update any affected allowlists. Existing CodeAct policy is preserved; grouping does not make an ineligible tool CodeAct-callable. See [ToolProxy discovery and execution](../a13n-harness/tool-proxy.md).

Independent Agent children use their own groups. Markdown children inherit the parent's grouping plan and their existing source/tool restrictions. Older saved Runs without a grouping plan retain direct presentation.

## Names and references at a glance

| Value              | Example                     | Used for                                                               |
| ------------------ | --------------------------- | ---------------------------------------------------------------------- |
| Filename           | `agents/reviewer.yaml`      | Human organization; may differ from the ID                             |
| Agent `id`         | `agent-reviewer`            | `--agent`, `defaults.agent`, `- agent:`, and Agent child delegate name |
| Agent `name`       | `Independent reviewer`      | Human-facing label                                                     |
| Markdown `id`      | `subagent-investigator`     | `- markdown:` references                                               |
| Markdown `name`    | `investigator`              | Delegate roster name; also derives the default Markdown ID             |
| Built-in name      | `explorer`                  | `subagents.include` and built-in delegate name                         |
| Built-in source ID | `subagent-builtin-explorer` | Explicit Markdown reference to the package-owned role                  |

## Built-in subagents

Discover the installed catalog and current root inclusion:

```console
a13n-harness-ui config subagents
a13n-harness-ui config subagents --format json
```

| Name            | Role                                                                                                  | Good delegation input                                                                   |
| --------------- | ----------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `code-reviewer` | Independent, risk-proportionate review; reports concrete material issues, accepts no-findings results | Exact changed paths/diff, intended behavior, affected invariants, focused/deep scope    |
| `executor`      | Autonomous execution of a bounded task; reports completed, partial, or blocked work                   | Scope, constraints, expected result, and an existing task ID if one was assigned        |
| `explorer`      | Repository discovery and evidence gathering                                                           | The symbol, flow, or concept to locate; starting paths and reason for the investigation |

These roles are based on the reference YAACLI definitions, adapted to Harness UI's canonical format. They inherit the parent's complete Model recipe, including settings and context characteristics, and its capabilities and visible tools. They carry role instructions, not independent credentials or special permissions. The explorer's intended read-only behavior is instruction guidance, not an enforced separate tool sandbox.

Configure names in **root** `a13n-harness-ui.yaml`:

```yaml
# All shipped roles:
subagents:
  include: [code-reviewer, executor, explorer]
```

```yaml
# Just discovery and review:
subagents:
  include: [explorer, code-reviewer]
```

```yaml
# No automatic built-in children (also the default when omitted):
subagents:
  include: []
```

Normal setup includes all three roles. `setup --advanced` offers **Include all defaults** or **Do not include defaults**. Choosing all saves the concrete names; it does not create user-owned copies. To choose a subset, edit `subagents.include`. Unknown or repeated names are invalid.

Inclusion appends children to the root Agent's authored roster in listed order. It does not recursively attach all defaults to child Agents. Built-in Markdown children are leaves. To explicitly attach one to a particular Agent instead of global inclusion, write:

```yaml
subagents:
  - markdown: subagent-builtin-explorer
```

An explicit edge to the same built-in ID is not duplicated by root inclusion. A different source named `explorer`, such as your own `subagent-explorer`, conflicts if both are selected. Remove the built-in from `include` or rename your custom child; Harness UI never silently chooses one. The reserved `subagent-builtin-*` IDs cannot replace package definitions.

Built-in bodies are captured with each Run. Package upgrades and inclusion edits affect later captures, not active Runs or saved immutable compositions. The parent remains responsible for planning, integration, and decisions; including a reviewer does not mean every change needs a review, and including an executor does not require parallelizing every task.

## Write a Markdown child

For a role that only needs different instructions, create `subagents/investigator.md`:

```markdown
---
name: investigator
description: Trace a bounded repository question and report evidence.
instruction: Use for focused read-only discovery before implementation.
tools: [glob, grep, ls, view]
---

Find the relevant implementation and tests. Return exact file paths and explain
how the pieces connect. Do not modify files. Stop at the assigned scope.
```

Then reference it from an Agent:

```yaml
subagents:
  - markdown: subagent-investigator
```

| Frontmatter field | Required? | Meaning                                                                 |
| ----------------- | --------- | ----------------------------------------------------------------------- |
| `name`            | Yes       | Delegate roster name; derives `subagent-<name>` unless `id` is explicit |
| `description`     | Yes       | Short parent-facing role description                                    |
| `id`              | No        | Explicit stable `subagent-` ID                                          |
| `instruction`     | No        | Additional routing guidance shown to the parent                         |
| `tools`           | No        | Exact child visible-tool filter; list or comma-separated names          |

The body is the child's additional instructions. With no `tools` field it inherits the parent's visible-tool filter. Markdown always inherits the parent model and has no nested roster.

**Do not add `model: inherit` or any other `model` field.** Inheritance is implicit. Remove that field from older local Markdown definitions. For independent model settings, use the [Agent reference recipe](#reference-an-existing-agent-as-a-subagent), not an expanded Markdown format. Previously captured Run model recipes are not rewritten.

## Agent file reference

Every Agent YAML uses `schema_version: "1"`, `kind: agent`, a unique `agent-` ID, and a human-facing `name`.

| Field             | Default | Meaning                                                                                                   |
| ----------------- | ------- | --------------------------------------------------------------------------------------------------------- |
| `model`           | `null`  | Model resource ID; can remain unconfigured while authoring, but execution requires a model                |
| `instructions`    | `""`    | Additional instructions, not a replacement system prompt                                                  |
| `capabilities`    | `[]`    | Ordered `{capability, configuration}` selections from the installed catalog                               |
| `harness_plugins` | `null`  | Inherit root defaults; `[]` selects none; a list selects exact IDs                                        |
| `mcp_servers`     | `null`  | Inherit root defaults; `[]` selects none; a list selects exact IDs                                        |
| `tools`           | `null`  | No additional visibility filter; `[]` exposes no optional contributed tools; a list is an exact allowlist |
| `subagents`       | `[]`    | Ordered `agent` or `markdown` references                                                                  |

Plugin/MCP defaults initialize a session's exact selections. Later global defaults do not rewrite those sticky selections. Referenced resources and included children are resolved again for later Runs, while captured executions remain immutable.

Capabilities own their own JSON configuration schemas. See [tool and extension recipes](extensions-and-mcp.md); arbitrary settings do not belong at the Agent's top level. Root disabled tool switches cannot be bypassed by explicitly selecting a capability.

## Instructions and guidance

Every Agent receives the package's base system prompt, authored in [`a13n_harness_ui/assets/system_prompt.md`](https://github.com/converge-ai-labs/agent-foundation/blob/main/packages/a13n-harness-ui/a13n_harness_ui/assets/system_prompt.md). It is a release-owned Markdown asset, not a file copied into user configuration. Each Run captures its text; later package edits do not rewrite saved compositions. Agent `instructions` and Markdown bodies add instructions through the native instructions channel; they do not replace the base.

Harness UI also reads `AGENTS.md` beside the root YAML and in the working directory. These are user-role contextual guidance, retained in native history but hidden in ordinary terminal and `/history` presentation. There is no ancestor scan and no `RULES.md` or `AGENTS.override.md` fallback. Global guidance is captured with accepted configuration; working-directory guidance uses the Environment's bounded reader. Neither source changes execution permissions.

## Import external definitions

`/import` in chat offers Codex or Claude Code source selection, scope, a preview, and explicit **import and enable** confirmation. It preserves role instructions while inheriting the parent model and tools. Publication and Agent enrollment are separate operations; partial completion is reported for deliberate retry.

For standalone conversion:

```console
a13n-harness-ui import subagents --product claude-code --scope user
a13n-harness-ui import subagents --product codex --scope project --project-root .
a13n-harness-ui import subagents --product cursor --scope user --apply
```

Without `--apply`, the command is a preview. It does not enroll the result into an Agent; add `- markdown: subagent-<name>` yourself. The standalone importer can preserve representable tool names, but never imports an independent model into Markdown. External model selections and unsupported product settings produce diagnostics. Foreign permissions, hooks, and MCP settings do not silently become Harness UI behavior.

Imports do not modify or continuously synchronize foreign sources. Setup does not scan `~/.yaacli/` or other products' definitions at runtime; built-ins are installed package content.
