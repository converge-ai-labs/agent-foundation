---
title: Automation and troubleshooting
description: Script Harness UI runs, trace execution, and diagnose connection, WebUI, and Environment problems.
---

## Automation and diagnostics

```console
a13n-harness-ui run "Review the current diff"
a13n-harness-ui run "Summarize the next step" --resume thread-id --format json
a13n-harness-ui --environment-mode sandbox run "Inspect the repository"
a13n-harness-ui plugin list
a13n-harness-ui import subagents --product codex --scope project --project-root .
a13n-harness-ui --help
a13n-harness-ui login --help
```

One-shot mode prints the final text or a structured operation object, then exits. It uses the same Project selection (from the current directory), Model resolution, continuation, and permission rules as interactive mode. It does not use the Model remembered by `/model`. Failed or suspended operations exit nonzero. It does not open an interactive approval prompt. Use interactive resume to answer pending decisions.

Help and version do not start the App. The landing TUI opens first, then initializes local configuration and storage. Models, Environments and MCP connect on demand. Startup timings go to `<data-root>/logs/terminal.log`.

If startup or `--resume` fails, read the displayed error chain and diagnostic-report path. Review the private report for sensitive content before sharing it; nothing is uploaded automatically.

## Execution tracing

See [Tracing Harness UI](observation.md) for automatic OTLP export, Langfuse and Logfire profiles, embedding with an existing provider, and the separate `dev/harness-ui/.env` used by `make cli`. Traces complement diagnostics and saved conversation state; they do not replace them.

## Long-running work

Root Agents and subagents have no fixed Harness request-count limit. You can cancel them, and provider or child limits still apply. Long Runs may use more tokens; a process crash does not resume active work automatically.

## Model connection interruptions

Eligible transient model interruptions retry from available history, up to five consecutive attempts including the first. A successful primary response resets the budget; permanent errors stop. The TUI shows `[System] Retrying model request…` while the same Run continues. Retries do **not** replay completed tool calls or recover a crashed process. Check whether a side-effecting action completed before repeating it manually.

## WebUI

See [WebUI](webui.md) for collaboration, listener/authentication options, API-key retention, container delivery, and foreground lifecycle.

## Source checkout dependencies

After switching branches, run `make sync` (or launch with `make a13n-harness-ui`) to install the dependencies locked for that branch. A virtual environment from another branch can contain incompatible dependencies and fail during import. Use the committed lockfile rather than patching private dependency imports or upgrading individual libraries independently. Installed users should upgrade `a13n-harness-ui` using the package manager that owns their installation.

## Logs, Updates, and Exit

Interactive diagnostics go to `<data-root>/logs/terminal.log` (5 MiB, three rotated backups), not the conversation or normal-screen scrollback. Skipped Content Plugins produce one actionable notice for each unchanged path and reason; use the log for details. Harness UI does not remove files in the legacy Content Plugin layout.

Model execution failures and unexpected errors also produce a private `a13n-harness-ui-error-*.json` report in the system temporary directory (`/tmp` on typical Linux installations). The failure notice links its path and the GitHub Issue form. The report collects exception chains, stack locations, component versions, and Thread/Run IDs without frame locals, source lines, or a transcript. Exception messages may still contain sensitive information: review the report before attaching it, and include reproduction steps. Nothing is uploaded automatically. Correlate the same Run ID with `terminal.log` if more context is needed; report creation never substitutes for saving conversation state.

If asyncio reports `Task was destroyed but it is pending!` without an accompanying exception, the TUI shows a warning and stays open, preserving your draft. The report includes the asyncio task identity, coroutine location, suspended stack locations, and creation stack locations when available. Keeping the TUI open does not recover the lost asyncio task or confirm that active work succeeded. Check `/status`, use `/cancel` if work stops progressing, and review the report before sharing it. No work is retried automatically. Other unhandled TUI event-loop failures still exit with recovery instructions.

Startup order is **update confirmation → setup if needed → conversation**. The update prompt, setup, and conversation replace the view inside the same TUI rather than exiting and reopening it or appending notices to your terminal. Installed release builds check public PyPI metadata with a three-second timeout and a daily cache; offline failure silently continues startup. Both ordinary launch and `a13n-harness-ui setup` follow this order.

Update detection is enabled by default. To disable it in `a13n-harness-ui.yaml`:

```yaml title="a13n-harness-ui.yaml"
process:
  terminal_update_check: false
```

Use `a13n-harness-ui --no-update-check` to skip detection for one invocation. `make a13n-harness-ui` always disables it for repository development. Development versions, help/version, and noninteractive commands also skip detection.

