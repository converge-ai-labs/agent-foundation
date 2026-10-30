---
title: SDK 与 CLI
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

1. 获取部署的 Service URL，并在 Console 中选择一个配置好的 agent。应用运行环境必须能够访问该 URL。
2. 选择目标工作空间的凭据。工作空间 API 密钥自动选中其所属工作空间；登录会话需要明确指定工作空间 ID。API 密钥保存在服务端配置中。凭据配置请参阅[身份与访问](identity.md)。
3. 按所用版本的客户端文档完成安装和初始化。
4. 先发送一条消息并展示回答，再添加流式输出、工具或资源管理。[连接你的应用](connect-application.md)介绍首次对话和对应的 HTTP 流程。

浏览器应用请遵循客户端的浏览器认证说明，以及 Service 的[工作空间与 CSRF 规则](http.md#authentication)。需要新部署 Service 时，从[快速入门](get-started.md)开始。

## 调用 agent

从客户端的高层对话工作流开始。需要显式资源操作或直接控制 HTTP 请求时，使用底层类型化 API。两者遵循相同的 Service 授权和执行规则；具体方法名和返回类型由所选客户端的指南说明。

围绕一次提交组织每次消息交互：

1. 使用幂等 key 提交输入。保存提交回执和线程引用，供重试或重连时找到同一项工作。
2. 等待实际纳入该输入的运行，包括消息排队的时间。[条目跟踪示例](connect-application.md#read-the-result)解释了为什么已分配条目还不等于已消费条目。
3. 处理结果：展示完成的回答、为等待收集明确响应，或报告失败和取消。等待响应会在后继运行中继续执行。
4. 向保留的线程发送后续消息，作为一次新提交。将新结果与上一轮交互分开跟踪。

应用需要展示运行进度时，再添加流式输出。实时输出是临时观测，可能存在缺口；回读使用已保存的结果和已提交的运行显示项。会话事件流可能跨越多次运行，因此应使用提交对应的条目和运行 ID，选择界面正在展示的工作。

按客户端指南关闭事件流并释放本地资源。本地超时或断开连接会停止观察，Service 仍继续执行。需要停止工作时，明确取消运行。应用指南还涵盖等待操作、文件、记忆、认证和恢复；CLI 工作流指南介绍 shell 用法和脚本操作。

## 明确版本归属

SDK 和远程 CLI 独立于 Service 发布。升级客户端或部署前，对照客户端固定的 Service 协议版本及发布说明，确认它们匹配将使用的 Service 版本。阅读已安装版本或对应 commit 的客户端文档；链接中的 `main` 分支可能已经描述更新的 API。

本站提供 [HTTP 约定](http.md)、[请求和响应 schema](api-reference/index.md)，以及 [agent 执行行为](agents-and-runs.md)。安装命令、方法签名、示例和各语言的生命周期处理请查阅客户端仓库。
