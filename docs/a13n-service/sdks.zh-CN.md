---
title: Service SDK 与 CLI
description: 选择 Service 客户端，在运行中的部署上完成第一次对话。
---

在应用中使用 Service SDK，在终端或 shell 脚本中使用远程 CLI。两者都通过 HTTP API 连接运行中的 Service，由 Service 负责 agent 执行和持久化。需要在自己的进程中执行 agent 时，使用 [Harness](../a13n-harness/index.md)；需要在代码仓库中使用交互式工作台时，使用 [Harness UI](../a13n-harness-ui/index.md)。

## 选择客户端

选择与应用语言匹配的 SDK。先打开快速入门完成安装和首次调用，再按应用指南实现所需任务。

| 客户端                            | 适用场景                       | 快速入门与任务指南                                                                                                                                                                                         |
| --------------------------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python（`a13n`）                  | Python 应用                    | [快速入门](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md) · [应用指南](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/docs/README.md)                             |
| TypeScript（`@converge.ai/a13n`） | TypeScript 和 JavaScript 应用  | [快速入门](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/README.md) · [应用指南](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/docs/README.md)                     |
| Go                                | Go 应用                        | [快速入门](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/README.md) · [应用指南](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/docs/README.md)                                     |
| Rust（`a13n`）                    | Rust 应用                      | [快速入门](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/README.md) · [应用指南](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/docs/README.md)                                 |
| 远程 CLI（`a13n-service-cli`）    | Shell 脚本、API 探索和资源操作 | [命令行为](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/a13n-service-cli/README.md) · [工作流](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/a13n-service-cli/docs/README.md) |

远程 CLI 属于 Rust SDK 仓库，拥有独立的可执行程序和发布渠道。调用部署使用 `a13n-service-cli`；启动和运维服务器使用 `a13n-service` 命令。

## 连接部署

按[应用接入指南](connect-application.md#set-up-credentials)获取 Service URL、agent ID 和工作空间凭据。使用上表对应客户端的快速入门完成安装和初始化。浏览器客户端还需遵守[工作空间与 CSRF 规则](http.md#authentication)。

## 调用 agent

使用 SDK 的对话接口发送消息，等待已保存的结果，并保留线程引用以便追问。要跟踪排队中的输入、处理等待，或在 Console 里找到同一次运行，请参阅[应用接入指南](connect-application.md)。需要管理资源或控制具体请求时，使用对应 SDK 仓库文档里的类型化 API。

## 明确版本归属

SDK 和远程 CLI 独立于 Service 发布。升级客户端或部署前，对照客户端固定的 Service 协议版本及发布说明，确认它们匹配将使用的 Service 版本。阅读已安装版本或对应 commit 的客户端文档；链接中的 `main` 分支可能已经描述更新的 API。

本站提供 [HTTP 约定](http.md)、[请求和响应 schema](api-reference/index.md)，以及 [agent 执行行为](agents-and-runs.md)。安装命令、方法签名、示例和各语言的生命周期处理请查阅客户端仓库。
