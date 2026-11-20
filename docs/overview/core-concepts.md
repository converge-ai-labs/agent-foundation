---
title: Agents and conversations
description: Understand how agent configuration, runtime conversation history, and durable execution state operate across Harness, Harness UI, and Service.
---

Every agent interaction within the Agent Foundation ecosystem is driven by the foundational Harness engine. The host environment determines where agent configurations live, who has permission to trigger executions, how state is preserved, and how paused runs resume.

## Choose an agent

An agent configuration pairs behavioral instructions and model parameters with specific tool permissions and environments.

- **In Service:** Select a versioned [agent definition in your workspace](../a13n-service/core-concepts.md#choose-an-agent). Team members inherit permissions assigned by workspace administrators.
- **In Harness UI:** Agents are loaded from declarative [YAML configuration files](../a13n-harness-ui/configuration.md) in your project or home directory.
- **In embedded Harness:** You construct and configure the `AgentSpec` or `AgentDefinition` directly in your [Python application code](../a13n-harness/agents-and-runs.md).

## Start a conversation

A conversation organizes user messages, agent replies, and intermediate tool execution events into an ordered thread.

- **In Service:** Dispatch messages via [Console or client SDKs](../a13n-service/core-concepts.md#start-a-conversation). Service captures the input in an inbox entry, allocates a run, and streams results over SSE.
- **In Harness UI:** Use the interactive terminal prompt or the [WebUI interface](../a13n-harness-ui/webui.md). You can supply multiline prompts, attach local files, and steer active generation in real time.
- **In embedded Harness:** Your application calls `executable.run()` for blocking execution or `async with executable.stream()` to consume live event streams.

## Answer a question or approve a tool call

Agent runs can request structured feedback or human approval before proceeding with sensitive operations.

- **In Service:** Pending interactions appear as structured cards in Console or as suspended run payloads via the API. Once approved or answered, submitting the batch [resumes execution](../a13n-service/core-concepts.md#answer-a-question-or-approve-a-tool-call).
- **In Harness UI:** Decision cards display command arguments, risk summaries, and option selectors directly in the TUI/WebUI.
- **In embedded Harness:** Register custom [human-in-the-loop interaction handlers](../a13n-harness/human-in-the-loop.md) and define fine-grained [tool approval policies](../a13n-harness/managed-tools.md) suited to your host platform.

## Continue later

Preserving conversation history allows agents to maintain multi-turn context and resume tasks across process restarts.

- **In Service:** Thread history, tool attachments, and execution checkpoints are reliably preserved in PostgreSQL and object storage.
- **In Harness UI:** Sessions and conversation threads are automatically persisted in the local workbench store.
- **In embedded Harness:** Harness emits a compact, serializable `HarnessState` object upon turn completion. Your host application stores this state and passes it to subsequent `run(..., previous_state=state)` calls.

## Understand execution when you need it

When diagnosing agent performance or reviewing operational safety, inspect execution at the appropriate level of granularity:

- **Sessions & Threads:** A **Session** groups logically related collaborative threads. A **Thread** represents an ordered lineage of messages and tool events.
- **Runs & Attempts:** A **Run** represents a single attempt to process new input within a thread. In distributed deployments, if a worker node experiences a transient restart, the scheduler automatically dispatches a new **Attempt** that resumes execution from the last durable checkpoint.
- **Deep Tracing:** Use Console's Debug view or the native [Harness observation APIs](../a13n-harness/observation.md) to inspect raw model request payloads, token counts, latency metrics, and exact tool invocation arguments.
