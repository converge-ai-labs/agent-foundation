# Agent UI

Agent UI is an interactive coding CLI built on Agent Foundation Harness. It uses your terminal's normal scrollback, not a full-screen workbench. One foreground process owns one `AgentUiApp`, the current conversation, and its active work. There is no browser server, daemon, or detached execution mode.

```console
cd your-repository
a13n-ui
```

You can type immediately while the App prepares. Enter during startup preserves your draft rather than submitting it unexpectedly. A model request begins only when you explicitly send a prompt. The current directory is the workspace; you do not need to create or manage a Project.

## First use

Run `/setup` from the prompt, or start with `a13n-ui setup`:

1. Choose **codex**, **grok**, or **api** access.
2. For Codex, choose the model, working context budget, and reasoning effort. Sol, balanced/350k, and high are the defaults.
3. Choose **full-control** or **sandbox** permissions. Subscription setup also offers shell review.
4. Inspect the file preview, then type **yes** to publish. `/cancel` leaves setup.
5. Use `/login codex` or `/login grok` if you have not already authenticated. Existing compatible account stores are reused.

Setup creates editable YAML resources. It does not put OAuth tokens or API keys into them, call a model to test entitlement, or silently overwrite edited Model resources. A preserved existing Model keeps its existing settings even if you selected different starter values; edit its YAML to change those values. Explicitly connecting the selected Agent can update its model binding through the reviewed publication.

### Codex reasoning and context

The defaults are release-owned recommendations, not claims that every account supports every model or context size.

| Setup choice | Working context budget | When to choose it                                                                             |
| ------------ | ---------------------: | --------------------------------------------------------------------------------------------- |
| standard     |                272,000 | Conservative local budget matching the current Codex catalog default                          |
| balanced     |                350,000 | Default for repository work, aligned with the reference YAACLI configuration                  |
| extended     |                872,000 | Large tasks where your account supports the catalog maximum; expect greater latency and usage |

A **working budget** controls local reminders and compaction. It does not increase the provider's limit or grant access. The default reminder threshold is 65% and automatic compaction starts at 90%, based on the latest reported root request footprint rather than cumulative tokens. At 350k these are 227,500 and 315,000 tokens.

Reasoning choices are `low`, `medium`, `high`, and `xhigh`. `/thinking default` returns to the selected Model's configured value. High reasoning is independent of detailed display: you can use high reasoning while seeing concise output. Only provider-exposed reasoning is shown, and some providers do not return it.

Codex subscription requests do **not** receive an API output-token cap copied from YAACLI presets. The native subscription adapter strips unsupported settings such as `max_tokens`; `openai_store` is forced false.

## Everyday interaction

| Action                                              | Command or key                                   |
| --------------------------------------------------- | ------------------------------------------------ |
| Send the draft                                      | Enter                                            |
| Insert a newline                                    | Alt+Enter                                        |
| Complete a slash command or supported argument      | Tab                                              |
| Clear an idle draft; cancel active work             | Ctrl+C                                           |
| Exit from an empty draft                            | Ctrl+D                                           |
| Switch concise/detailed display                     | Ctrl+O or `/mode concise`, `/mode detailed`      |
| Explain commands                                    | `/help` or `/help command`                       |
| Start a new conversation without deleting history   | `/new`                                           |
| List recent conversations in this workspace         | `/resume`                                        |
| Resume one saved conversation                       | `/resume session-id`                             |
| Read saved messages and tool details                | `/history`, then the next-page command it prints |
| List/select configured Models                       | `/model`, `/model model-codex`, `/model default` |
| Read/change reasoning                               | `/thinking`, `/thinking low`                     |
| Read/change execution permissions                   | `/environment`, `/environment sandbox`           |
| Show current settings, usage, and pending decisions | `/status`                                        |
| Locate configuration and explain precedence         | `/config`                                        |
| Cancel active work                                  | `/cancel`                                        |
| Exit after cancelling and cleaning up active work   | `/quit` or `/exit`                               |

