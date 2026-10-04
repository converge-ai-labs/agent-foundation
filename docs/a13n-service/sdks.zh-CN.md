---
title: SDK 与 CLI
description: 通过 Service SDK 或远程 CLI 集成，无需直接处理 HTTP。
---

使用 Service SDK 将应用接入运行中的 Service，或使用远程 CLI 编写 shell 脚本和执行终端操作。这些客户端调用 Service HTTP API，不会在你的进程中运行 agent。嵌入式执行请使用 [Harness](../a13n-harness/index.md)，在代码仓库中与 agent 交互式工作请使用 [Harness UI](../a13n-harness-ui/index.md)。

## 选择客户端

| 客户端                            | 适用场景                           | 各自仓库中的文档                                                                                                                                                                                           |
| --------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Python（`a13n`）                  | 异步 Python 应用                   | [快速入门](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/README.md) · [应用指南](https://github.com/converge-ai-labs/a13n-sdk-python/blob/main/docs/README.md)                             |
| TypeScript（`@converge.ai/a13n`） | Node.js 应用和同源浏览器集成       | [快速入门](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/README.md) · [应用指南](https://github.com/converge-ai-labs/a13n-sdk-typescript/blob/main/docs/README.md)                     |
| Go                                | 使用 context 取消机制的 Go 应用    | [快速入门](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/README.md) · [应用指南](https://github.com/converge-ai-labs/a13n-sdk-go/blob/main/docs/README.md)                                     |
| Rust（`a13n`）                    | 异步 Rust 应用                     | [快速入门](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/README.md) · [应用指南](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/docs/README.md)                                 |
| 远程 CLI（`a13n-service-cli`）    | Shell 脚本、API 探索和显式资源操作 | [命令行为](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/a13n-service-cli/README.md) · [工作流](https://github.com/converge-ai-labs/a13n-sdk-rust/blob/main/a13n-service-cli/docs/README.md) |

各 SDK 仓库负责安装、语言专属方法、示例、兼容性和发布。先阅读快速入门，再按应用指南的任务章节了解对话、流式输出、等待操作、文件与记忆、认证和恢复。CLI 工作流指南介绍等价的 shell 任务和脚本边界。这些指南是 GitHub 上的普通 Markdown 文件，没有单独的文档网站。CLI 属于 Rust SDK 仓库，但拥有独立的可执行程序和发布渠道，与 `a13n-service` 服务器及运维命令不同。

## 连接部署

准备 Service URL 和目标工作空间的凭据：API 密钥在自身所属工作空间中生效，登录会话则指定工作空间 ID（参阅 [HTTP 约定](http.md#workspace)）。提交消息还需要选择已有 agent。[连接你的应用](connect-application.md)介绍凭据、agent 选择和首次请求。[快速入门](get-started.md)介绍部署；[身份与访问](identity.md)说明 API 密钥和登录会话的区别。

## 调用 agent

SDK 在相同的传输和认证机制上提供两层 API：

- **底层 API：** 从导出的 Service schema 生成类型和操作，提供完整的协议访问和显式资源管理。
- **高层 API：** 按各语言惯例手工编写的方法，用于启动 agent 和发送输入。启动 agent 或发送新输入会返回一个有限的 **Interaction** ；同一对象提供事件流迭代、结果读取、线程引用和原始提交回执。

Interaction 观察实际并入其输入的运行，包括排队时间。只有当运行消费了排队的条目后，Interaction 才跟随该运行；分配给某个运行的条目，如果该运行在完成或等待时没有并入它，会回到 `pending`。该运行完成、失败、取消或等待人工输入、审批、客户端工具结果时，迭代结束，不会继续跟随线程的后续运行。等待是需要显式处理的结果，并不授权 SDK 自动批准或恢复。只需要结果的应用可以直接等待结果，无需打开事件流。

事件流承载临时观测，而不是完整对话记录或持久化结果。保留期限、事件缺口和线程当前运行的变化可能限制回放；回读时请使用持久化结果和已提交的运行显示项。底层 API 仍可访问线程事件流，但这不是另一种高层交互模式。

Service 负责授权和持久执行。关闭 Interaction、超时或断开连接只会停止本地观测，运行仍会继续；中断运行需要单独的显式操作。原生资源清理、条件写入、结构化输入和恢复请遵循所选客户端的指南。

## 明确版本归属

这些仓库独立于 Service 管理版本，并固定所支持的 Service 协议约定。请核对客户端的协议来源和发布文档是否匹配你的部署，阅读所用版本的文档，不要假定 `main` 与已安装的软件包一致。

本站通过 [HTTP 约定](http.md)、[HTTP 参考](api-reference/index.md)和 [Agent、线程与运行](agents-and-runs.md)介绍 Service 行为。SDK 方法签名和命令用法由各自仓库维护，因此本站不重复列出。
