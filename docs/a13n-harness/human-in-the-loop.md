---
title: Human-in-the-loop
description: Pause Harness runs for structured user questions, tool approvals, or external tool results and resume safely from saved state.
---

When an Agent needs a human decision—a structured question (`UserInteractionCapability`), a tool approval (`ask` permission), or a client-executed tool result—the Run returns `result.status == "suspended"`.

## Suspend and resume flow

1. Run or stream the Agent and check `result.status == "suspended"`.
2. Persist `result.state` and inspect `result.deferred` for the pending questions, approvals, or external tool calls.
3. Collect the user's answers or approval decisions in your UI/API.
4. Resume the Run by passing `previous_state=result.state` and the deferred responses on the next `run()` or `stream()` call.

## Host boundaries

- Set `RunBindings.deferred_tools_supported=False` when a Run cannot pause for human input (for example, [built-in inline children](delegation-and-codeact.md#host-managed-feedback)).
- For Service HTTP resume workflows with `Idempotency-Key`, see [Service waiting Runs](../a13n-service/agents-and-runs.md#resume-a-waiting-run) and the [SDK guides](../a13n-service/sdks.md).
