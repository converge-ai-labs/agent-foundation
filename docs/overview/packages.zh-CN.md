---
title: 软件包目录
description: 发行包名、导入路径、源码位置、可运行示例和发布组。
---

在此目录中查找组件的可安装发行包、源码和示例。如果还在嵌入式执行、Service 和 Harness UI 之间选择，请先阅读[选择使用方式](choose-your-path.md)。

## Python 软件包

安装时使用 `a13n-harness` 这样的连字符发行包名；导入模块时使用下划线，例如 `a13n_harness`。

| 发行包                 | 源码                            | 用途与指南                                                                                                     |
| ---------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `a13n-harness`         | `packages/a13n-harness`         | [Agent 组合与执行](../a13n-harness/index.md)、工具、状态和 provider                                            |
| `a13n-stream-protocol` | `packages/a13n-stream-protocol` | [将 Harness 观测转换为 AG-UI 事件](../a13n-stream-protocol/index.md)                                           |
| `a13n-harness-ui`      | `packages/a13n-harness-ui`      | [终端和浏览器工作台](../a13n-harness-ui/index.md)，也可作为[可嵌入的 App](../a13n-harness-ui/embedding.md)使用 |
| `a13n-envd-client`     | `packages/a13n-envd-client`     | 用于 Envd 会话、文件、进程和输出的 [Python EIP 客户端](../a13n-envd/python-client.md)                          |
| `a13n-service`         | `packages/a13n-service`         | 提供身份管理、资源和持久执行的[托管 agent 运行时](../a13n-service/index.md)                                    |
| `a13n-logging`         | `packages/a13n-logging`         | 为库和应用提供[结构化日志](../a13n-logging/index.md)                                                           |

需要可移植的文件和命令访问时，使用 Harness 中的 [Environments](../environments/index.md)。同一套 provider 可用于普通应用和 agent 宿主。

## 原生守护进程

使用 **Envd**，通过 EIP Session 访问文件、命令、进程、保留输出和计算机操作。原生源码位于 `crates/a13n-envd`。独立部署时，先[安装守护进程](../a13n-envd/installation.md)，再[完成配置](../a13n-envd/configuration.md)，最后通过 [Python 客户端](../a13n-envd/python-client.md)连接。

在 Harness 中，选择 Local Envd provider，将守护进程用作环境。Harness UI 可以为该 provider 自动获取匹配的可执行文件。

## Service SDK 与 CLI

选择 Python、TypeScript、Go 或 Rust 客户端来调用 Service HTTP API。每种客户端都有自己的仓库；Rust 仓库还提供 `a13n-service-cli`。

按照 [SDK 与 CLI](../a13n-service/sdks.md)安装客户端并运行示例。通过 [HTTP 参考](../a13n-service/api-reference/index.md)查看 Service API。

## 前端源码

| 源码                            | 用途                                     |
| ------------------------------- | ---------------------------------------- |
| `frontend/apps/a13n-console`    | Service 的浏览器界面，用于资源和对话管理 |
| `frontend/apps/a13n-harness-ui` | Harness UI 的 WebUI                      |
| `frontend/packages/a13n-ui`     | 共享 React 组件、设计 token 和品牌素材   |
| `frontend/apps/a13n-docs`       | 从 `docs/` 构建的文档站                  |
| `frontend/apps/a13n-site`       | 项目官网首页                             |

安装 Service 即可获得 Console，安装 `a13n-harness-ui` 即可获得 WebUI。构建好的浏览器资源随所属应用的 wheel 和 sdist 发布，因此运行已安装的应用无需 Node.js。修改这些资源时，请遵循[前端开发指南](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md)。

## 可运行的示例项目

选择最接近集成需求的示例，再按照其 README 配置依赖、运行和测试：

| 示例                                                                                                                        | 演示内容                                           |
| --------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| [Agent 应用](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md)                   | 离线流式轮次、状态保存和重启恢复                   |
| [环境 provider](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md)     | Direct Local、Local Envd 和 Docker 的生命周期      |
| [能力与插件](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md)                     | 自定义能力、Harness 插件、环境 provider 和运行扩展 |
| [已安装的 provider 插件](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/provider-plugin/README.md) | 打包环境 provider 并加载已安装的入口               |
| [MCP App](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/mcp-apps/README.md)                       | 带交互式浏览器计数器的 stdio MCP 服务器            |

通过[示例索引](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/README.md)比较前置条件和运行命令。其他内置 provider 的用法见[环境示例](../environments/examples.md)。

## 发布

按发布组选择版本：

| 发布组                       | 版本关系                                               |
| ---------------------------- | ------------------------------------------------------ |
| Harness 和 Stream Protocol   | 使用相同版本；Stream Protocol 依赖该精确版本的 Harness |
| Envd 及其 Python 客户端      | 使用相同版本                                           |
| Harness UI、Service、Logging | 独立发布；软件包元数据声明兼容的依赖版本               |

根据已发布软件包的元数据选择部署版本。源码树中的版本是开发占位值，示例项目不参与生产发布。

开发软件包时，从[贡献指南](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md)开始。[仓库模型](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md)定义了软件包归属和发布关系。
