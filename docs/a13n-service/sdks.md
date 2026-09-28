# SDKs and CLI

Use a Service SDK to integrate an application with a running Service, or the remote CLI for shell scripts and terminal operations. These clients call the Service HTTP API; they do not run agents inside your process. For embedded execution, use [Harness](../a13n-harness/index.md). For a local agent application, use [Harness UI](../a13n-harness-ui/index.md).

## Choose a client

| Client                           | Use it for                                                      | Documentation in its own repository                                                                                                                                                                                   |
| -------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python (`a13n`)                  | Async Python applications                                       | [Quick start](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md) · [Application guide](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/docs/README.md)                            |
| TypeScript (`@converge.ai/a13n`) | Node.js applications and same-origin browser integrations       | [Quick start](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/README.md) · [Application guide](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/docs/README.md)                    |
| Go                               | Go applications using context-based cancellation                | [Quick start](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/README.md) · [Application guide](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/docs/README.md)                                    |
| Rust (`a13n`)                    | Async Rust applications                                         | [Quick start](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/README.md) · [Application guide](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/docs/README.md)                                |
| Remote CLI (`a13n-service-cli`)  | Shell scripts, API exploration and explicit resource operations | [Command behavior](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/a13n-service-cli/README.md) · [Workflows](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/a13n-service-cli/docs/README.md) |

The SDK repositories own installation, language-specific methods, examples, compatibility and releases. Start with a quick start, then use the application guide's task chapters for conversations, streaming, waiting actions, files and Memory, authentication, and recovery. The CLI workflow guide covers the equivalent shell tasks and scripting boundaries. These guides are ordinary Markdown files on GitHub, not a separate documentation website. The CLI is owned by the Rust SDK repository but has an independent executable and release channel; it is not the `a13n-service` server/operator command.

## Connect to your deployment

Start with the Service URL and credentials for the intended workspace: an API key acts in its own workspace, and a login session names a workspace ID (see [HTTP conventions](http.md#workspace)). To submit a message, also select an existing Agent. [Get started](get-started.md) covers deployment and initial setup; [identity and access](identity.md) explains API keys versus login sessions.

## Invoke an Agent

The SDKs have two layers over the same transport and authentication:

- **Low-level API:** types and operations generated from the exported Service schemas, for complete protocol access and explicit resource administration.
- **High-level API:** an authored Agent workflow using each language's native conventions. Starting an Agent or sending another input returns one finite **Interaction**, with stream iteration, result access, the Thread reference and the original submission receipt on the same object.

An Interaction observes the Run that actually incorporates its input, including time spent queued. A queued Entry must be consumed before its assigned Run becomes authoritative; a provisional assignment is not enough. Iteration ends when that Run completes, fails, is cancelled or waits for human input, approval or a client-tool result; it does not follow later Runs on the Thread. Waiting is an outcome to handle explicitly, not permission for the SDK to approve or resume automatically. Applications that only need the result can wait for it without opening the stream.

The stream carries provisional observations, not a complete transcript or the durable result. Retention, gaps and changes to the Thread's current Run can limit replay; use the authoritative result and committed Run Items for readback. The low-level API retains access to the underlying Thread stream, but it is not a second high-level interaction mode.

Service owns authorization and durable execution. Closing an Interaction, timing out or disconnecting stops local observation, not remote work; interrupting a Run is a separate explicit operation. Follow the selected client's guide for native cleanup, conditional writes, structured inputs and recovery.

## Keep version ownership clear

These repositories version independently of Service and pin a Service contract. Check the client's contract provenance and release documentation against your deployment, and read its docs at the version you consume rather than assuming `main` matches an installed package.

This site documents Service behavior through [HTTP conventions](http.md), the [HTTP reference](api-reference.md), and [Threads and Runs](agents-and-runs.md). It intentionally does not duplicate SDK method signatures or command recipes: those change with their owning repositories.
