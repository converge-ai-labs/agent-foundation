# Harness UI

Harness UI (`a13n-harness-ui`, installed from the `a13n-harness-ui` distribution) is an interactive coding CLI built on Agent Foundation Harness. Its native full-terminal interface supports Windows, macOS, and Linux, with reflowing Markdown, selectable interactions, and image drafts. One foreground process owns one `HarnessUiApp`, the current conversation, and its active work. The optional `a13n-harness-ui webui` command starts the HTTP API and a bundled Hello World page in a foreground server; browser chat and management are not implemented. There is no detached daemon or detached execution mode.

## Install and update

Use [`uv`](https://docs.astral.sh/uv/getting-started/installation/) to install the published CLI in an isolated tool environment; no repository checkout or Node.js is required:

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

If the command is not on PATH, run `uv tool update-shell` and restart your shell. For a shortcut in Bash or Zsh, add this line to `~/.bashrc` or `~/.zshrc`, then reload the file or open a new shell:

```bash
alias anui='a13n-harness-ui'
```

Use `anui` to start, `anui setup` to reconfigure, and `anui update` to upgrade. The alias is optional and does not rename the installed executable.

```console
a13n-harness-ui update
```

This explicitly updates the running uv-tool installation without waiting for the cached startup check or opening chat/setup. It requires uv on PATH and does not ask for another confirmation. Restart Harness UI afterwards. Startup checks alone never install automatically. See [update behavior and troubleshooting](automation-and-troubleshooting.md#logs-updates-and-exit) for other installation methods and failures.

### Dependency upgrades and constraints

You can also upgrade directly, without starting Harness UI:

```console
uv tool upgrade a13n-harness-ui
```

uv resolves the UI and its dependencies together within the installed tool's requirements and constraints. Published UI requirements allow compatible Harness-group patches within `>=0.0.5,<0.1.0` and Logging releases within `>=0.1.0,<0.2.0`. Environment accepts `a13n-envd-client>=0.0.5,<0.1.0`. The Harness-group packages still require matching versions of one another. A dependency patch does not require a new UI release while it remains within these bounds; consuming newer APIs or crossing a breaking compatibility line requires updated UI requirements. These ranges are not a general compatibility guarantee for all `0.x` releases.

An upgrade cannot widen requirements embedded in an older wheel. If your installed UI pins exact dependencies, install a newer UI release that publishes the bounded requirements. If an explicit tool-version pin or installation constraint prevents that upgrade, revise that constraint through uv rather than forcing incompatible dependencies into the tool environment. For example, an explicit UI range can be selected with `uv tool install 'a13n-harness-ui>=MIN,<MAX'`, replacing `MIN` and `MAX` with your intended UI versions. This changes the UI selection, not its dependency metadata.

Managed Local EIP selects the native daemon matching the installed `a13n-envd-client` version. It does not search for the latest daemon or silently update Python packages at startup; package installation requires an explicit update command or confirmation. After an explicit package upgrade, restart Harness UI; the next managed Local EIP use acquires the matching native release if it is not cached. Source client version `0.0.0`, missing metadata, or invalid metadata cannot select a managed release; use a validated explicit executable for source development. Full Control does not need that acquisition.

For source development, use `make a13n-harness-ui` from the repository root; see the [repository contribution guide](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md) for prerequisites.

Startup checks local configuration before opening full-terminal chat. If no Model is configured, a setup wizard opens in the same full-terminal interface before chat. A model request begins only when you explicitly send a prompt. The current directory is the workspace; you do not need to create or manage a Project.

## First use

Setup runs automatically when needed. To change configuration later, leave chat and run `a13n-harness-ui setup`; there is no `/setup` command inside chat.

1. **Connect a model:** choose Codex subscription, Grok subscription, or an API key. Existing compatible Codex/Grok logins are detected and reused without another login prompt, including credentials that can refresh when used. API-key access guides you through provider/protocol, base URL, a hidden key or environment-variable/stored-key reference, model ID, and settings preset.
2. **Choose a model:** Codex offers Astra, Sol, and Terra; Grok offers 4.6, 4.5, and 4.20 Reasoning. Sol and Grok 4.6 are the defaults. Availability depends on your account.
3. **Choose Codex service tier:** Fast is selected by default and saves a priority request; Standard saves the default tier. This step is skipped for other providers. Priority may use more quota or cost more and does not guarantee speed. See [temporary and permanent Fast settings](models-and-authentication.md#fast-mode-and-service-tiers).
4. **Choose execution permissions and finish:** Full Control runs as your host account; Sandbox checks isolation prerequisites before saving. Your answer saves the configuration directly, with no extra confirmation. There is no automatic fallback between modes.

The subscription starter enables shell review at the extra-high risk threshold and all three built-in subagents (`code-reviewer`, `executor`, `explorer`). Codex uses high reasoning and a 350k working budget. Use `a13n-harness-ui setup --advanced` for optional context, reasoning, review, subagent, and instruction choices. These remain ordinary editable configuration values.

After first use, run `a13n-harness-ui add model` to create only a reusable Model. Run `a13n-harness-ui add agent` to create another Agent: select an existing Model or **Create a new model**, then name the Agent. Existing resources, defaults, and permissions stay unchanged; repeated names create separate resources instead of replacing them. In chat, `/agent` switches the complete agent; `/model` temporarily overrides only the model for this TUI session without saving configuration. See [model choices](models-and-authentication.md#starter-model-choices) for the reviewed catalog.

Use Up/Down and Enter, or type option numbers. Esc goes back; Ctrl+C or Ctrl+D cancels. Cancelling first-use setup returns to the command shell without opening chat. After successful first-use setup, chat opens automatically. Running `a13n-harness-ui setup` explicitly returns to the command shell after saving or cancelling.

If the selected account is missing, setup shows the external `a13n-harness-ui login codex` or `a13n-harness-ui login grok` command, then offers recheck or configuration without signing in. Login runs outside the terminal UI; chat has no `/login` command. Unsupported or malformed stores show repair guidance and a recheck action; they are not overwritten. Discovery never refreshes tokens or starts authentication. Cancelling setup does not undo a completed login or configuration publication.

External subagent migration is separate from built-in inclusion: use `/import` in chat to select Codex or Claude Code definitions, project/user scope, and an explicit import-and-enable confirmation. Setup does not scan or import external definitions.

Imports preserve instructions and explicitly inherit the parent model and visible tools rather than activating foreign tool names. Preview lists unsupported settings and conflicts. Successful import enrolls selected definitions in the selected Agent's roster; file publication and enrollment are separate operations, and partial completion is reported for deliberate retry.

Setup creates editable YAML resources. It does not put OAuth tokens or API keys into them, call a model to test entitlement, or silently overwrite edited Model resources. A preserved existing Model keeps its existing settings even if you selected different starter values; edit its YAML to change those values. Explicitly connecting the selected Agent can update its model binding through the reviewed publication.

## Find your next step

| I want to…                                                      | Guide                                                               |
| --------------------------------------------------------------- | ------------------------------------------------------------------- |
| Send prompts, steer work, attach images, and handle decisions   | [Use the terminal](everyday-use.md)                                 |
| Find every root setting and understand file precedence          | [Configuration reference](configuration.md)                         |
| Use a subscription or API key; tune reasoning and context       | [Models and authentication](models-and-authentication.md)           |
| Create an Agent file or reference an existing Agent as a child  | [Agents and subagents](agents-and-subagents.md)                     |
| Choose execution permissions and configure multiple directories | [Environments and Projects](environments-and-projects.md)           |
| Add MCP servers, Skills, or plugins                             | [Extensions and MCP](extensions-and-mcp.md)                         |
| Use one-shot commands, recover work, or diagnose failures       | [Automation and troubleshooting](automation-and-troubleshooting.md) |
