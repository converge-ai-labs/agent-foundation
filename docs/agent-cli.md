# Agent CLI

Agent CLI (`a13n-ui`, installed from the `a13n-ui` distribution) is an interactive coding CLI built on Agent Foundation Harness. Its native full-terminal interface supports Windows, macOS, and Linux, with reflowing Markdown, selectable interactions, and image drafts. One foreground process owns one `AgentUiApp`, the current conversation, and its active work. The optional `a13n-ui webui` command starts the HTTP API and a bundled Hello World page in a foreground server; browser chat and management are not implemented. There is no detached daemon or detached execution mode.

```console
uv tool install a13n-ui
cd your-repository
a13n-ui
```

Startup checks local configuration before opening full-terminal chat. If no Model is configured, a standalone setup wizard opens in the normal terminal first. A model request begins only when you explicitly send a prompt. The current directory is the workspace; you do not need to create or manage a Project.

## First use

Setup runs automatically when needed. To change configuration later, leave chat and run `a13n-ui setup`; there is no `/setup` command inside chat.

1. **Connect a model:** choose Codex subscription, Grok subscription, or an API key. Existing compatible Codex/Grok logins are detected and reused without another login prompt, including credentials that can refresh when used. API-key access asks for a model route and an environment-variable or stored-key reference, never the raw key.
2. **Choose execution permissions:** Full Control runs as your host account; Sandbox uses isolated execution and checks prerequisites before saving. There is no automatic fallback between them.
3. **Review and save:** confirm the connection, starter settings, workspace, permissions, and files to publish. Codex defaults to Sol, high reasoning, a 350k working budget, and enabled shell review. Choose **Adjust model options** only if you want to change the model, context budget, reasoning, shell review, or additional instructions. Existing edited resources are preserved.

Use Up/Down and Enter, or type option numbers. Esc goes back; Ctrl+C or Ctrl+D cancels. Cancelling first-use setup returns to the command shell without opening chat. After successful first-use setup, chat opens automatically. Running `a13n-ui setup` explicitly returns to the command shell after saving or cancelling.

If the selected account is missing, setup offers explicit device sign-in or configuration without signing in. Unsupported or malformed stores show repair guidance and a recheck action; they are not overwritten. Discovery never refreshes tokens or starts authentication. Cancelling setup does not undo a completed login or configuration publication.

Subagent migration is separate: use `/import` in chat to select Codex or Claude Code definitions, project/user scope, and an explicit import-and-enable confirmation. Setup does not scan or import external definitions.

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

### Local guidance

Agent UI loads two guidance sources: `AGENTS.md` beside the selected `a13n-ui.yaml` (normally `~/.a13n-ui/AGENTS.md`), and `AGENTS.md` in the current working directory. Both are user-role contextual content, not the provider's `instructions` field. No ancestor scan or `RULES.md` / `AGENTS.override.md` fallback is performed. Global guidance is captured with each accepted configuration generation; working-directory guidance uses the Environment's bounded file reader. Injected guidance is hidden in the terminal and `/history` using application-only `display: false` metadata, while remaining available to the model and retained native history.

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
| Send additional guidance to the current Run         | Enter while running                              |
| Cancel active work                                  | `/cancel`                                        |
| Exit after cancelling and cleaning up active work   | `/quit` or `/exit`                               |

Bracketed multiline paste stays in the draft until Enter. Terminal support for Alt+Enter varies; terminals normally encode it as Escape followed by Enter. An unknown slash command is never sent to the model.

**Enter sends a message while idle, or adds text guidance while the agent is running.** The input hint changes with the current state. `/steer <message>` is still available as an optional explicit alternative. Guidance is appended through the current Harness Run's native input queue; the CLI does not reorder messages or maintain a separate next-turn queue. Acceptance is distinct from delivery at a model boundary, and neither promises an immediate interruption of an in-flight tool or request.

Preparing, cancelling, or completed operations do not accept guidance. Rejected or unconfirmed input stays in the draft, or is available through `/recover` if you have started another draft. It is never silently redirected to a new Run or resent automatically. Active-run Enter with images preserves the entire draft because this CLI steering path accepts text only. In an approval or question selector, Enter confirms that interaction instead.

Commands preserve Windows backslashes. Quote paths containing spaces, such as `/attach "C:\\My Photos\\image.png"`. `/result request-id {"answer": "two words"}` takes raw JSON without an extra shell-quoting layer.

**Concise** keeps provider-exposed thinking independently expanded. Successful edit and multi-edit operations show expanded diffs from the native `FileEditAppliedEvent`, using the actual before/after file contents rather than proposed replacement snippets. Failed and no-op edits produce no applied-edit panel. Ordinary tool arguments and results remain folded; their full source is retained within the transcript display budget rather than replaced with a preview. **Detailed** expands retained tool blocks and shows subsequent child output. Ctrl+O switches without replaying events; `/history` reads saved details regardless of mode.

Summarize displays its body from the native `HandoffSummaryEvent` after the handoff summary has been persisted. This is the **prepared** state, distinct from **completed** when the handoff is consumed at the next boundary. The full available summary and file reminders are expanded, with lifecycle status separate from the body.

