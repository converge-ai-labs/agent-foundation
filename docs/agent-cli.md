# Agent CLI

Agent CLI (`a13n-cli`, installed from the `a13n-ui` distribution) is an interactive coding CLI built on Agent Foundation Harness. Its native full-terminal interface supports Windows, macOS, and Linux, with reflowing Markdown, selectable interactions, and image drafts. One foreground process owns one `AgentUiApp`, the current conversation, and its active work. There is no browser server, daemon, or detached execution mode.

```console
cd your-repository
a13n-cli
```

You can type immediately while the App prepares. Enter during startup preserves your draft rather than submitting it unexpectedly. A model request begins only when you explicitly send a prompt. The current directory is the workspace; you do not need to create or manage a Project.

## First use

Missing model configuration opens setup automatically and preserves anything you typed during startup. `/setup` or `a13n-cli setup` reopens it:

1. **Connect a model:** choose BYOS (Codex/Grok subscription) or BYOK (an API route and an environment-variable or stored-key reference). Never paste a raw secret into the composer. Codex defaults to Sol, high reasoning, and a balanced 350k working budget.
2. **Configure the coding Agent:** choose Full Control or Sandbox, optional subscription shell review, and optional additional Agent instructions. Built-in system instructions remain active. Preview files and explicitly choose Publish. Existing edited resources are preserved.
3. **Optional BYOS follow-up:** sign in now or later, then optionally migrate Codex or Claude Code subagents. Select product, project/user scope, definitions, and explicit import-and-enable confirmation. Skip does not scan external files. `/import` makes the same flow available later, including for BYOK.

Use Up/Down and Enter, or type option numbers. Space toggles multiple selections. Esc returns to the previous setup question; `/cancel` restores the conversation draft. Publication confirmation never defaults to approval. After configuration has been published, cancelling login or import does not undo those files.

Imports preserve instructions and explicitly inherit the parent model and visible tools rather than activating foreign tool names. Preview lists unsupported settings and conflicts. Successful import enrolls selected definitions in the selected Agent's roster; file publication and enrollment are separate operations, and partial completion is reported for deliberate retry.

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
| Send additional guidance to the current Run         | `/steer message`                                 |
| Cancel active work                                  | `/cancel`                                        |
| Exit after cancelling and cleaning up active work   | `/quit` or `/exit`                               |

Bracketed multiline paste stays in the draft until Enter. Terminal support for Alt+Enter varies; terminals normally encode it as Escape followed by Enter. An unknown slash command is never sent to the model. Ordinary input entered while a Run is active is preserved, not silently steered or queued. Use `/steer <message>` for explicit text-only guidance to the current receipt, or wait for completion/cancel first. Acceptance means the input was queued inside the current Harness Run for a model boundary, not that the model has already consumed it. A preparing or completed receipt rejects steering; the CLI preserves rejected guidance and never resends it as a new prompt.

Commands preserve Windows backslashes. Quote paths containing spaces, such as `/attach "C:\\My Photos\\image.png"`. `/result request-id {"answer": "two words"}` takes raw JSON without an extra shell-quoting layer.

**Concise** output emphasizes assistant text, errors, decisions, and necessary results. **Detailed** output also shows tool calls, file-edit arguments, bounded results, child output, and exposed reasoning. Tool results include call identity and elapsed time; child blocks include their execution or Run identity. A live switch affects subsequent events, not previously displayed blocks; `/history` can display retained details in the selected mode.

The compact status bar shows the model, reasoning, last request footprint/working budget, elapsed time, state, and output mode. `?` means unavailable, not zero. The footprint is the last reported request, not an exact estimate of your next prompt. Child and auxiliary usage are not summed into it. `/status` shows complete details even in a narrow terminal.

### Approvals and questions

Flagged shell commands and failed shell reviews open a selectable prompt. Inspect the tool, request, arguments, and review details before choosing **Approve once** or **Deny**. No approval is preselected. The Inspect action and `/review request-id` read retained details when the preview is truncated. Ordinary free text cannot approve a shell request.

Structured questions support single choice, multiple choice with Space, and typed answers. Complete option labels and descriptions remain scrollable above the compact selector, including in narrow terminals. Answers stay local until the complete batch is ready and are submitted against the exact continuation. `/cancel` discards local answers without approving anything; `/status` reopens pending decisions. Resuming a suspended conversation also reopens them. Advanced `/approve`, `/deny`, and `/result` commands are available outside an active selector; use `/cancel` first to leave that selector.

### Theme, scrolling, copy, and images

- `/theme auto|dark|light` changes UI and Markdown syntax colors for this session; `display.theme` sets the file default. Auto uses passive terminal metadata, never an interactive terminal query.
- PageUp/PageDown scroll the bounded display history; Ctrl+End returns to following live output. `/history` retrieves durable pages after display eviction.
- `/mouse on` captures wheel scrolling; `/mouse off` (the default) leaves native terminal selection/copy available. Code rendering avoids padded backgrounds and OSC hyperlinks.
- Ctrl+V, Alt+V, or `/paste-image` explicitly reads clipboard images. Normal text paste remains text and never submits itself. Some terminals intercept Ctrl+V; use Alt+V or the command there.
- `/attach "path/to/image.png"` is the portable fallback. Linux clipboard images need `wl-paste` or `xclip`; no helper is installed automatically.
- Chips show draft images. `/remove 1` removes one; `/remove all` or idle Ctrl+C clears them. Up to eight validated PNG/JPEG/WebP/GIF images are accepted, with 10 MiB per image, 20 MiB total, and 32 megapixels per image.
- Image-only prompts are supported. The selected model must support the submitted modality; failures never silently drop images or switch models. A failed pre-admission send restores its draft, or exposes `/recover` if you have already begun another draft. It is never resent automatically. A rejected `/new` or `/resume` also preserves attachments; failed commands restore their text or expose `/recover` without overwriting a newer draft.

