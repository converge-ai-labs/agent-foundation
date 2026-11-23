---
title: Delegation and CodeAct
description: Delegate bounded tasks to inline or host-managed subagents and execute restricted Python programs with CodeAct in Harness.
---

Use **subagents** to isolate focused work in a clean context window, and **CodeAct** to let an Agent compose multi-step tool calls in restricted Python.

## Subagents

<span id="asynchronous-children"></span>

Attach named child definitions on `HarnessBuilder().build(..., subagents=...)` (or via `SubagentCapability`):

- **Inline subagents:** Run inside the parent Run's stream, share the parent's Environment, and return their result directly to the parent.
- **Host-managed subagents:** Run as independent child Runs managed by the Host (such as Service or Harness UI) with their own checkpoints and concurrency limits.

### Host-managed feedback

Built-in inline children set `RunBindings.deferred_tools_supported=False` because they cannot pause independently for human input. Hosts that support pausing and resuming child Runs manage deferred feedback on their own child lifecycle.

## CodeAct

`CodeActCapability` lets the Model run restricted Python snippets that invoke the Agent's existing tools (subject to the same permissions and approvals) and persist JSON values across steps with `store`, `load`, and `forget`.