Compaction displays its generated summary in an independently expanded Markdown block, correlated to the operation ID. The body comes from the native custom event, not inferred saved history or an assistant answer. Lifecycle metadata remains separate; neither summary visibility nor a completed lifecycle event asserts that a new continuation has been saved.

The status bar prioritizes state, model, reasoning effort, `ctx N%`, and elapsed time. Context percentage uses the last reported root request divided by your configured working budget. `--` means unavailable, not zero; genuine zero is `0%`. Child and auxiliary usage are not summed into it. `/status` shows exact counters and complete settings even in a narrow terminal.

### Local host commands

Enter `!command` to run a command yourself on the local POSIX host, for example `!git status`. This is user-owned shell execution in the CLI's working directory with the host process environment, **outside the model's selected Environment and Sandbox**. Selecting Sandbox for model tools does not sandbox `!command`. The command and its output are not injected into model context.

Local commands are accepted only while idle and outside interaction menus. A busy-state or menu rejection preserves the draft and attached images. Stdout and stderr are displayed as output events, followed by exit status and elapsed time. Commands are noninteractive: stdin receives EOF. Each command has a 120-second deadline and a combined 256 KiB output display limit; output beyond that limit is still drained rather than allowed to block the process.

Ctrl+C or `/cancel` terminates the owned process group and waits for cleanup before returning to idle. `!command` is explicitly unsupported on Windows until equivalent process-group cleanup is available; ordinary Windows CLI use and model tools remain supported under their existing execution contracts.

### Approvals and questions

Flagged shell commands and failed shell reviews open a selectable prompt. Inspect the tool, request, arguments, and review details before choosing **Approve once** or **Deny**. No approval is preselected. The Inspect action and `/review request-id` read retained details when the preview is truncated. Ordinary free text cannot approve a shell request.

Structured questions support single choice, multiple choice with Space, and typed answers. Complete option labels and descriptions remain scrollable above the compact selector, including in narrow terminals. Answers stay local until the complete batch is ready and are submitted against the exact continuation. `/cancel` discards local answers without approving anything; `/status` reopens pending decisions. Resuming a suspended conversation also reopens them. Advanced `/approve`, `/deny`, and `/result` commands are available outside an active selector; use `/cancel` first to leave that selector.

### Theme, scrolling, copy, and images

- `/theme auto|dark|light` changes UI and Markdown syntax colors for this session; `display.theme` sets the file default. Auto preserves your terminal foreground/background and ANSI palette; passive metadata selects syntax variants without consuming input.
- PageUp/PageDown scroll the bounded display history; Ctrl+End returns to following live output. `/history` retrieves durable pages after display eviction.
- Scroll mode (`/mouse on`) is the default. Wheel events belong to the hovered transcript, selector, or composer. In selectors, scrolling only browses; click highlights and Enter confirms. Ctrl+Space switches selector/composer keyboard focus. Esc closes an interaction/completion first, otherwise toggles scroll/select. Select mode (`/mouse off`) restores native selection/copy; application wheel routing is unavailable there. PageUp/PageDown and Ctrl+End work in either mode. Scrolling up freezes follow; returning to the bottom resumes it. Code rendering avoids padded backgrounds and OSC hyperlinks.
- Ctrl+V, Alt+V, or `/paste-image` explicitly reads clipboard images. Normal text paste remains text and never submits itself. Some terminals intercept Ctrl+V; use Alt+V or the command there.
- `/attach "path/to/image.png"` is the portable fallback. Linux clipboard images need `wl-paste` or `xclip`; no helper is installed automatically.
- Chips show draft images. `/remove 1` removes one; `/remove all` or idle Ctrl+C clears them. Up to eight validated PNG/JPEG/WebP/GIF images are accepted, with 10 MiB per image, 20 MiB total, and 32 megapixels per image.
- Image-only prompts are supported. The selected model must support the submitted modality; failures never silently drop images or switch models. A failed pre-admission send restores its draft, or exposes `/recover` if you have already begun another draft. It is never resent automatically. A rejected `/new` or `/resume` also preserves attachments; failed commands restore their text or expose `/recover` without overwriting a newer draft.

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
  terminal_update_check: true
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

### Files, Projects, and recovery

Model, Agent, extension, MCP, and Project resources live in sibling YAML directories. A Project contains an ordered list of directories; the first is its default working directory. Launching the CLI in that first directory uses the same Project and all its roots. For example, a Project with roots `[code, notes]` is entered from `code`; adding `notes` later does not create a new Project or hide existing CLI sessions.

The first prompt creates a single-root Project only when no Project's first directory matches. It never adopts a parent Project, treats a secondary root as another entry point, or rewrites an existing Project. Multiple matching Projects require resuming a specific session or editing their roots. To resume a conversation, launch the CLI in its Project's first directory. `--resume` cannot be combined with Agent, Environment, or title overrides; resume first, then use an explicit slash command.