## Configuration

The default root remains `~/.a13n-ui/a13n-ui.yaml`. Use `--config PATH` before a subcommand to select another tree. `--data-root PATH` selects separate local state, followed by `A13N_UI_DATA_ROOT`; the default is the configuration directory's `data/` child.

```console
a13n-cli config path
a13n-cli config show --format json
a13n-cli config validate
a13n-cli --config /path/to/a13n-ui.yaml config validate
a13n-cli doctor --format json
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
  theme: auto
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

Every Agent receives the release-owned system prompt separately from its optional `instructions`. Instructions add preferences; they do not replace the built-in system prompt. Setup does not silently enable external MCP servers or subagents. Optional migration requires explicit import-and-enable confirmation; other selections remain editable resources; inspect accepted configuration and the [configuration specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/agent-cli/01-configuration-and-resource-catalog.md) for their contracts.

### Subscription login and API keys

```console
a13n-cli auth status
a13n-cli auth login codex
a13n-cli auth login grok
a13n-cli auth login codex --browser
a13n-cli auth key list
a13n-cli auth key set key-primary
a13n-cli auth key delete key-primary
```

Device authorization is the default and needs no host callback. Open the printed URL yourself. Use `--browser` only when your browser can reach the host's loopback callback; Codex uses `http://localhost:1455/auth/callback`. There is no automatic fallback to another login method. Authorization expires within fifteen minutes. Replacing a different shared account requires `--allow-account-switch` through the CLI.

For API access, run the hidden key prompt first, then choose `api` in setup and provide `key:key-primary`. Or choose `env:OPENAI_API_KEY`; enter the variable name, not its value. The variable must exist in the Agent UI process. Never paste an API key into the normal composer.

Stored API keys are plaintext in the data root's independent `auth.json`, with private permissions. Protect the host and backups. Configuration and Run snapshots hold references, not key bytes. A completed credential save/login is independent of setup publication and is not undone by cancelling setup.

### Files, workspaces, and recovery

Model, Agent, extension, MCP, and internal Project resources live in sibling YAML directories. The first prompt creates an exact-directory internal Project if necessary; it does not rewrite an old Project to follow your current directory. Existing history is preserved. To resume a conversation from another directory, launch the CLI in its original workspace. `--resume` cannot be combined with Agent, Environment, or title overrides; resume first, then use an explicit slash command.

Setup publishes resources first and root defaults last. Multi-file publication is not a transaction: failures report completed paths. Review those paths and preview again. An interrupted replacement may retain an original in `.a13n-ui-setup-recovery-*`; inspect and restore or move it before retrying. Do not delete a competing save to force publication.

Process loss discards active receipts and incomplete input/output. Resume continues only a previously selected complete checkpoint; it does not replay interrupted side effects. Best-effort live output can be incomplete; if events are lost, the terminal labels recovery and prints the authoritative final answer.

## Execution permissions

**Full Control** runs as your host account with ambient filesystem and network authority. It does not download or launch agent-envd. Direct Local executes natively on Linux, macOS, and Windows without WSL. Windows uses PowerShell (`pwsh`, then Windows PowerShell), UTF-8 text streams, and a Job Object that owns the command's descendants. Cancellation, timeout, and exit clean up that owned tree. Portable interrupt signals are unavailable on Windows; cancellation uses tree termination instead. Full Control remains host-account execution, not a sandbox.

**Sandbox** uses the local Environment provider and required filesystem/process isolation with denied networking. Readiness is checked when execution needs it, not during landing. A failure is explicit and does not fall back to Full Control. Fix the prerequisite and retry, or intentionally select `/environment full-control` before sending a new prompt. Agent UI never runs `sudo`, changes sysctls, or disables required isolation for you. Windows production Sandbox isolation is not supported. See the [agent-envd operations guide](agent-envd/index.md#isolation-behavior).

## Automation and diagnostics

```console
a13n-cli run "Review the current diff"
a13n-cli run "Summarize the next step" --resume session-id --format json
a13n-cli --environment-mode sandbox run "Inspect the repository"
a13n-cli plugin list
a13n-cli import subagents --product codex --scope project --project-root .
a13n-cli --help
a13n-cli auth login --help
```

One-shot mode prints the final text or a structured operation object, then exits. It shares workspace, model, continuation, and permission semantics with interactive mode. Failed or suspended operations exit nonzero. It does not open an interactive approval prompt. Use interactive resume to answer pending decisions.

Help and version do not load provider or database modules. App initialization happens behind the editable prompt; model construction, Environment acquisition, and selected MCP connections happen only when needed. `AgentUiApp` remains the reusable application boundary for a future WebUI or other adapter; the CLI does not own a parallel execution engine.
