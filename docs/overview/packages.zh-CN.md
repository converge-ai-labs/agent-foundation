---
title: 软件包目录
description: 发行包名、导入路径、源码位置、可运行示例和发布组。
---

查找适合你的集成所需的库、应用或示例。Python 发行包名使用连字符（`a13n-harness`），导入路径使用下划线（`a13n_harness`）。

## Python 软件包

| 发行包                 | 源码                            | 用途与指南                                                                                                     |
| ---------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `a13n-harness`         | `packages/a13n-harness`         | [Agent 组合与执行](../a13n-harness/index.md)、工具、状态和 provider                                            |
| `a13n-stream-protocol` | `packages/a13n-stream-protocol` | [将 Harness 观测转换为 AG-UI 事件](../a13n-stream-protocol/index.md)                                           |
| `a13n-harness-ui`      | `packages/a13n-harness-ui`      | [终端和浏览器工作台](../a13n-harness-ui/index.md)，也可作为[可嵌入的 App](../a13n-harness-ui/embedding.md)使用 |
| `a13n-envd-client`     | `packages/a13n-envd-client`     | 用于 Envd 会话、文件、进程和输出的 [Python EIP 客户端](../a13n-envd/python-client.md)                          |
| `a13n-service`         | `packages/a13n-service`         | 提供身份管理、资源和持久执行的[托管 agent 运行时](../a13n-service/index.md)                                    |
| `a13n-logging`         | `packages/a13n-logging`         | 为库和应用提供[结构化日志](../a13n-logging/index.md)                                                           |

[Environments](../environments/index.md) 是 Harness 的一部分，也可以在不使用 agent 的情况下单独使用。

## 原生守护进程

`crates/a13n-envd` 构建 **Envd** 守护进程。它通过 EIP 提供 Envd 会话、文件、命令、进程、保留输出和计算机操作。请参阅[安装](../a13n-envd/installation.md)、[配置](../a13n-envd/configuration.md)和 [Python 客户端](../a13n-envd/python-client.md)。

Harness 的 Local Envd provider 将守护进程连接到环境。选择此 provider 时，Harness UI 可以自动获取匹配的可执行文件。

## Service SDK 与 CLI

Service 客户端分别位于独立的 Python、TypeScript、Go 和 Rust 仓库。Rust 仓库还提供 `a13n-service-cli`。这些客户端调用 Service HTTP API；要直接嵌入 agent 执行，请使用 Harness。

[SDK 与 CLI](../a13n-service/sdks.md)链接到各客户端的安装说明和示例。[HTTP 参考](../a13n-service/api-reference/index.md)描述本仓库的 Service API。

## 前端源码

| 源码                            | 用途                                     |
| ------------------------------- | ---------------------------------------- |
| `frontend/apps/a13n-console`    | Service 的浏览器界面，用于资源和对话管理 |
| `frontend/apps/a13n-harness-ui` | Harness UI 的 WebUI                      |
| `frontend/packages/a13n-ui`     | 共享 React 组件、设计 token 和品牌素材   |
| `frontend/apps/a13n-docs`       | 从 `docs/` 构建的文档站                  |
| `frontend/apps/a13n-site`       | 项目官网首页                             |

Console 随 Service 发布，WebUI 随 `a13n-harness-ui` 发行包发布。两者都打包在各自的 wheel 和 sdist 中，安装使用时无需 Node.js。前端开发请遵循[前端 README](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md)。

## 可运行的示例项目

每个项目都有自己的依赖、测试和运行说明。请从各自的 README 开始：

| 示例                                                                                                                        | 演示内容                                           |
| --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| [Agent 应用](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md)                   | 离线流式轮次、状态保存和重启恢复                   |
| [环境 provider](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md)     | Direct Local、Local Envd 和 Docker 的生命周期      |
| [能力与插件](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md)                     | 自定义能力、Harness 插件、环境 provider 和运行扩展 |
| [已安装的 provider 插件](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/provider-plugin/README.md) | 打包环境 provider 并加载已安装的入口               |
| [MCP App](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/mcp-apps/README.md)                       | 带交互式浏览器计数器的 stdio MCP 服务器            |

[示例索引](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/README.md)列出了运行命令和前置条件。[环境示例](../environments/examples.md)介绍了其他内置 provider。

## 发布

| 发布组                       | 版本关系                                               |
| ---------------------------- | ------------------------------------------------------ |
| Harness 和 Stream Protocol   | 使用相同版本；Stream Protocol 依赖该精确版本的 Harness |
| Envd 及其 Python 客户端      | 使用相同版本                                           |
| Harness UI、Service、Logging | 独立发布；软件包元数据声明兼容的依赖版本               |

前端随其所属应用发布。示例项目不属于生产发布。源码树中的软件包版本是开发占位值；选择部署版本时请以已发布软件包的元数据为准。

贡献者环境配置请参阅[贡献指南](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md)。仓库和发布约定请参阅[仓库模型](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md)。
