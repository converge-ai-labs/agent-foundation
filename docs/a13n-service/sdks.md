---
title: Service SDKs and CLI
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

Use the Service URL, agent ID, and workspace credentials from [Connect your application](connect-application.md#set-up-credentials). Follow your client’s quick start above to install and initialize it. For browser clients, check the [workspace and CSRF rules](http.md#authentication).

## Invoke an agent

Use the SDK’s conversation workflow to send a message and wait for its saved result. Keep the thread reference for follow-ups. Follow [Connect your application](connect-application.md) to correlate queued input, handle a wait, or find the same run in Console. If you need resource operations or raw request control, use the typed API documented in your SDK repository.

## Keep version ownership clear

SDKs and the remote CLI release independently of Service. Before upgrading a client or deployment, compare the client's pinned Service contract and release notes with the Service version you will use. Read the client documentation at the installed release or commit; the linked `main` branch may describe newer APIs.

Use this site for [HTTP conventions](http.md), [request and response schemas](api-reference/index.md), and [agent execution behavior](agents-and-runs.md). Use the client repository for installation commands, method signatures, examples, and language-specific lifecycle handling.
