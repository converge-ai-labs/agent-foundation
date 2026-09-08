# Automation and troubleshooting

## Automation and diagnostics

```console
a13n-harness-ui run "Review the current diff"
a13n-harness-ui run "Summarize the next step" --resume session-id --format json
a13n-harness-ui --environment-mode sandbox run "Inspect the repository"
a13n-harness-ui plugin list
a13n-harness-ui import subagents --product codex --scope project --project-root .
a13n-harness-ui --help
a13n-harness-ui login --help
```

One-shot mode prints the final text or a structured operation object, then exits. It shares workspace, model, continuation, and permission semantics with interactive mode. Failed or suspended operations exit nonzero. It does not open an interactive approval prompt. Use interactive resume to answer pending decisions.

Help and version do not load provider or database modules. A lightweight startup view appears before execution dependencies load. Unused model-provider and memory SDKs are not imported merely to start the terminal. Startup shows runtime loading, local storage/configuration, and session preparation separately; phase timings are written to the data root’s `logs/terminal.log`. App initialization precedes chat, and startup, setup, and conversation keep the same alternate screen without a terminal reset; model construction, Environment acquisition, and selected MCP connections happen only when needed. The CLI and HTTP adapter share the reusable `HarnessUiApp` application boundary; the Hello World page does not call that API, and the CLI does not own a parallel execution engine.

If interactive startup or `--resume` fails, the terminal prints the nested exception chain and traceback locations after cleanup, including application error codes, rather than only a TaskGroup error count. A private diagnostic report retains the full exception details. Local variables, source lines, and raw provider messages are not printed; review the report for sensitive content before sharing it. Nothing is uploaded automatically.

## Model connection interruptions

Harness UI automatically continues eligible interrupted model requests from the available history, for up to five total attempts including the first. This applies to root Agents and subagents. The terminal shows a short `[System] Retrying model request…` notice rather than an error for each retry. If recovery succeeds, the same Run continues normally. If the budget is exhausted, a terminal error reports the attempt count and suggests continuing the conversation again.

Recovery does not restart completed work or directly replay tool calls. Cancellation, usage limits, tool failures, and pending approvals do not trigger this mechanism. Before manually repeating a side-effecting action after an interruption, check whether it already completed. These in-process retries do not recover a crashed process.

## Browser UI

The bundled page displays only **Hello World**. It does not authenticate, consume the URL's API-key fragment, open live streams, or provide conversation, setup, or management controls. The HTTP API and foreground server remain available independently.

```bash
a13n-harness-ui webui                       # 127.0.0.1:8765, generated per-process API key
a13n-harness-ui webui --host 127.0.0.1 --port 9000
```

Open the ordinary URL printed by the server to view the page; static assets require no API key. The server also prints a generated key and a convenience URL carrying it only in the fragment. The placeholder does not consume that fragment. API clients must send the key in `Authorization: Bearer <key>` for every API request. `--api-key` selects an explicit key; it is not echoed, but command arguments may be visible to the shell and operating system. `--dangerously-bypass-permission` disables authentication only by explicit request. A non-loopback listener is for a trusted single-user network, not a multi-user service. The server owns the App lifetime even when browsers disconnect; Ctrl+C stops the server and closes the App.

The browser assets ship inside the wheel. End users do not need Node.js or a separate frontend checkout. For repository development, run `make a13n-harness-ui-assets` before `uv run --locked a13n-harness-ui webui`.

## Source Environment Troubleshooting

After switching branches, run `make sync` (or launch with `make a13n-harness-ui`) to synchronize the locked workspace. This branch requires Pydantic AI 2.40 or newer; an older environment can fail with `cannot import name 'prices' from 'pydantic_ai'`. Do not work around this by importing upstream private modules. Installed users should upgrade `a13n-harness-ui` using the package manager that owns their environment.

## Logs, Updates, and Exit

Interactive diagnostics go to `<data-root>/logs/terminal.log` (5 MiB, three rotated backups), not the conversation or normal-screen scrollback. Skipped plugins produce one actionable notice for each unchanged path/reason; use the log for details. No legacy plugin files are removed automatically.

Model execution failures and unexpected errors also produce a private `a13n-harness-ui-error-*.json` report in the system temporary directory (`/tmp` on typical Linux installations). The failure notice links its path and the GitHub Issue form. The report collects exception chains, stack locations, component versions, and Thread/Run IDs without frame locals, source lines, or a transcript. Exception messages may still contain sensitive information: review the report before attaching it, and include reproduction steps. Nothing is uploaded automatically. Correlate the same Run ID with `terminal.log` if more context is needed; report creation never substitutes for saving conversation state.

If asyncio reports `Task was destroyed but it is pending!` without an accompanying exception, the terminal shows a warning and stays open, preserving your draft. The report includes task identity, coroutine location, suspended stack locations, and creation stack locations when available. Keeping the terminal open does not recover the lost task or confirm that active work succeeded. Check `/status`, use `/cancel` if work stops progressing, and review the report before sharing it. No work is retried automatically. Other unhandled terminal event-loop failures still exit with recovery guidance.

Startup order is **update confirmation → setup if needed → conversation**. The update prompt, setup, and conversation replace the view inside the same TUI rather than exiting and reopening it or appending notices to your terminal. Installed release builds check public PyPI metadata with a three-second timeout and a daily cache; offline failure silently continues startup. Both ordinary launch and `a13n-harness-ui setup` follow this order.

Update detection is enabled by default. To disable it in `a13n-harness-ui.yaml`:

