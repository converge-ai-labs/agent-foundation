---
title: Automation and troubleshooting
description: Run Harness UI from a script, inspect failures, update installations, and resume saved conversations.
---

Use `run` for a single scripted task. When something fails, inspect its operation status and saved state before repeating it.

## Automation and diagnostics

First complete [setup](setup.md) and verify a short interactive conversation. Then run commands from the directory whose Project you want to use.

1. Submit one task and print its final answer:

   ```console
   a13n-harness-ui run "Review the current diff"
   ```

2. For scripts, request the structured operation object:

   ```console
   a13n-harness-ui run "Review the current diff" --format json
   ```

   Exit status `0` means the operation completed. Failed or suspended operations return nonzero. Inspect the operation's status and failure details rather than treating any output as success.

3. To continue an existing conversation, replace `thread-id` with its saved root Thread ID:

   ```console
   a13n-harness-ui run "Summarize the next step" --resume thread-id --format json
   ```

One-shot mode uses the same Project, continuation, and permission rules as interactive mode, but skips the Model preference remembered by `/model`. It does not open interactive approval prompts. If a Run suspends for a decision, resume interactively and answer it:

```console
a13n-harness-ui --resume thread-id
```

Put global options before the subcommand. For example, this selects Sandbox for the scripted task:

```console
a13n-harness-ui --environment-mode sandbox run "Inspect the repository"
```

Use `a13n-harness-ui --help` and subcommand `--help` for syntax without starting the App. Configuration inspection commands do open the local App: they can initialize storage, write accepted indexes, and start the pricing updater when enabled. `config validate` checks configuration, not provider or MCP connectivity; `--no-update-check` only disables the startup package-update check.

## Diagnose without losing your draft

| Symptom                                | Next step                                                                                                               |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| A configuration edit seems ignored     | Run `config validate`, then `config show`. An invalid edit leaves the previous accepted generation active.              |
| An Agent cannot run                    | Check its Model reference, Capability keys, roster IDs, and exact tool names.                                           |
| A local Markdown child rejects `model` | Remove that field. For independent settings, create an Agent and reference it with `- agent: agent-id`.                 |
| Two children share a name              | Remove a selection or rename the custom Markdown child. Built-ins do not override custom roles.                         |
| Sandbox readiness fails                | Run `a13n-harness-ui doctor` and fix its reported prerequisites. Selecting Full Control is a separate, explicit choice. |
| Submission was rejected                | Use the restored draft or `/recover`, check the rejection, and submit again when ready.                                 |
| A one-shot Run suspended               | Resume that Thread interactively and complete the pending decision.                                                     |
| A reply appeared, but saving failed    | Read the error and saved continuation status. Inspect completed actions before submitting another task.                 |
| Startup, resume, or the process failed | Read the diagnostic-report path, then use the saved-state recovery steps below.                                         |

Large active messages first appear as plain-text previews and reflow to Markdown when complete. Older rendered rows load as you scroll. If the bounded source cache evicts content, use `/history` as directed; it shows content the App retained and can expose.

## Model connection interruptions

When the TUI shows `[System] Retrying model request…`, the same Run is retrying an eligible transient model interruption from available history. It allows up to five consecutive attempts, including the first. A successful primary response resets that budget; permanent errors stop execution.

Let the existing retry finish or cancel the Run. Completed tool calls are not replayed by model recovery. Before manually repeating a side-effecting action after a failure, inspect its actual result. Model retries do not restart a crashed process.

## Long-running work

Root Agents and subagents have no fixed Harness request-count limit. Provider and child limits still apply, and longer Runs can consume more tokens. Use `/status` to inspect progress and `/cancel` to stop active work. After a process crash, start a new invocation from the saved continuation; active work does not restart automatically.

## Logs, Updates, and Exit

### Find a diagnostic report

1. Read the displayed error chain and report path. Model execution failures and unexpected errors can create `a13n-harness-ui-error-*.json` in the system temporary directory.
2. Read `<data-root>/logs/terminal.log` for startup timings and interactive diagnostics. The log rotates at 5 MiB and keeps three backups. Correlate entries by Run ID.
3. Review the report before sharing it. It contains exception chains, stack locations, component versions, and Thread/Run IDs; exception messages can contain sensitive data. It excludes frame locals, source lines, and transcripts.
4. If reporting a bug, attach the reviewed report and reproduction steps through the Issue link in the failure notice. Nothing is uploaded automatically.

Skipped Content Plugins produce one actionable notice for each unchanged path and reason; the log contains details. Legacy Content Plugin files stay on disk.

For the asyncio warning `Task was destroyed but it is pending!` without an accompanying exception, the TUI keeps your draft and stays open. The report includes bounded task identity and available coroutine, suspended-stack, and creation-stack locations. Check `/status`; use `/cancel` if work stops progressing. The lost task is not restored or retried. Other unhandled TUI event-loop failures exit with recovery instructions.

