---
title: 软件包目录
description: 全面概览 Agent Foundation 生态下的所有分发包、源码目录、运行时客户端与官方可运行示例项目。
---

在此查阅 Agent Foundation 生态系统的全部可安装包、多语言客户端、原生系统守护进程与架构参考工程。若您仍在评估适合自己的系统形态，请先阅读[选择使用方式](choose-your-path.md)。

## Python 软件包

所有核心 Python 库均基于现代 Python (3.13+) 构建，并统一使用连字符命名规范发布。在 Python 代码中导入时，请使用下划线：

| 分发包名 | 源码目录 | 核心定位与参考指南 |
| :--- | :--- | :--- |
| `a13n-harness` | `packages/a13n-harness` | [核心 Agent 编排与执行引擎](../a13n-harness/index.md)，提供状态持久化、工具链与 Capabilities 扩展 |
| `a13n-stream-protocol` | `packages/a13n-stream-protocol` | [事件流转换协议](../a13n-stream-protocol/index.md)，将 Harness 内部执行事件转换为类型化 AG-UI 规范 |
| `a13n-harness-ui` | `packages/a13n-harness-ui` | [交互式开发者工作台](../a13n-harness-ui/index.md)，包含命令行终端 TUI、全功能 WebUI 与可嵌入 App 模块 |
| `a13n-envd-client` | `packages/a13n-envd-client` | [Python EIP 客户端](../a13n-envd/python-client.md)，用于同 Envd 守护进程、安全沙箱及执行系统通信 |
| `a13n-service` | `packages/a13n-service` | [企业级分布式平台](../a13n-service/index.md)，提供多租户工作空间、持久化运行队列与全套 HTTP 管理接口 |
| `a13n-logging` | `packages/a13n-logging` | [结构化日志框架](../a13n-logging/index.md)，专为智能体系统优化全链路上下文与追踪关联 |

跨本地系统、Docker 容器与云端沙箱获得统一的文件读写与命令执行抽象，可通过 `a13n-harness` 直接挂载 [Environments](../environments/index.md)。

## 原生守护进程

**Envd** 是基于 Rust 开发的高性能系统守护进程（位于 `crates/a13n-envd`）。它通过轻量级 EIP 协议提供强隔离的进程管控、桌面自动化（Computer Use）以及精细的出网流量策略（Egress Validation）。

- 独立部署：直接[安装可执行程序](../a13n-envd/installation.md)，[阅读配置手册](../a13n-envd/configuration.md)，并通过 [Python 客户端](../a13n-envd/python-client.md)轻松连接。
- Harness 集成：在环境配置中选择 `local_envd` 提供方，即可在运行时自动调用本地沙箱能力。

## Service SDK 与 CLI

官方提供多语言 SDK 与 CLI 工具，方便从业务系统中快速集成与调用 Service 平台：

- **Python SDK：** `a13n-service-sdk`
- **TypeScript / Node.js SDK：** `@a13n/service-sdk`
- **Go SDK：** `github.com/converge-ai-labs/a13n-service-go`
- **Rust SDK 与 CLI：** `a13n-service-rs`（包含 `a13n-service-cli` 命令行工具）

详细安装命令与调用示例请参阅 [SDK 与 CLI 指南](../a13n-service/sdks.md)，接口定义详见 [HTTP API 参考](../a13n-service/api-reference/index.md)。

## 前端源码

各前端资产在打包时已完整编译并内嵌于对应的 Python 分发包中，这意味着运行 Service 控制台或 Harness UI 时无需宿主机安装任何 Node.js 运行时环境：

| 前端应用 / 模块 | 源码目录 | 主要职责 |
| :--- | :--- | :--- |
| `a13n-console` | `frontend/apps/a13n-console` | 现代化的 Web 管理控制台，用于配置 Agent、观测执行全链路与管理团队资源 |
| `a13n-harness-ui` | `frontend/apps/a13n-harness-ui` | Harness UI 工作台的 Web 界面，支持代码协作与实时会话交互 |
| `a13n-ui` | `frontend/packages/a13n-ui` | 跨端共享的 React UI 组件库、设计令牌（Design Tokens）与品牌资产 |
| `a13n-docs` | `frontend/apps/a13n-docs` | 基于 Fumadocs 构建的技术文档门户 |
| `a13n-site` | `frontend/apps/a13n-site` | 项目官方介绍与产品展示站点 |

参与前端页面开发与样式调试，请遵循[前端开发指南](https://github.com/converge-ai-labs/agent-foundation/blob/main/frontend/README.md)。

## 可运行的示例项目

在仓库 `examples/` 目录下提供了一系列完整且可直接运行的工程参考示例：

| 示例工程 | 核心架构与场景演示 |
| :--- | :--- |
| [Agent Application](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/agent-app/README.md) | 离线事件流式输出、基于检查点的跨轮状态持久化与进程重启无缝恢复 |
| [Environment Providers](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/environment-provider/README.md) | Direct Local 本地路径、Local Envd 沙箱与 Docker 容器执行生命周期管理 |
| [Capabilities & Plugins](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/README.md) | 自定义 Agent Capabilities、Harness 中间件插件与运行时扩展 |
| [Provider Plugin](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/provider-plugin/README.md) | 通过 Python 标准 entry-point 机制打包与动态发现自定义执行环境提供方 |
| [MCP App](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/mcp-apps/README.md) | 基于 stdio MCP 协议实现的富交互式网页组件与客户端工具通信 |

## 发布

各个组件根据运行时依赖与二进制契约进行版本归组管理：

- **Harness 与 Stream Protocol：** 统一发版，版本号保持完全一致。
- **Envd 与 Python 客户端：** 基于底层通信协议协同发版。
- **Service、Harness UI 与 Logging：** 独立版本演进；各软件包在元数据中明确声明所兼容的依赖范围。

详细的发布管理规范请参阅[贡献指南中的发布说明](https://github.com/converge-ai-labs/agent-foundation/blob/main/CONTRIBUTING.md#releases)与[仓库模型设计](https://github.com/converge-ai-labs/agent-foundation/blob/main/spec/repository-model.md)。
