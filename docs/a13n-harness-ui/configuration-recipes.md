---
title: Common configuration recipes
sidebarTitle: Common recipes
description: Edit a Model, Agent, or tool configuration and check that the change is in use.
---

Use these recipes after [your first Agent works](setup.md). Each recipe changes one part of that setup. You can use WebUI settings for interactive editing; the file examples below are useful for inspecting, sharing, and reproducing your configuration.

## Before editing

Run this in your shell to locate the selected configuration:

```console
a13n-harness-ui config path
```

The default root file is `~/.a13n-harness-ui/a13n-harness-ui.yaml`. Paths such as `models/primary.yaml` below are relative to its directory, **not your project directory**. The data directory holds saved conversations and credentials; your project directory holds the files the Agent works on.

Examples labeled **complete file** can create a new resource. Examples labeled **merge into** change an existing mapping: preserve other fields and list entries, and do not add a second copy of the same YAML key. References use a resource's `id`, regardless of its filename or display name.

After an edit, run:

```console
a13n-harness-ui config validate
```

Fix validation errors and inspect warnings before testing the feature. Unknown additive fields can be preserved without being used. Validation checks configuration, not provider or MCP connectivity. Project directories must be available when a Run starts.

If you use a custom tree, use the same selector for validation and startup:

```console
a13n-harness-ui --config /path/to/a13n-harness-ui.yaml config validate
a13n-harness-ui --config /path/to/a13n-harness-ui.yaml
```

## Change the default Agent

1. Find the desired Agent's `id` in `agents/`, or create an Agent with `a13n-harness-ui add agent`.

2. Merge that ID into the root file:

   ```yaml title="a13n-harness-ui.yaml — merge into defaults"
   defaults:
     agent: agent-coder
   ```

3. Validate, then start a new conversation. Use `/status` in the terminal to check the selected Agent.

The default initializes new conversations. To switch an existing conversation, use `/agent agent-coder` instead.

## Change the Model, reasoning, or context budget

This example creates a Model, assigns it to an existing Agent, and tests the selection. If you only want to tune your current Model, edit that existing Model file instead of creating another resource.

1. Save an API key with the CLI's hidden prompt:

   ```console
   a13n-harness-ui auth key set key-primary
   ```

2. Create the complete file below. Replace the route with a model your account supports if necessary:

   ```yaml title="models/primary.yaml — complete file"
   schema_version: "1"
   kind: model
   id: model-primary
   name: Primary API model
   route: openai-responses:gpt-5
   authentication:
     kind: api_key
     credential_ref: key-primary
   settings:
     thinking: high
   model_characteristics:
     context_window_tokens: 128000
     proactive_context_management_threshold: 0.65
     compact_threshold: 0.90
   ```

   `thinking` requests a supported reasoning level. `context_window_tokens` sets the local working budget; it does not increase the provider's limit. Here context reminders and compaction derive from 65% and 90% of that budget. Choose a budget and request settings appropriate to your route.

3. Open the Agent file you use and replace its `model` field:

   ```yaml title="agents/coder.yaml — merge into your existing Agent"
   model: model-primary
   ```

4. Validate the configuration. In the terminal, clear a remembered Project Model with `/model default`. Clear temporary request overrides with `/thinking default`, `/fast reset`, and `/pro reset` if you previously set them.

5. Check `/status`, then send a short message. The next Run uses the accepted Model settings; a Run already in progress keeps its original settings.

For environment-managed credentials, replace `credential_ref: key-primary` with `env: OPENAI_API_KEY` and set that variable in the process that launches Harness UI. Do not write a key value in the YAML.