### Update Harness UI

For a recognized uv-tool installation:

1. Exit Harness UI.

2. Run:

   ```console
   a13n-harness-ui update
   ```

3. Restart Harness UI after the command succeeds.

The command directly runs `uv tool upgrade a13n-harness-ui` for the running installation's tool directory. It does not ask for another confirmation, open setup, or use the startup metadata cache. uv must be on PATH. Other installation types should use their original package manager. You can also invoke `uv tool upgrade a13n-harness-ui` directly.

A failed installer returns a nonzero status without automatic retry. An interrupted update exits with status `130`; inspect the installation before retrying. Updating the Python package and preparing its native Envd runtime are separate operations.

### Control update notices

Startup runs setup if needed, then opens the conversation. Package-update detection runs in the background and **never installs an update**. Installed release builds check public PyPI metadata with a three-second timeout and a daily cache. Offline failure is silent. A newer stable release produces one System notice with both versions and the appropriate update command; it does not interrupt typing or require a choice.

To disable detection, merge this fragment into the [selected root YAML](configuration.md#locate-and-validate-your-files):

```yaml title="a13n-harness-ui.yaml"
process:
  terminal_update_check: false
```

Use `a13n-harness-ui --no-update-check` to skip it once. Development versions, help/version, noninteractive commands, and `make a13n-harness-ui` skip detection. Setup-only invocations do not wait for or show an update notice; closing the terminal cancels a pending check. The next enabled conversational launch can show a cached notice again. These controls leave the explicit `update` command available.

### Prepare or update Envd

For the managed runtime, stop other Harness UI instances using the same data root, then run:

```console
a13n-harness-ui envd update --check
a13n-harness-ui envd update
```

The first command reports whether the selected runtime file exists; it does not verify execution. The second acquires the version required by the installed client. Restart Harness UI afterward. To select a newer managed version, update Harness UI; use `--force` to reinstall the currently selected version.

These commands honor global `--config` and `--data-root`, and start neither the App nor a daemon. Acquisition uses an existing native updater where supported, or bootstrap acquisition for a missing installation or legacy cached binary. Native failures are reported without automatic fallback or retry.

For a standalone installation, stop its daemons first and select the executable:

```console
a13n-harness-ui envd update --executable /absolute/path/to/a13n-envd
```

A configured `envd.executable` is selected automatically. This path delegates to [Envd's native updater](../a13n-envd/installation.md#update-an-existing-installation), which defaults to its newest stable release. Add `--version X.Y.Z` to select a client-matched version or RC. Restart the affected application or service after success. Remote Devices are not updated over EIP.

If Envd reports missing macOS Screen Recording or Accessibility permission after replacement, follow its error and [restore permissions](../a13n-envd/computer-use.md#restore-macos-permissions-after-an-upgrade).

### Resume after exit or failure

1. Use the resume command printed after terminal cleanup. It names the root Thread, retains explicit configuration/data-root options, and identifies the working directory.
2. Inspect `/status` and `/history` before continuing. Check any action whose outcome was uncertain.
3. Submit the next instruction or complete the pending decision.

Failed or interrupted operations save an available valid Harness checkpoint, including safely retained partial text, before releasing the operation. The next turn uses that selected checkpoint. If export or storage failed, or the process was killed before cleanup, resume uses the previous saved checkpoint.

Recorded tool results are not replayed. Unanswered calls follow [`tool_recovery="declared"`](../a13n-harness/state-and-resume.md#resume-unanswered-tool-calls): currently declared retryable tools may run again under fresh permissions and approval requirements. An earlier approval does not authorize replay. Accepted deferred results saved beside the checkpoint remain available until native history incorporates them.

Saved state does not recover unsent input or establish the outcome of an in-flight side effect. A diagnostic report is separate from that saved state.

## Source checkout dependencies

After switching branches, run `make sync`, or launch with `make a13n-harness-ui`, to install the dependencies locked for that checkout. Reusing another branch's environment can cause import failures. Use the committed lockfile rather than patching private dependency imports or independently upgrading libraries. Installed users should update through their installation's package manager.

## Execution tracing

Follow [Tracing Harness UI](observation.md) to configure OTLP export, send a test Run, and inspect model/tool timing and continuation saving. The repository's `make cli` workflow uses a separate private `dev/harness-ui/.env`.

## WebUI

See [WebUI](webui.md) for listener and authentication setup, collaboration, API-key retention, containers, and foreground lifecycle.

## Command reference

The [command reference](command-reference.md) covers shell subcommands, options, slash-command grammar, aliases, and busy-state availability. Use `-h` or `--help` at each command level.
