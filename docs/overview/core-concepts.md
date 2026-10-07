---
title: Core concepts
description: Choose an agent, exchange messages, handle requests, and continue a conversation.
---

Follow one conversation with an agent that reports project status. This example uses **Service**, the managed-agent runtime, through **Console** or a Service SDK. For embedded or interactive execution, see [Choose your path](choose-your-path.md).

## Choose an agent

An administrator gives you a role in a **workspace**, which contains your team's agents, other resources, and conversations. Your roles in the organization and workspace determine what you can use or change.

An **agent** combines a model, instructions, and tools. Example instructions: “Summarize project progress and cite the information you used.”

| Part            | Role in the agent's work                                                   |
| --------------- | -------------------------------------------------------------------------- |
| **Model**       | Interprets requests and produces responses or tool calls                   |
| **Tool**        | Looks up a task, reads a file, or performs another configured action       |
| **Connection**  | Gives access to external tools through MCP or an app account               |
| **Environment** | Provides the files and command execution used by tools                     |
| **Memory**      | Holds project decisions the agent can read and update across conversations |

Start with a model and instructions. Before you ask the agent to look up real progress, add a tool or connection that reads your project data. [Tools and connections](../a13n-service/tools.md) explains the setup.

## Start a conversation

Ask: “Summarize the project status and list the next two tasks.”

In Console, choose an agent and send your message. In an application, use the SDK's high-level start operation. Service creates the conversation resources for you.

You see replies and tool activity as they arrive. While the agent works, you can add guidance or stop it. Guidance may be applied at the next model request or remain queued; check its receipt rather than assuming it has already been applied.

## Answer a question or approve a tool call

If the agent asks which project you mean, answer its question card. If a tool call needs approval, approve or deny it. Complete every pending request before submitting the answers. The conversation continues after the complete batch is accepted. Applications handle these through [Waits, approvals and questions](../a13n-service/agents-and-runs.md#waits-approvals-and-questions).

## Continue later

Open **Sessions** and choose **Continue conversation**, then ask “Which task should I do first?” Applications send the follow-up to the returned thread reference. Work continues when you disconnect, and Service stores the history for your return. **Inspect execution** opens the diagnostic view when you need to investigate a problem.

| Information       | Purpose                                            |
| ----------------- | -------------------------------------------------- |
| Thread history    | Continue this thread                               |
| Memory            | Save and retrieve information across conversations |
| Environment files | Store documents and other working files for tools  |

Mount a [memory](../a13n-service/memory.md) on the thread, or set it as an agent default, to share saved decisions across conversations. It can supply file context or recall relevant records automatically; enabled memory tools let the agent search and update it. The idle policy of the [environment](../a13n-service/environments.md) template determines how long an environment's files remain available.

## Understand execution when you need it

A **session** groups related **threads**. Each thread has its own history and inbox; a branch or delegated task can have a separate thread. A **run** advances a thread using the agent's model and tools. A **run attempt** records a worker's execution of that run, including recovery. Console's Debug view exposes these details; ordinary conversation controls handle their identifiers for you.

These are not one-to-one with messages: guidance can join an active run, and answering a waiting request creates a successor run. See [Agents, threads and runs](../a13n-service/agents-and-runs.md) for the exact lifecycle.

Each agent configuration save creates an immutable **revision**. New runs select the current default unless a message pins another revision. Active work keeps its accepted configuration, and approval continuations inherit the waiting run's revision. **Versions** lets you inspect saved configurations.

Next, [try an agent in Console](../a13n-service/use-platform.md) or [connect your application](../a13n-service/connect-application.md). See [Agents, threads and runs](../a13n-service/agents-and-runs.md) for revision selection and execution details.