Model connection fields, request settings, and local context policy have separate owners. See the [Model reference](models-and-authentication.md#model-file-reference) before adding provider-specific fields or media-input declarations.

## Connect an OpenAI-compatible endpoint

Have the endpoint's API base URL, model ID, supported protocol, and API key ready. This example uses Chat Completions; choose a Responses route only when your endpoint implements Responses.

1. Save the key with `a13n-harness-ui auth key set key-compatible`.

2. Create this complete Model file, replacing `your-model-id` and the example URL:

   ```yaml title="models/compatible.yaml — complete file"
   schema_version: "1"
   kind: model
   id: model-compatible
   name: Compatible API
   route: openai-chat:your-model-id
   authentication:
     kind: api_key
     credential_ref: key-compatible
   model_configuration:
     base_url: https://api.example.com/v1
   settings: {}
   ```

3. Set your Agent's `model: model-compatible`, validate, and select that Agent. Clear any Project Model override, then send a test message as in the previous recipe.

Use `model_configuration.base_url` for the connection URL and `settings` for request parameters. Local HTTP endpoints are supported; the URL must not contain credentials, query parameters, or a fragment. Native `xai:` SDK and subscription routes do not accept this HTTP override. For provider-specific reasoning, use the supported native route, such as `deepseek:`, `zai:`, or `moonshotai:`, rather than assuming a generic compatible route preserves it.

## Add coding instructions

For instructions used by one Agent, edit its existing `instructions` field:

```yaml title="agents/coder.yaml — merge into your existing Agent"
instructions: |
  Read the relevant code before editing.
  Keep changes focused and validate the affected behavior.
  Report changed files, checks, and remaining limitations.
```

Validate, select that Agent, and start the next Run. To share instructions across Agents, put them in `AGENTS.md` beside the root YAML. To describe a repository, put `AGENTS.md` in its working directory. Harness UI does not scan ancestor directories for guidance.

These instructions guide behavior. Configure actual file and command authority through [Environment permissions](environments-and-projects.md#execution-permissions).

## Enable selected built-in subagents

1. Merge the desired roles into the root file:

   ```yaml title="a13n-harness-ui.yaml — merge into subagents"
   subagents:
     include: [explorer, code-reviewer]
   ```

2. Validate, then inspect the configured inclusion:

   ```console
   a13n-harness-ui config subagents --format json
   ```

3. Start the next Run to use the updated roster.

The available roles are `explorer`, `code-reviewer`, and `executor`; `[]` disables automatic inclusion. Built-ins inherit the parent Model and join the root roster, not every child's roster. You do not need to copy their Markdown files.

For a child with its own Model, [reference an Agent](agents-and-subagents.md#reference-an-existing-agent-as-a-subagent). For custom instructions on the parent's Model, [write a Markdown subagent](agents-and-subagents.md#write-a-markdown-subagent).

## Configure shell review

Shell review asks a Model to assess a shell launch before the permission decision. It makes an additional model request; it does not isolate the command's filesystem or network access.

1. Configure a reviewer Model, or choose an existing Model ID. The reviewer is a Model resource, not the `code-reviewer` subagent.

2. Merge this policy into root YAML, replacing `model-review` with that ID:

   ```yaml title="a13n-harness-ui.yaml — merge into security"
   security:
     shell_review:
       enable: true
       risk_threshold: extra_high
       model: model-review
       on_flagged: approval_required
       on_error: allow
   ```

3. Validate, then start a new Run. The policy reviews `environment.shell_exec` for the Agent and its children. Inspect available risk and reason details with `/review request-id` after a review request exists.

In this example, `extra_high` risk requests human approval. Non-timeout reviewer failures use `allow`, which still runs the remaining permission checks. Choose `deny` or `approval_required` for a different error policy. A reviewer timeout always denies execution. Human approval has its own `tools.interaction_timeout_seconds` timeout.

The shortcut overrides the Agent's shell permission rule, including an explicit `allow`, `deny`, or `ask`. Supplied review fields replace corresponding Agent fields; unrelated rules and reviewer instructions remain. Omitted fields inherit the Agent policy, then the built-in defaults. Set `enable: false` to stop adding the shortcut; explicit Agent policies remain. See [shell-review reference](configuration.md#shell-review-shortcut) for defaults and Codex Guardian linking.

### Use TypeSafe Jev for review

To use Jev as the reviewer, create this complete Model file and make `TYPESAFE_API_KEY` available to the Harness UI process:

```yaml title="models/jev-review.yaml — complete file"
schema_version: "1"
kind: model
id: model-jev-review
name: Jev tool review
route: typesafe:jev-latest
authentication:
  kind: api_key
  env: TYPESAFE_API_KEY
```

Set the previous recipe's `security.shell_review.model` to `model-jev-review`. Validate and start a new Run. Keep the conversational Agent on a text-capable Model. For repeatable evaluations, replace `jev-latest` with a tested versioned Jev ID.

A custom gateway must implement the native TypeSafe protocol. To use one, add `model_configuration.base_url: https://jev-gateway.example` to this Model; the default endpoint is `https://api.typesafe.ai`.

Jev maps severity 0–3 to `low`, `medium`, `high`, and `extra_high`. Confidence does not change the decision. Jev supplies no text explanation. Test representative commands before replacing an existing reviewer.

## Enable an MCP server

Have a working MCP endpoint and its authentication details ready. The following URL is a placeholder, not a public test server.

1. Make `DOCS_MCP_TOKEN` available to the process launching Harness UI.

2. Create the complete server file:

   ```yaml title="mcp/docs.yaml — complete file"
   schema_version: "1"
   kind: mcp_server
   id: mcp-docs
   name: Documentation server
   transport:
     url: https://mcp.example.com/mcp
     headers:
       Authorization: "Bearer ${DOCS_MCP_TOKEN}"
   ```

3. Add the server ID to your Agent's selected servers, preserving other desired IDs:

   ```yaml title="agents/coder.yaml — merge into your existing Agent"
   mcp_servers: [mcp-docs]
   ```

4. Validate, then create a new conversation with that Agent. Try a known read-only tool from the server to check the connection separately from configuration validation.

The server file registers a resource; selecting it enables it. `mcp_servers: null` inherits defaults when conversation selections are initialized; `[]` selects none. Existing conversations keep their MCP selections. For local command transport or client-style JSON, use the [MCP guide](mcp.md).

## Enable Skills

1. Add this entry to the existing Agent's `capabilities` list:

   ```yaml title="agents/coder.yaml — merge into capabilities"
   capabilities:
     - capability: skills
       configuration: {}
   ```

2. Put a Skill's `SKILL.md` in a discoverable source, such as the Project's `.agents/skills/<skill-name>/`. Follow [the Skill guide](skills-and-content-plugins.md) for its format.

3. Validate and begin the next Run. In the terminal, type `$` to look for the Skill.

Automatic sources also include user `~/.agents/skills`, installed Content Plugins, and the release-owned configuration Skill. Discovery uses the selected Environment. An explicit `configuration.roots` entry must be an absolute Environment path; naming a Host directory does not make it available inside a Sandbox. See [source precedence](skills-and-content-plugins.md#automatic-sources-and-precedence).

## Change display and tool switches

Merge only the fields you want to change:

```yaml title="a13n-harness-ui.yaml — merge into display and tools"
display:
  theme: dark
  mode: detailed
  show_status: true
  max_tool_result_lines: 15
  max_tool_argument_chars: 8192
tools:
  enable_ask_user_question: true
  interaction_timeout_seconds: 300
  enable_codeact: true
```

Validate and restart to apply display defaults. For an immediate presentation change, use `/theme` or `/mode`; these do not change model reasoning or permissions.

The interaction timeout applies to questions, approvals, and external results in the terminal, or a complete [WebUI decision batch](webui.md#questions-and-approval-timeouts). It is not a model execution deadline, and expiry never grants approval. `enable_codeact: false` removes built-in CodeAct runners and state tools from newly resolved Runs, even if an Agent selects the Capability explicitly. See [root fields](configuration.md#display-settings) for allowed ranges.

## Work with multiple directories

For one directory, launch the terminal from that directory; no Project file is needed. For several directories:

1. Create a [Project resource](environments-and-projects.md#project-file-reference) with the ordered absolute roots you need.
2. Validate the configuration.
3. Launch the terminal from the Project's first root, and inspect `/status` before starting work.

Use `/environment` to change execution mode. Project roots organize work; Full Control still uses the Host account's authority.

## Why did my edit not take effect?

Check these in order:

1. **File location:** does `config path` show the tree you edited?
2. **Validity:** does `config validate` accept the change, without relevant Capability warnings?
3. **Selection:** does `/status` show the Agent and Model you meant to change? Clear temporary overrides if needed.
4. **Timing:** an active Run keeps its captured configuration. Test ordinary resource edits in the next Run, default selections in a new conversation, and process/display settings after a restart.

A rejected file edit can leave the last accepted configuration active. Repair the error rather than deleting the data directory, which also stores conversations and credentials. See [precedence and activation](configuration.md#what-wins-and-when-edits-apply) for the full rules.
