# Choose your path

Agent Foundation supports managed execution through Service, embedded execution through Harness, and interactive work through Harness UI.

| Your goal                              | What you need                                                   | Start here                                                      |
| -------------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------- |
| Deploy a shared agent platform         | Docker Compose and a model provider account for the local trial | [Service quickstart](a13n-service/get-started.md)               |
| Use your team's agents                 | Console URL, an account, and permission to run an agent         | [Use an existing platform](a13n-service/use-platform.md)        |
| Call remote agents from an application | Service URL, workspace API key, and a configured agent          | [Connect your application](a13n-service/connect-application.md) |
| Run agents inside your Python process  | Python 3.13+ and uv; the first example uses an offline model    | [Harness quickstart](a13n-harness/getting-started.md)           |
| Work interactively in a repository     | Harness UI and a supported model subscription or API key        | [Harness UI setup](a13n-harness-ui/setup.md)                    |

## Choose where execution lives

- **Service:** your application submits work to a running deployment. Service owns identities, saved conversations, and durable execution; Console is its browser application.
- **Harness:** your Python process constructs and runs the agent. Your application owns storage, access policy, and delivery.
- **Harness UI:** the terminal and browser workbench runs agents with its own configuration and conversation history. Share an instance with trusted collaborators.

A **Service SDK** is a client for remote execution. The **Harness library** executes agents inside your process. Service Console and Harness UI's browser are separate applications.

## Use individual components

| Need                                                  | Guide                                            |
| ----------------------------------------------------- | ------------------------------------------------ |
| Portable files and commands, with or without an agent | [Environments](environments/index.md)            |
| Environment operations through a daemon               | [Envd](a13n-envd/index.md)                       |
| AG-UI events from Harness observations                | [Stream Protocol](a13n-stream-protocol/index.md) |
| Distribution names, imports, and runnable examples    | [Package catalog](packages.md)                   |

For a guided explanation of one conversation, read [Core concepts](core-concepts.md). For deployment operations, see [Run and maintain](a13n-service/operations.md). Service clients have independent versions; check their [supported contract](a13n-service/sdks.md#keep-version-ownership-clear) against your deployment.
