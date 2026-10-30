---
title: SDKs and CLI
description: Choose a Service client and build your first conversation against a running deployment.
---

Use a Service SDK from your application, or the remote CLI from a terminal or shell script. Both connect to a running Service through its HTTP API. Service owns agent execution and persistence. To execute an agent inside your own process, use [Harness](../a13n-harness/index.md); for an interactive workbench in your repositories, use [Harness UI](../a13n-harness-ui/index.md).

## Choose a client

Choose the SDK that matches your application's language. Open its quick start for installation and a first call, then its application guide for the task you are building.

| Client                           | Use it for                                              | Quick start and task guides                                                                                                                                                                                           |
| -------------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python (`a13n`)                  | Python applications                                     | [Quick start](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md) · [Application guide](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/docs/README.md)                            |
| TypeScript (`@converge.ai/a13n`) | TypeScript and JavaScript applications                  | [Quick start](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/README.md) · [Application guide](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/docs/README.md)                    |
| Go                               | Go applications                                         | [Quick start](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/README.md) · [Application guide](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/docs/README.md)                                    |
| Rust (`a13n`)                    | Rust applications                                       | [Quick start](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/README.md) · [Application guide](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/docs/README.md)                                |
| Remote CLI (`a13n-service-cli`)  | Shell scripts, API exploration, and resource operations | [Command behavior](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/a13n-service-cli/README.md) · [Workflows](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/a13n-service-cli/docs/README.md) |

The remote CLI belongs to the Rust SDK repository and has its own executable and release channel. Use `a13n-service-cli` to call a deployment; the `a13n-service` command starts and operates the server.

## Connect to your deployment

1. Get the deployment's Service URL and select a configured agent in Console. The URL must be reachable from your application's runtime.
2. Choose credentials for the intended workspace. A workspace API key selects its own workspace; a login session uses an explicit workspace ID. Keep API keys in server-side configuration. See [identity and access](identity.md) for credential setup.
3. Follow the selected client's installation and initialization instructions at the version you use.
4. Send one message and display its answer before adding streaming, tools, or resource management. [Connect your application](connect-application.md) walks through this first conversation and its HTTP equivalent.

For browser applications, follow the client's browser authentication instructions and the Service [workspace and CSRF rules](http.md#authentication). For a new Service deployment, start with [Get started](get-started.md).

## Invoke an agent

Start with the client's high-level conversation workflow. Use its low-level typed API when you need explicit resource operations or direct control over HTTP requests. Both use the same Service authorization and execution rules; exact method names and return types belong to the selected client's guide.

Build each message exchange around one submission:

1. Submit input with an idempotency key. Preserve the submission receipt and thread reference so a retry or reconnect can find the same work.
2. Wait for the run that incorporates that input, including any queue time. The [entry tracking walkthrough](connect-application.md#read-the-result) explains why an assigned entry is not yet a consumed entry.
3. Handle the outcome: display a completed answer, collect explicit responses for a wait, or report failure or cancellation. Waiting responses continue execution in a successor run.
4. Send a follow-up to the retained thread as a new submission. Keep its result separate from the earlier exchange.

Add streaming when your application needs progress while work runs. Live output is provisional and can have gaps; use the saved result and committed run items for readback. A session stream can span multiple runs, so use the submission's entry and run IDs to select the work your screen is displaying.

Follow the client's guide to close streams and release local resources. A local timeout or disconnect stops observation, while Service continues execution. Cancel the run explicitly when stopping the work is the intended action. The application guides cover waiting actions, files, memory, authentication, and recovery; the CLI workflow guide covers shell usage and scripting.

## Keep version ownership clear

SDKs and the remote CLI release independently of Service. Before upgrading a client or deployment, compare the client's pinned Service contract and release notes with the Service version you will use. Read the client documentation at the installed release or commit; the linked `main` branch may describe newer APIs.

Use this site for [HTTP conventions](http.md), [request and response schemas](api-reference/index.md), and [agent execution behavior](agents-and-runs.md). Use the client repository for installation commands, method signatures, examples, and language-specific lifecycle handling.
