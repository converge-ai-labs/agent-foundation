# Service

The Service runs agents as a shared, multi-user application. It stores agent configurations and conversations, executes each run durably on workers with the [Harness](../a13n-harness/index.md), and exposes everything through an authenticated HTTP API and the Console web application. Work continues when a client disconnects, and a run survives the loss of the worker executing it.

Use the Service when several people or applications share agents, credentials and history. For a personal terminal agent, use [Harness UI](../a13n-harness-ui/index.md); to run agents inside your own process, use the Harness directly.

## Concepts

| Concept                    | Meaning                                                                                                                                                                                                                                                                     |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Organization**           | The administration boundary. It holds members and the providers and models it shares with its workspaces.                                                                                                                                                                   |
| **Workspace**              | The boundary for work: agents, conversations, connections, environments and most resources belong to one workspace.                                                                                                                                                         |
| **Principal, role, grant** | A user or service account; a role (`viewer`, `runner`, `builder`, `admin`) granted to it in an organization or workspace.                                                                                                                                                   |
| **API key**                | A bearer credential of one principal, confined to one workspace.                                                                                                                                                                                                            |
| **Provider**               | A configured account of an external service: a model provider (OpenAI, Anthropic, ...), a web provider (search and scrape), an environment provider (Docker, E2B, Daytona, Modal, Vercel, Sprites or Runloop), a connector provider (Composio) or a memory provider (mem0). |
| **Model**                  | A named upstream model of a model provider, with its capabilities and pricing, that agents select.                                                                                                                                                                          |
| **Agent, revision**        | An agent is a named head; each change adds an immutable, numbered revision of its configuration: model, instructions, tools, skills, connections, subagents and output.                                                                                                     |
| **Session, thread**        | A session is one conversation. It contains threads, each an independently advancing history with its own inbox and environments.                                                                                                                                            |
| **Run, attempt**           | A run is one accepted advancement of a thread with one agent revision. An attempt is one worker's execution of it.                                                                                                                                                          |
| **Inbox**                  | A thread's queue of input waiting to become a run or to steer the running one.                                                                                                                                                                                              |
| **Environment**            | A sandbox or computer where agent tools read files and run commands: created from an environment template, or an external envd target. Threads mount environments.                                                                                                          |
| **Memory**                 | What agents read and change across conversations: a tree of text files with the history of every change, or short records in mem0 that runs recall. Threads mount memories.                                                                                                 |
| **Connection**             | A source of tools with its own credential: a remote MCP server, or an app account through a connector provider.                                                                                                                                                             |
| **Skill, secret, asset**   | A versioned instruction package; a write-only value tools can use; immutable content uploaded or published by agents.                                                                                                                                                       |
| **Subscription**           | A webhook that receives run lifecycle events.                                                                                                                                                                                                                               |

## How the pieces fit

```mermaid
flowchart LR
    Client["Console or API client"] -->|"HTTP /api/v1"| Control["Control: authorize, store, accept"]
    Control --> Postgres[("PostgreSQL: resources, threads, runs")]
    Worker["Worker: execute with the Harness"] --> Postgres
    Worker --> Objects[("Object store: checkpoints, displays, files")]
    Worker --> External["Models, tools, environments"]
    Worker -->|"live output"| Redis[("Redis")]
    Redis --> Control
    Control -->|"thread stream"| Client
```

1. A client submits a message to a thread. The Service stores it in the thread's inbox and, when the thread is free, accepts it as a run that pins the agent revision and its resources.
2. A worker claims the run, executes the agent with the Harness, and commits a checkpoint and the run's display at each safe boundary, before model requests and tool calls. Live output flows through Redis to clients that follow the thread stream.
3. When the agent needs an approval, a client tool result or an answer, the run waits; a resume or a new message continues it as a successor run. When a run completes, the next queued input starts; after a failed or cancelled run, the thread waits for a new message.

Control and worker are roles of one executable; a single process can run both. See [Run and maintain](operations.md).

## Guides

| Goal                                                                 | Guide                                                                                                                                                                                                                       |
| -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deploy, create the first administrator and hold a first conversation | [Get started](get-started.md)                                                                                                                                                                                               |
| Manage members, roles, invitations, API keys and service accounts    | [Identity and access](identity.md)                                                                                                                                                                                          |
| Configure agents and run conversations through the API               | [Agents, threads and runs](agents-and-runs.md)                                                                                                                                                                              |
| Let an agent configure agents with you                               | [Configuration assistant](configuration-assistant.md)                                                                                                                                                                       |
| Configure resources shared by agents                                 | [Resource basics](resources.md), [Models](models.md), [Tools and connections](tools.md), [Skills and secrets](skills.md), [Environments](environments.md), [Memory](memory.md), [Files and webhooks](files-and-webhooks.md) |
| Build a client                                                       | [HTTP conventions](http.md) and the [HTTP reference](api-reference.md)                                                                                                                                                      |
| Configure and operate a deployment                                   | [Configure Service](configuration.md), [Run and maintain](operations.md), [Settings reference](configuration-reference.md)                                                                                                  |

## Interfaces

- **Console** is the browser application for the Service. Deployments serve it on the same origin as the API.
- **HTTP API**: every operation is under `/api/v1`. A running Service publishes its OpenAPI document at `/api/v1/openapi.json`; the [HTTP reference](api-reference.md) is generated from it.
- **Specification**: the Service's accepted contracts start at the [Service overview specification](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/a13n-service/00-overview.md).
- **`a13n-service`** is the server and operator command. Language SDKs and the companion remote command-line client are developed in independent repositories of [converge-ai-labs](https://github.com/converge-ai-labs); check each one for the Service versions it supports.
