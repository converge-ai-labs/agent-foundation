---
title: Harness UI
sidebarTitle: 概览
description: 在终端和浏览器中与 Agent 工作，通过语音协调任务。
---

Harness UI 是面向个人和可信小团队的工作台。让 Agent 探索代码库、修改文件、运行检查或调查问题，并按任务选择 Model、工具、Skill 和执行 Environment。

## 安装并启动

通过 [uv](https://docs.astral.sh/uv/getting-started/installation/) 安装，然后在项目目录中启动：

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

设置向导通过受支持的订阅或 API 密钥连接 Model，并让你选择执行权限。Full Control 使用 Host 账户，Sandbox 使用受支持的 Linux/macOS 隔离机制。Windows 的内置执行支持 Full Control。具体步骤见[设置](setup.md)，升级方法见[安装](installation.md)。

试着发送第一条提示：

```text
Explain this repository's main entry point and tests. Do not modify any files.
```

## 选择工作方式

| 界面  | 适合的工作                               | 从这里开始                     |
| ----- | ---------------------------------------- | ------------------------------ |
| TUI   | 在终端中提问、引导和查看工作             | [使用 TUI](everyday-use.md)    |
| WebUI | 在浏览器中操作对话、文件、Git 变更和终端 | [使用 WebUI](webui.md)         |
| Live  | 在浏览工作空间时，通过语音讨论任务和决策 | [启用 Live Mode](live-mode.md) |

启动浏览器服务器：

```console
a13n-harness-ui webui
```

打开服务器打印的登录链接。浏览器中也有设置向导。Live 通过受支持的 Codex 订阅 Model 和浏览器麦克风权限，为 WebUI 提供连续语音对话。

## 配置工作流

从向导提供的默认配置开始，再按任务添加能力：

| 目标                         | 指南                                                     |
| ---------------------------- | -------------------------------------------------------- |
| 切换模型、账号或推理设置     | [模型与身份验证](models-and-authentication.md)           |
| 定义 Agent 并委派任务        | [Agent 与 subagent](agents-and-subagents.md)             |
| 组织代码库并选择工具执行位置 | [环境与 Project](environments-and-projects.md)           |
| 添加可复用的操作指引         | [Skill 与 Content Plugin](skills-and-content-plugins.md) |
| 连接外部工具和交互式结果     | [MCP](mcp.md) 与 [MCP Apps](mcp-apps.md)                 |
| 搜索网络、读取文件或媒体     | [原生工具与 Web 工具](native-and-web-tools.md)           |

在终端中，`/agent` 和 `/model` 更改当前选择，`/status` 显示设置与用量，`/resume` 打开保存的对话。完整命令见[命令参考](command-reference.md)。

## 配置工作台

默认配置保存在 `~/.a13n-harness-ui/`。根 YAML 选择默认设置，同级文件定义 Model、Agent、Project 和扩展。可以直接编辑文件，也可以使用 WebUI 的配置编辑器。

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

常见修改见[配置用法](configuration-recipes.md)，字段和优先级见[配置参考](configuration.md)。项目指令放在代码库的 `AGENTS.md` 中。

## 共享并继续工作

共享的 WebUI 实例让可信协作者访问同一组凭据、配置和文件。Host 原生文件访问和终端默认开启；`--no-share-computer` 可以关闭它们，与 Agent 执行权限分别设置。

Agent 工作时保持应用运行。关闭浏览器后，Run 仍会继续。应用重启后，从最后保存的检查点继续。[自动化与故障排查](automation-and-troubleshooting.md)介绍状态保存和恢复。

需要受管理的用户、角色和持久执行的团队平台时，使用 [Service](../a13n-service/index.md)。

## 基于 Harness UI 构建

嵌入 [Python App](embedding.md) 或调用其 [HTTP API](http-api.md)，可以构建其他界面。两者与 TUI、WebUI 使用相同的配置和对话存储。[Harness](../a13n-harness/index.md) 提供底层 Agent 执行库。
