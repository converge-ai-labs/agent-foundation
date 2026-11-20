---
title: 软件包与组件目录
description: 查询 Agent Foundation 各组件的 PyPI 发行包名、Python 导入路径、原生守护进程、多语言 SDK、可运行示例工程与版本发布组。
---

本目录汇总了 Agent Foundation 生态中所有可安装的软件包、源码路径与可运行示例工程。如果你仍在评估哪种产品形态最贴合你的业务架构，请先阅读[选择适合你的路径](choose-your-path.md)。

## Python 软件包

安装时使用带连字符的 PyPI 发行包名称（如 `a13n-harness`），在 Python 代码中导入时使用下划线名称（如 `a13n_harness`）。

| PyPI 发行包            | 仓库路径                        | 核心职责与文档入口                                                                                                         |
| ---------------------- | ------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `a13n-harness`         | `packages/a13n-harness`         | [可嵌入的 Python Agent SDK](../a13n-harness/index.md)：Agent 组装、工具与 Capabilities、状态持久化及环境提供方             |
| `a13n-stream-protocol` | `packages/a13n-stream-protocol` | [AG-UI 事件流转换器](../a13n-stream-protocol/index.md)：将 Harness 观测流转换为类型化前端事件并支持检查点重放              |
| `a13n-harness-ui`      | `packages/a13n-harness-ui`      | [终端与浏览器交互工作台](../a13n-harness-ui/index.md)，亦可作为[可嵌入的 Python App](../a13n-harness-ui/embedding.md) 调用 |
| `a13n-envd-client`     | `packages/a13n-envd-client`     | [Python EIP 客户端](../a13n-envd/python-client.md)：通过 HTTP、WebSocket 或 stdio 与 Envd Session 通信                     |
| `a13n-service`         | `packages/a13n-service`         | [自托管多租户 Agent 平台](../a13n-service/index.md)：包含 Console、RBAC 权限、Memory Spaces、定时调度与持久化 Worker       |
| `a13n-logging`         | `packages/a13n-logging`         | [结构化日志组件](../a13n-logging/index.md)：供 Python 库与可执行程序统一配置日志输出                                       |

跨平台执行层 [Environments](../environments/index.md) 内置在 `a13n-harness` 中，既能配合 Agent 使用，也能在普通 Python 程序中独立管理文件与命令。

## 原生执行守护进程 (Envd)

**Envd**（`crates/a13n-envd`）是由 Rust 编写的轻量级执行守护进程，通过 EIP（Environment Interface Protocol）协议提供文件读写、命令执行、后台进程管理、输出流留存、桌面 GUI 操控（Computer Use）以及设备指标采集。

- 独立部署或远程接入：参阅[安装守护进程](../a13n-envd/installation.md)、[配置沙箱与出站规则](../a13n-envd/configuration.md)，并使用 [Python 客户端](../a13n-envd/python-client.md)连接。
- 配合 Harness 与 Harness UI：选择 `local_envd` 提供方即可启用本机操作系统级沙箱；Harness UI 支持自动下载匹配当前版本的 `a13n-envd` 可执行文件。

## Service 多语言 SDK 与远程 CLI

业务应用可通过官方提供的 Python、TypeScript、Go 或 Rust 客户端 SDK 调用 Service HTTP API，或使用 Rust SDK 仓库发布的 `a13n-service-cli` 在命令行管理远程工作空间。

- 参阅 [SDK 与 CLI 指南](../a13n-service/sdks.md)获取各语言的安装方式与快速接入代码。
- 查阅 [Service HTTP API 参考](../a13n-service/api-reference/index.md)了解完整的接口参数定义。

## 前端应用源码

| 仓库路径                        | 用途说明                                                                                     |
| ------------------------------- | -------------------------------------------------------------------------------------------- |
| `frontend/apps/a13n-console`    | Service 的浏览器端管理界面（**Console**），用于工作空间管理、Agent Composer 构建与运行排障   |
| `frontend/apps/a13n-harness-ui` | Harness UI 的浏览器界面（**WebUI**），支持对话协作、文件树浏览、Git 变更对比与 Live 语音模式 |
| `frontend/packages/a13n-ui`     | 跨应用共享的 React 组件库、设计 Token 与品牌资源                                             |
| `frontend/apps/a13n-docs`       | 基于 `docs/` 与 `proto/a13n-service/openapi.json` 构建的 Fumadocs 文档站点                   |
| `frontend/apps/a13n-site`       | 官方网站首页与技术博客                                                                       |

Console 和 WebUI 的前端构建产物已直接打包进 `a13n-service` 和 `a13n-harness-ui` 的 Python wheel 与 sdist 发行包中，因此在生产或本地运行这两款应用时无需安装 Node.js。如需修改前端源码，请参阅 [`frontend/README.md`](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md)。

## 可运行示例工程

仓库的 [`examples/`](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/README.md) 目录提供了多个开箱即用的完整示例工程：

| 示例工程                                                                                                                        | 演示能力                                                             |
| ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| [Agent application](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md)                | 多轮离线流式对话、JSON 状态持久化与进程重启恢复                      |
| [Environment providers](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md) | Direct Local、Local Envd 与 Docker 环境的完整生命周期及文件/命令操作 |
| [Capabilities and plugins](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md)           | 自定义 Capability、Harness 插件、环境提供方插件与运行期扩展          |
| [Installed provider plugin](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/provider-plugin/README.md)  | 将自定义环境提供方打包为独立 Python 包并通过 entry point 自动发现    |
| [MCP App](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/mcp-apps/README.md)                           | 构建带有交互式浏览器 UI 组件的 stdio MCP 服务端                      |

更多云沙箱与容器环境脚本请参阅 [Environment 可运行示例](../environments/examples.md)。

## 发布组与版本兼容契约

各组件按发布组协调版本号：

| 发布组                           | 版本关联策略                                                                             |
| -------------------------------- | ---------------------------------------------------------------------------------------- |
| **Harness 与 Stream Protocol**   | 始终以完全相同的版本号同步发布；`a13n-stream-protocol` 严格锁定同版本的 `a13n-harness`。 |
| **Envd 与 `a13n-envd-client`**   | 原生二进制、沙箱容器镜像与 Python 客户端始终以同一版本号同步发布。                       |
| **Service、Harness UI、Logging** | 按各自节奏独立发布，在包元数据中精确锁定经验证的 Harness 版本及兼容依赖区间。            |

如需参与源码贡献或了解仓库架构边界，请参阅[贡献指南](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md)与[仓库模型规范](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md)。