Setup publishes resources first and root defaults last. Multi-file publication is not a transaction: failures report completed paths. Review those paths and preview again. An interrupted replacement may retain an original in `.a13n-ui-setup-recovery-*`; inspect and restore or move it before retrying. Do not delete a competing save to force publication.

Process loss discards active receipts and incomplete input/output. Resume continues only a previously selected complete checkpoint; it does not replay interrupted side effects. Best-effort live output can be incomplete; if events are lost, the terminal labels recovery and prints the authoritative final answer.

## Execution permissions

**Full Control** runs as your host account with ambient filesystem and network authority. It does not download or launch agent-envd. Direct Local executes natively on Linux, macOS, and Windows without WSL. Windows uses PowerShell (`pwsh`, then Windows PowerShell), UTF-8 text streams, and a Job Object that owns the command's descendants. Cancellation, timeout, and exit clean up that owned tree. Portable interrupt signals are unavailable on Windows; cancellation uses tree termination instead. Full Control remains host-account execution, not a sandbox.

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

Help and version do not load provider or database modules. App initialization precedes chat, with setup owning a single redrawn alternate-screen view; model construction, Environment acquisition, and selected MCP connections happen only when needed. The CLI and HTTP adapter share the reusable `AgentUiApp` application boundary; the Hello World page does not call that API, and the CLI does not own a parallel execution engine.

## Browser UI

The bundled page displays only **Hello World**. It does not authenticate, consume the URL's API-key fragment, open live streams, or provide conversation, setup, or management controls. The HTTP API and foreground server remain available independently.

```bash
a13n-ui webui                       # 127.0.0.1:8765, generated per-process API key
a13n-ui webui --host 127.0.0.1 --port 9000
```

Open the ordinary URL printed by the server to view the page; static assets require no API key. The server also prints a generated key and a convenience URL carrying it only in the fragment. The placeholder does not consume that fragment. API clients must send the key in `Authorization: Bearer <key>` for every API request. `--api-key` selects an explicit key; it is not echoed, but command arguments may be visible to the shell and operating system. `--dangerously-bypass-permission` disables authentication only by explicit request. A non-loopback listener is for a trusted single-user network, not a multi-user service. The server owns the App lifetime even when browsers disconnect; Ctrl+C stops the server and closes the App.

The browser assets ship inside the wheel. End users do not need Node.js or a separate frontend checkout. For repository development, run `make agent-ui-assets` before `uv run --locked a13n-ui webui`.

## Windows Local Execution

Windows supports **Full Control only** for the built-in local modes. Setup and the CLI Environment selector offer Full Control and explain that commands run with the Host account's filesystem and network permissions. Job Object cleanup is not Sandbox isolation. Explicit Sandbox requests fail without downloading envd, changing saved selections, or falling back. Custom and remote Providers retain their own contracts.

## Source Environment Troubleshooting

After switching branches, run `make sync` (or launch with `make a13n-ui`) to synchronize the locked workspace. This branch requires Pydantic AI 2.40 or newer; an older environment can fail with `cannot import name 'prices' from 'pydantic_ai'`. Do not work around this by importing upstream private modules. Installed users should upgrade `a13n-ui` using the package manager that owns their environment.

## Logs, Updates, and Exit

Interactive diagnostics go to `<data-root>/logs/terminal.log` (5 MiB, three rotated backups), not the conversation or normal-screen scrollback. Skipped plugins produce one actionable notice for each unchanged path/reason; use the log for details. No legacy plugin files are removed automatically.

Startup order is **update confirmation → setup if needed → conversation**. The update prompt and setup redraw the same TUI rather than appending notices to your terminal. Installed release builds check public PyPI metadata with a three-second timeout and a daily cache; offline failure silently continues startup. Both ordinary launch and `a13n-ui setup` follow this order.

Update detection is enabled by default. To disable it in `a13n-ui.yaml`:

```yaml
process:
  terminal_update_check: false
```

Use `a13n-ui --no-update-check` to skip detection for one invocation. `make a13n-ui` always disables it for repository development. Development versions, help/version, and noninteractive commands also skip detection.

**Nothing is installed without confirmation.** For a recognized uv-tool installation, the prompt shows the command and tool directory, with **Update now** and **Not now** (the default). Choosing Not now, Escape, or Ctrl+C at this prompt continues startup. If a newer version remains available, the next enabled launch asks again. Choosing Update now closes the App and TUI before running the installer, then asks you to restart; an installer failure is reported without retry or continuing setup. Other installation methods receive manual instructions rather than a guessed update command. You can also update a uv-tool installation yourself:

```console
uv tool upgrade a13n-ui
```

After cleanup, the normal terminal shows a resume command for the actual saved root thread, preserving explicit configuration/data-root options and identifying the workspace to run it from. An interrupted operation may not have produced a new resumable continuation; resume uses the last saved one.

Large active messages use a lightweight plain-text preview and reflow to Markdown when complete. Rendered rows are loaded in pages as you scroll, rather than dropping older rows at a fixed viewport limit. The source cache is still bounded; explicit eviction notices direct you to `/history`. That command can only recover content retained and exposed by the App, not data omitted upstream.
