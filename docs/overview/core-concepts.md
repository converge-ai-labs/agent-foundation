---
title: Agents and conversations
description: Understand how agents, threads, runs, human approvals, and persistent state map across Service, Harness, and Harness UI.
---

Service, Harness UI, and applications embedding Harness share the same core execution concepts. Because every product runs on Harness, learning how an agent processes a conversation in one product transfers directly to the others.

| Concept         | Role in the lifecycle                                                                                     |
| --------------- | --------------------------------------------------------------------------------------------------------- |
| **Agent**       | Combines a model, instructions, tools, and capabilities to define how work is performed.                  |
| **Thread**      | Holds the ordered message history and portable feature state of one conversation.                         |
| **Run**         | A single execution turn that processes new input or resumes after a human decision, advancing the thread. |
| **Environment** | Provides the filesystem, shell, process, or desktop target where file and command tools execute.          |
| **Memory**      | Retains shared knowledge, preferences, and notes across separate threads and sessions.                    |

## Choose an agent

Every conversation starts by selecting or constructing an agent definition:

- **Service:** Choose a workspace agent in Console or pass its `agent_id` to the API. Each configuration change saves an immutable revision so active runs remain reproducible. See [How Service conversations work](../a13n-service/core-concepts.md#choose-an-agent).
- **Harness UI:** Select an Agent from your [instance configuration](../a13n-harness-ui/configuration.md) using `/agent` in the terminal or the Agent selector in WebUI.
- **Harness (Python SDK):** Construct an `AgentSpec` (or `AgentDefinition`) and build a reusable `ExecutableAgent` with `HarnessBuilder` in your Python process. See [Agents and Runs](../a13n-harness/agents-and-runs.md).

## Start a conversation

When you send a message, the host starts a run on a new or existing thread:

- **Service:** Submit input from [Console](../a13n-service/core-concepts.md#start-a-conversation) or through the [Service SDK and HTTP API](../a13n-service/connect-application.md). You can stream events in real time or poll the run status.
- **Harness UI:** Work from the [TUI](../a13n-harness-ui/everyday-use.md) or [WebUI](../a13n-harness-ui/webui.md). Both interfaces share the same local conversation store, allowing you to start in the terminal and inspect files or diffs in the browser.
- **Harness (Python SDK):** Call `await executable.run(...)` for a final result or `async with executable.stream(...)` to consume live events as the model generates text and calls tools.

## Answer a question or approve a tool call

Agents can pause execution to ask the user a clarifying question, request approval before running a sensitive tool, or wait for an external client tool result:

- **Service:** Console displays interactive cards for [pending questions and approvals](../a13n-service/core-concepts.md#answer-a-question-or-approve-a-tool-call). API clients read `pending` requests on the run and submit the complete response batch to `/resume`.
- **Harness UI:** Decision cards open directly in the TUI composer or WebUI thread. You can approve once, deny with feedback, or edit tool arguments before continuing.
- **Harness (Python SDK):** Configure the [interaction handler](../a13n-harness/human-in-the-loop.md) and [managed tool approval policy](../a13n-harness/managed-tools.md), or persist the suspended `HarnessState` and resume later with `DeferredToolResume`.

## Continue later

Conversation state is decoupled from live compute resources so threads can be resumed across restarts:

- **Service:** Persists [thread history](../a13n-service/core-concepts.md#continue-later) in PostgreSQL and run checkpoints in object storage, and pairs threads with shared [Memory Spaces](../a13n-service/memory.md).
- **Harness UI:** Saves conversations and checkpoints automatically in its local SQLite store; use `/resume` in the TUI or select any saved thread in WebUI.
- **Harness (Python SDK):** Every run returns a serializable `HarnessState`. Pass `previous_state` to the next run in memory, or serialize it to JSON in your database to [resume across process restarts](../a13n-harness/state-and-resume.md).

## Understand execution when you need it

When you need to debug tool calls, inspect token usage, or understand failure recovery:

- **Service:** Groups related threads (including subagent delegations) inside a **session**, and tracks each **run** and worker execution **attempt**. Open Console's Debug view or read [How Service conversations work](../a13n-service/core-concepts.md#understand-execution-when-you-need-it).
- **Harness UI:** Toggle `Ctrl+O` in the TUI to expand full tool arguments and diffs, run `/usage` and `/subagents` to inspect costs and child work, or view structured traces.
- **Harness (Python SDK):** Attach [OpenTelemetry and observation hooks](../a13n-harness/observation.md) or convert streams into frontend-ready AG-UI events with [Stream Protocol](../a13n-stream-protocol/index.md).
