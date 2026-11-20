---
title: Harness UI
sidebarTitle: 概览
description: 面向开发者与 DIY 玩家的高效终端与浏览器工作台，深度集成代码仓库分析、多智能体协同与模块化能力扩展。
---

**Harness UI** 是专为个人开发者与 DIY 极客打造的交互式智能体工作台。无论您是偏好在全键盘命令行终端（TUI）中与 Agent 并肩审查 Git 提交并执行构建命令，还是喜欢在具备文件树、代码语法高亮 Diff 和全双工实时语音的现代浏览器（WebUI）中协作，Harness UI 都能为您提供开箱即用、以 Harness 为强劲引擎的高能操作体验。

## 安装并启动

仅需两步即可在您的代码仓库中开启协作：

1. [安装 Harness UI](installation.md)：安装统一的发行包，开箱即包含命令行终端工具与 WebUI 服务。
2. [设置首个 Agent](setup.md)：快速连接模型 API 密钥或订阅账号，授予执行权限，即可开启首次对话。

首次使用无需手写复杂的 YAML 配置文件或搭建数据库。一旦首次问答通过，即可立即在终端或浏览器中高效展开工作。

## 选择工作方式

Harness UI 能够无缝融入您习惯的日常开发动线：

- **终端命令行（TUI）：** 在任意工程根目录下直接运行 `a13n-harness-ui`。TUI 界面与您的 Shell 深度融为一体，支持极速全键盘操作、交互式决策卡片审批与思考中途实时引导（Steer）。[掌握终端交互指南](everyday-use.md)。
- **现代浏览器（WebUI）：** 运行 `a13n-harness-ui webui` 并点击控制台输出的链接。获得图文并茂的 Markdown 渲染、折叠式代码目录树、Git 变更 Diff 查看器以及内嵌的多标签终端。[探索 WebUI 使用指南](webui.md)。
- **全双工语音交互（Live Mode）：** 在 WebUI 中开启基于 WebRTC 的超低延迟实时语音通话，实现一边双眼审查代码 Diff、一边通过自然对话统筹 Agent 推进任务。[了解 Live 实时语音](live-mode.md)。

## 配置工作台

Harness UI 通过三大核心声明式概念组织您的开发环境：

| 核心概念 | 在工作台中的作用 | 配置途径 |
| :--- | :--- | :--- |
| **Model（模型）** | 定义提供商凭证、上游模型标识符与温度等生成参数 | `models/` 目录或交互式向导 |
| **Agent（智能体）** | 设定系统角色提示词、默认模型、工具授权与可用子智能体 | `agents/` 目录 |
| **Project（项目）** | 圈定工作空间目录范围、工程级本地配置与环境默认绑定 | `.a13n-project.yaml` 或项目根目录 |

查阅[常用配置方案](configuration-recipes.md)获取开箱即用的配置模板，或阅读[配置参考手册](configuration.md)查阅完整的 YAML 参数定义。

## 配置工作流

随着工程任务的深入，灵活定制与扩展您的专属工作台：

- **自由切换大模型：** 通过[模型接入与身份认证](models-and-authentication.md)，随心切换 OpenAI、Anthropic、Gemini 或本地 Ollama 服务。
- **自定义 Subagent 团队：** 参照[智能体与子智能体](agents-and-subagents.md)，配置专属的审查员、调研员等子角色。
- **固化标准化工程技能：** 通过 [Skills 与 Content Plugins](skills-and-content-plugins.md)，将团队的开发规范与操作规程沉淀为结构化 Markdown 技能包。
- **持久化工作备忘录：** 通过[文件记忆配置](configuration.md#file-memory)，让 Agent 跨会话牢记个人偏好与项目架构约定。
- **挂载外部工具生态：** 借助 [MCP](mcp.md) 协议接入外部生态，并通过 [MCP Apps](mcp-apps.md) 在网页端渲染动态响应式交互控件。
- **启用原生与网页工具：** 通过[原生与 Web 工具](native-and-web-tools.md)赋予 Agent 桌面自动化操作（Computer Use）与网页检索抓取能力。

详细的命令行启动参数与斜杠命令清单，请参阅[命令参考大全](command-reference.md)。

## 共享并继续工作

您的所有会话记录、执行日志与未发送的输入草稿均自动在本地持久化：

- **随时接续历史会话：** 无论关闭浏览器标签还是退出终端，正在运行的任务均不会丢失。在 TUI 中输入 `/resume` 或在 WebUI 侧边栏点选历史线程，即可无缝继续。
- **优雅的故障自愈：** 遭遇意外断电或终端进程崩溃时，Harness UI 会保护未提交的草稿并保留执行检查点，支持一键安全恢复。
- **局域网可信共享：** Harness UI 遵循个人与可信团队的高权限工作台定位。在局域网内共享实例时，协作者将共享实例内的凭证、文件与终端权限。

若您的企业需要基于细粒度 RBAC 权限隔离、集中审计与分布式 Worker 集群的企业级平台，请部署 [Service 平台](../a13n-service/index.md)。

## 基于 Harness UI 构建

Harness UI 同样向高阶开发者提供了丰富的可编程接入通道：

- **嵌入为 Python 应用：** 通过 [Python App 接口](embedding.md)，直接将完整工作台挂载并打包进自研的桌面客户端或本地微服务中。
- **通过 HTTP API 自动化：** 借助 [HTTP API](http-api.md)，轻松编写自动化调用脚本、接入编辑器插件或触发 Webhook。
- **底层裸引擎集成：** 若您仅需纯粹的 Agent 执行逻辑而无需 UI 层，请直接集成底层的 [Harness SDK](../a13n-harness/getting-started.md)。