**Startup never installs without confirmation.** For a recognized uv-tool installation, the prompt shows the command and tool directory, with **Update now** and **Not now** (the default). Choosing Not now, Escape, or Ctrl+C at this prompt continues startup. If a newer version remains available, the next enabled launch asks again. Choosing Update now closes the App and TUI before running the installer, then asks you to restart; an installer failure is reported without retry or continuing setup. Other installation methods receive manual instructions rather than a guessed update command. To update immediately without waiting for the next startup check:

```console
a13n-harness-ui update
```

The command itself requests installation, so it does not ask for another confirmation or open the TUI or setup. It runs `uv tool upgrade a13n-harness-ui` against the running installation's tool directory, bypassing the startup metadata cache. Disabling startup detection does not disable this command. uv must be available on PATH; unsupported installations fail with instructions to use their original package manager. uv owns package resolution and network errors. Failures return the installer status without retry; an interrupted update exits with status 130 and asks you to check the installation before retrying. Restart Harness UI after success. You can also run `uv tool upgrade a13n-harness-ui` directly.

After cleanup, your terminal shows a resume command for the saved conversation (its root Thread). The command keeps explicit configuration and data-root options and identifies the working directory to run it from. Failed and interrupted operations save an available valid Harness checkpoint, including safely retained partial text, before releasing the operation. The next turn and `--resume` use that selected checkpoint. Recorded tool results are not replayed, and an earlier approval does not authorize replay. Unanswered calls follow the Harness default [`tool_recovery="declared"` policy](../a13n-harness/state-and-resume.md#resume-unanswered-tool-calls): currently declared retryable tools may execute again under fresh permissions and approval requirements. Accepted deferred results saved alongside the checkpoint remain available until native history incorporates them; this is not a guarantee that every admitted input was saved. If state export or storage fails, or the process is killed before cleanup can run, resume uses the previous saved checkpoint.

Large active messages use a lightweight plain-text preview and reflow to Markdown when complete. Rendered rows are loaded in pages as you scroll, rather than dropping older rows at a fixed viewport limit. The source cache is still bounded; explicit eviction notices direct you to `/history`. That command can only recover content retained and exposed by the App, not data omitted upstream.

## Checkpoint retention and upgrades

Harness UI saves recovery checkpoints before model requests. Each Run remembers its latest checkpoint and removes only its own previous checkpoint after a successful replacement. Its final checkpoint remains available across later Runs. Existing historical objects are not automatically cleaned up.

Harness UI supports one-way database upgrades, not schema downgrades. Use forward repair or restore a complete pre-upgrade backup with the matching application version; do not point an older release at an upgraded data root.

Before upgrading from a release with output comments, stop every Harness UI App sharing the data root and back up the complete data root. Upgrade all of those Apps before restarting them. The upgrade removes comment APIs and comment database records; saved conversations and captured attachment bytes remain intact. Historical feedback captures appear as ordinary files. Rolling old/new operation is unsupported for this transition, and rollback requires restoring the backup.

## Command reference

The [complete command reference](command-reference.md) lists every registered shell subcommand and option, slash-command grammar, aliases, and busy-state availability. Put global options before subcommands; use `-h` or `--help` at each level.

Configuration commands open the local application and may write accepted configuration indexes or initialize data storage. They can start the pricing updater when enabled; `--no-update-check` only disables the startup package-update check. They are not a strictly offline, side-effect-free YAML parser. Validation does not make a model request or prove provider/MCP credentials work.

## Diagnose without losing your draft

| Symptom                                 | Next step                                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| A file edit seems ignored               | Run `config validate`, then `config show`; the previous accepted generation remains active if a new save is invalid             |
| An Agent cannot run                     | Check its `model` reference, selected Capability keys, roster IDs, and exact tool names                                         |
| A local Markdown child rejects `model`  | Remove the field; for independent settings create an Agent and use `- agent: agent-id`                                          |
| Two children share a name               | Remove one selection or rename the custom Markdown child; built-ins do not override custom roles                                |
| Sandbox readiness fails                 | Run `a13n-harness-ui doctor`; fix prerequisites or explicitly choose Full Control. Harness UI never falls back automatically.   |
| A request was rejected                  | Use the restored draft or `/recover`; do not assume it was sent                                                                 |
| An operation suspended in one-shot mode | Resume interactively and complete its pending decisions                                                                         |
| A process crashed                       | Inspect the private diagnostic path, review it for sensitive exception messages, and use the printed saved-state resume command |

Unsent input and in-flight side effects are not recoverable merely because a saved conversation exists. A diagnostic report contains no locals or transcript and is never uploaded automatically. Review it before sharing.
