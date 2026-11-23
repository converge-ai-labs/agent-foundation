---
title: Managed tools
description: Enforce tool permissions, shell command review, and approval workflows in Harness.
---

Managed tools let the Host decide whether each tool call runs automatically, pauses for human approval, or is denied—without changing tool implementations.

## Select tool permissions

Configure permission rules on your Capability or Host policy to classify tool calls into three outcomes:

| Decision | Behavior                                                            |
| -------- | ------------------------------------------------------------------- |
| `allow`  | Executes immediately                                                |
| `ask`    | Suspends the Run with a deferred approval request for the Host/user |
| `deny`   | Rejects the call and returns the denial reason to the Model         |

When a Run suspends for approval, inspect `result.deferred`, collect the user's decision, and resume the Run with the approval response. See [Human-in-the-loop](human-in-the-loop.md) for the complete resume flow.

## Shell command review

Add `ShellReviewCapability` to evaluate `environment.shell_exec` calls with a dedicated reviewer Model before execution:

- Assigns a risk level (`low`, `medium`, `high`, `extra_high`) and short rationale.
- Commands at or above `ask_at_or_above` pause for human approval with the reviewer's assessment attached.
- Denial reasons are returned to the Agent so it can propose a safer alternative.

## Background and retained tool execution

Long-running tools (such as process-backed shell commands) can yield a handle after a foreground wait so the Agent can inspect status (`shell_info`), wait (`shell_wait`), or cancel without blocking the entire turn.