```yaml
process:
  terminal_update_check: false
```

Use `a13n-harness-ui --no-update-check` to skip detection for one invocation. `make a13n-harness-ui` always disables it for repository development. Development versions, help/version, and noninteractive commands also skip detection.

**Startup never installs without confirmation.** For a recognized uv-tool installation, the prompt shows the command and tool directory, with **Update now** and **Not now** (the default). Choosing Not now, Escape, or Ctrl+C at this prompt continues startup. If a newer version remains available, the next enabled launch asks again. Choosing Update now closes the App and TUI before running the installer, then asks you to restart; an installer failure is reported without retry or continuing setup. Other installation methods receive manual instructions rather than a guessed update command. To update immediately without waiting for the next startup check:

```console
a13n-harness-ui update
```

The command itself requests installation, so it does not ask for another confirmation or open chat/setup. It runs `uv tool upgrade a13n-harness-ui` against the running installation's tool directory, bypassing the startup metadata cache. Disabling startup detection does not disable this command. uv must be available on PATH; unsupported installations fail with instructions to use their original package manager. uv owns package resolution and network errors. Failures return the installer status without retry; an interrupted update exits with status 130 and asks you to check the installation before retrying. Restart Harness UI after success. You can also run `uv tool upgrade a13n-harness-ui` directly.

After cleanup, the normal terminal shows a resume command for the actual saved root thread, preserving explicit configuration/data-root options and identifying the workspace to run it from. Failed and interrupted operations save an available valid Harness checkpoint, including safely retained partial text, before releasing the operation. The next turn and `--resume` use that selected checkpoint without automatically replaying tools. If state export or storage fails, or the process is killed before cleanup can run, resume uses the previous saved checkpoint.

Large active messages use a lightweight plain-text preview and reflow to Markdown when complete. Rendered rows are loaded in pages as you scroll, rather than dropping older rows at a fixed viewport limit. The source cache is still bounded; explicit eviction notices direct you to `/history`. That command can only recover content retained and exposed by the App, not data omitted upstream.

## Command reference

Put global options before the subcommand. Use `--help` at each level for exact argument parsing.

| Command                                                  | Options / behavior                                                                                                                                                           |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a13n-harness-ui`                                        | Interactive launch; `--config`, `--data-root`, `--resume`, `--agent`, `--environment-mode`, `--environment-profile`, `--display`, `--no-update-check`, `--help`, `--version` |
| `run PROMPT`                                             | One-shot; `--resume`, `--agent`, `--environment-mode`, `--environment-profile`, `--title`, `--format text\|json`                                                             |
| `update`                                                 | Immediately upgrade the running uv-tool installation; no startup check or second confirmation                                                                                |
| `setup`                                                  | Guided configuration outside chat; no in-chat `/setup`                                                                                                                       |
| `config path\|show\|validate\|subagents`                 | Read-only inspection; `--format text\|json`                                                                                                                                  |
| `environment list`, `doctor`                             | Inspection/readiness diagnostics; `--format text\|json`                                                                                                                      |
| `auth status [PROVIDER]`                                 | Inspect all or one of `codex`, `grok`; `--format`                                                                                                                            |
| `login PROVIDER`                                         | `--device-code` (default) or `--browser`, `--allow-account-switch`, `--format`                                                                                               |
| `auth logout PROVIDER`                                   | Explicit shared-account logout; `--format`                                                                                                                                   |
| `auth key list`, `auth key set ID`, `auth key delete ID` | Stored-key management; set uses hidden input, delete confirms; only list accepts `--format`                                                                                  |
| `import subagents`                                       | Required `--product claude-code\|cursor\|codex`, `--scope user\|project`; optional `--project-root`, `--user-home`, `--apply`, `--format`                                    |
| `plugin install REPOSITORY`                              | Optional `--plugin ID`, `--ref REF`, `--format`; repository must expose valid content                                                                                        |
| `plugin list`, `plugin uninstall ID`                     | Content Plugin management; `--format`                                                                                                                                        |
| `webui`                                                  | `--host`, `--port`, `--api-key`, `--dangerously-bypass-permission`; foreground server                                                                                        |

`--resume` cannot combine with Agent, Environment, or title overrides. Environment mode and profile options cannot combine. There is no detached execution, merge/deploy command, or hidden background continuation after the foreground App closes.

## Diagnose without losing your draft

| Symptom                                 | Next step                                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| A file edit seems ignored               | Run `config validate`, then `config show`; the previous accepted generation remains active if a new save is invalid             |
| An Agent cannot run                     | Check its `model` reference, selected capability keys, roster IDs, and exact tool names                                         |
| A local Markdown child rejects `model`  | Remove the field; for independent settings create an Agent and use `- agent: agent-id`                                          |
| Two children share a name               | Remove one selection or rename the custom Markdown child; built-ins do not override custom roles                                |
| Sandbox readiness fails                 | Read `doctor`; fix prerequisites or explicitly choose Full Control, never assume fallback                                       |
| A request was rejected                  | Use the restored draft or `/recover`; do not assume it was sent                                                                 |
| An operation suspended in one-shot mode | Resume interactively and complete its pending decisions                                                                         |
| A process crashed                       | Inspect the private diagnostic path, review it for sensitive exception messages, and use the printed saved-state resume command |

Unsent input and in-flight side effects are not recoverable merely because a saved session exists. A diagnostic report contains no locals or transcript and is never uploaded automatically. Review it before sharing.
