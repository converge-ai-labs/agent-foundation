---
title: Automation and troubleshooting
description: Run headless one-shot agent tasks from CI or shell scripts, inspect diagnostic logs, update installations, and recover interrupted threads.
---

Use `a13n-harness-ui run` for headless one-shot scripts and CI jobs, and inspect operation status and saved checkpoints before repeating interrupted work.

## Automation and diagnostics

After completing [setup](setup.md), execute non-interactive tasks from the target Project directory (placing global flags before the subcommand):

```console
# Print final text answer
a13n-harness-ui run "Review the current diff"

# Emit structured JSON operation receipt (exit 0 = completed, nonzero = failed/suspended)
a13n-harness-ui run "Review the current diff" --format json

# Continue a saved Thread or force Sandbox mode
a13n-harness-ui run "Summarize the next step" --resume thread-id --format json
a13n-harness-ui --environment-mode sandbox run "Inspect the repository"
```

One-shot mode uses Project defaults and permission rules without opening interactive approval cards. If a Run suspends for a decision, resume interactively with `a13n-harness-ui --resume thread-id` to answer it.

| Symptom                             | Resolution                                                                                              |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Configuration edit appears ignored  | Run `a13n-harness-ui config validate` and `config show` (invalid edits leave the prior generation live) |
| Agent fails to start                | Check Model IDs, Capability keys, roster IDs, and duplicate custom vs. built-in subagent names          |
| Sandbox readiness fails             | Run `a13n-harness-ui doctor` and resolve reported prerequisites                                         |
| Submission rejected or draft closed | Restore buffered input with `/recover`, fix the reported issue, and resubmit                            |
| `[System] Retrying model request…`  | Transient provider interruption (up to 5 attempts per turn without replaying completed tools)           |
| Source checkout import errors       | Run `make sync` (or `make a13n-harness-ui`) after switching Git branches                                |

See [Tracing](observation.md) for OTLP telemetry, [WebUI](webui.md) for server restart recovery, and the [Command reference](command-reference.md) for full CLI syntax.

## Logs, updates, and exit

### Diagnostic reports and checkpoint recovery

1. **Logs and crash reports:** Unexpected failures write `a13n-harness-ui-error-*.json` to the system temporary directory (review before sharing; no data is uploaded automatically). Interactive logs rotate at `<data-root>/logs/terminal.log` (5 MiB, 3 backups, correlated by Run ID).
2. **Resume after exit or crash:** Reopen the Thread using the printed `a13n-harness-ui --resume <thread-id>` command and inspect `/status` and `/history`. Completed tool results are preserved; unanswered retryable calls follow [`tool_recovery="declared"`](../a13n-harness/state-and-resume.md#resume-unanswered-tool-calls) under fresh permission checks.

### Update Harness UI and the Envd runtime

Close active TUI/WebUI sessions before upgrading a `uv tool` installation:

```console
a13n-harness-ui update
```

Startup update checks display a notice only (updates run explicitly via `a13n-harness-ui update`); suppress the notice once with `--no-update-check` or permanently in `a13n-harness-ui.yaml`:

```yaml title="a13n-harness-ui.yaml"
process:
  terminal_update_check: false
```

To check or acquire the matching `a13n-envd` binary for Sandbox mode (or upgrade a standalone daemon), stop running instances using that data root first:

```console
a13n-harness-ui envd update --check
a13n-harness-ui envd update
a13n-harness-ui envd update --executable /absolute/path/to/a13n-envd
```

On macOS, if Screen Recording or Accessibility permissions need re-authorization after replacing the binary, follow [Restore macOS permissions](../a13n-envd/computer-use.md#restore-macos-permissions-after-an-upgrade).