Bracketed multiline paste stays in the draft until Enter. Terminal support for Alt+Enter varies; terminals normally encode it as Escape followed by Enter. An unknown slash command is never sent to the model. Ordinary input entered while a Run is active is preserved, not silently steered or queued. Wait for completion or cancel first.

**Concise** output emphasizes assistant text, errors, decisions, and necessary results. **Detailed** output also shows tool calls, file-edit arguments, bounded results, child output, and exposed reasoning. A live switch affects subsequent events, not old scrollback; `/history` can display retained details in the selected mode.

The compact status bar shows the model, reasoning, last request footprint/working budget, elapsed time, state, and output mode. `?` means unavailable, not zero. The footprint is the last reported request, not an exact estimate of your next prompt. Child and auxiliary usage are not summed into it. Terminal cursor-position reporting is needed for prompt-toolkit's bottom toolbar; use `/status` if your terminal suppresses that reporting.

### Approvals and questions

Flagged shell commands and failed shell reviews require an explicit decision. Review the request ID, tool, and arguments. `/review request-id` opens its bounded retained details when the inline argument preview is truncated:

```text
/approve request-id
/deny request-id
/result request-id '{"answers":{"question-key":"selected answer"}}'
```

`/result` supplies JSON for an external tool, including a structured question. Use the displayed request schema to form the answer. It is not an approval shortcut. For multiple pending requests, answers remain local until the complete batch is ready; every answer is validated against the same continuation. Cancel or exit never approves a request. A suspended conversation can be resumed later.

## Configuration

The default root remains `~/.a13n-ui/a13n-ui.yaml`. Use `--config PATH` before a subcommand to select another tree. `--data-root PATH` selects separate local state, followed by `A13N_UI_DATA_ROOT`; the default is the configuration directory's `data/` child.

```console
a13n-ui config path
a13n-ui config show --format json
a13n-ui config validate
a13n-ui --config /path/to/a13n-ui.yaml config validate
a13n-ui doctor --format json
```

Configuration precedence is:

1. Explicit `/model` and `/thinking` selections for subsequent operations.
2. Launch selections such as `--agent` and `--environment-mode` for a new session.
3. Accepted resource YAML and root defaults.
4. Documented built-in defaults.

For display, an explicit `/mode` or `--display` takes precedence over `display.mode`. Model/reasoning slash commands do not write YAML. File edits affect later captures, never previous immutable Run snapshots. Resume restores the last selected continuation's Model ID and reasoning, resolving them against current resources. It rejects a deleted model rather than inventing a fallback.

### Explicit Codex configuration

A typical root document is:

```yaml
schema_version: "2"
process:
  pricing_auto_update: true
  log_level: INFO
  log_format: pretty
defaults:
  agent: agent-codex
  environment_profile: environment-native
display:
  mode: concise
  show_status: true
  max_tool_result_lines: 5
  max_tool_argument_chars: 8192
```

`models/codex.yaml`:

```yaml
schema_version: "1"
kind: model
id: model-codex
name: Codex coding
route: openai-codex:gpt-5.6-sol
authentication:
  kind: codex_subscription
settings:
  thinking: high
  openai_reasoning_summary: detailed
  openai_store: false
model_characteristics:
  context_window: 350000
  proactive_context_management_threshold: 0.65
  compact_threshold: 0.90
```

`agents/codex.yaml`:

```yaml
schema_version: "1"
kind: agent
id: agent-codex
name: Codex coding
model: model-codex
instructions: ""
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
```

This minimal example omits shell review. Setup enables it by default for subscriptions, adds a separate Codex Luna/low reviewer resource, and selects `ShellReviewCapability` with `risk_threshold: high`, `on_flagged: approval_required`, and `on_error: approval_required`. Review is not a filesystem sandbox.

The empty context-capability configurations use native defaults. Advanced users can set an absolute `compaction.configuration.trigger_tokens`, a `handoff.configuration.summary_reminder_tokens`, or `runtime_context.configuration.context_window_tokens`. Explicit capability values override derived defaults. Normally change the Model's `model_characteristics` instead, so all derived thresholds stay aligned.

Every Agent receives the release-owned system prompt separately from its optional `instructions`. Instructions add preferences; they do not replace the built-in system prompt. Setup does not silently enable external MCP servers or a subagent roster. Those remain advanced editable resources; inspect accepted configuration and the [configuration specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/agent-ui/01-configuration-and-resource-catalog.md) for their contracts.

### Subscription login and API keys

```console
a13n-ui auth status
a13n-ui auth login codex
a13n-ui auth login grok
a13n-ui auth login codex --browser
a13n-ui auth key list
a13n-ui auth key set key-primary
a13n-ui auth key delete key-primary
```

Device authorization is the default and needs no host callback. Open the printed URL yourself. Use `--browser` only when your browser can reach the host's loopback callback; Codex uses `http://localhost:1455/auth/callback`. There is no automatic fallback to another login method. Authorization expires within fifteen minutes. Replacing a different shared account requires `--allow-account-switch` through the CLI.

For API access, run the hidden key prompt first, then choose `api` in setup and provide `key:key-primary`. Or choose `env:OPENAI_API_KEY`; enter the variable name, not its value. The variable must exist in the Agent UI process. Never paste an API key into the normal composer.

Stored API keys are plaintext in the data root's independent `auth.json`, with private permissions. Protect the host and backups. Configuration and Run snapshots hold references, not key bytes. A completed credential save/login is independent of setup publication and is not undone by cancelling setup.

### Files, workspaces, and recovery

Model, Agent, extension, MCP, and internal Project resources live in sibling YAML directories. The first prompt creates an exact-directory internal Project if necessary; it does not rewrite an old Project to follow your current directory. Existing history is preserved. To resume a conversation from another directory, launch the CLI in its original workspace. `--resume` cannot be combined with Agent, Environment, or title overrides; resume first, then use an explicit slash command.

Setup publishes resources first and root defaults last. Multi-file publication is not a transaction: failures report completed paths. Review those paths and preview again. An interrupted replacement may retain an original in `.a13n-ui-setup-recovery-*`; inspect and restore or move it before retrying. Do not delete a competing save to force publication.

Process loss discards active receipts and incomplete input/output. Resume continues only a previously selected complete checkpoint; it does not replay interrupted side effects. Best-effort live output can be incomplete; if events are lost, the terminal labels recovery and prints the authoritative final answer.

## Execution permissions

**Full Control** runs as your host account with ambient filesystem and network authority. It does not download or launch agent-envd. The current Direct Local provider requires POSIX for shell/process execution; Windows file access does not imply Windows command execution support.

**Sandbox** uses the local Environment provider and required filesystem/process isolation with denied networking. Readiness is checked when execution needs it, not during landing. A failure is explicit and does not fall back to Full Control. Fix the prerequisite and retry, or intentionally select `/environment full-control` before sending a new prompt. Agent UI never runs `sudo`, changes sysctls, or disables required isolation for you. Windows production Sandbox isolation is not supported. See the [agent-envd operations guide](agent-envd/index.md#isolation-behavior).

## Automation and diagnostics

```console
a13n-ui run "Review the current diff"
a13n-ui run "Summarize the next step" --resume session-id --format json
a13n-ui --environment-mode sandbox run "Inspect the repository"
a13n-ui plugin list
a13n-ui import subagents --product codex --scope project --project-root .
a13n-ui --help
a13n-ui auth login --help
```

One-shot mode prints the final text or a structured operation object, then exits. It shares workspace, model, continuation, and permission semantics with interactive mode. Failed or suspended operations exit nonzero. It does not open an interactive approval prompt. Use interactive resume to answer pending decisions.

Help and version do not load provider or database modules. App initialization happens behind the editable prompt; model construction, Environment acquisition, and selected MCP connections happen only when needed. `AgentUiApp` remains the reusable application boundary for a future WebUI or other adapter; the CLI does not own a parallel execution engine.
