---
title: Harness UI
sidebarTitle: 概览
description: 在终端和浏览器中与 agent 一起处理真实项目的工作台。
---

Harness UI 是面向个人和可信小团队的 [Harness](../a13n-harness/index.md) 交互式试验工作台。你可以一边处理真实项目，一边尝试不同的模型、指令、工具、Skill 和执行环境。让 agent 解释代码库、修改文件、运行检查，或将范围明确的调查交给 subagent。

终端提供个人编程 agent 工作流；浏览器则支持共享对话和草稿、实时执行、文件、Git 变更、终端和配置编辑。两者使用同一个应用和 agent 执行基础，无须编写 SDK 代码或部署 Service。

## 安装并启动

使用 [uv](https://docs.astral.sh/uv/getting-started/installation/) 安装 `a13n-harness-ui` 命令：

```console
uv tool install a13n-harness-ui
cd your-repository
a13n-harness-ui
```

要启动浏览器工作台，运行：

```console
a13n-harness-ui webui
```

打开服务器打印的登录链接。浏览器内提供设置向导；协作、身份验证、宿主机原生访问和服务器生命周期见[使用浏览器](webui.md)。

终端支持 macOS、Linux 和 Windows。安装后的应用不需要 Node.js 或仓库检出目录。如果 PATH 中找不到命令，运行 `uv tool update-shell`，然后打开新终端。

首次启动时，设置向导会引导你完成：

1. **连接模型：** 使用支持的订阅登录或 API 密钥，也可以复用现有的兼容账户存储。
2. **选择模型设置：** 选择提供的模型，以及适用的服务层级。推理和上下文设置可以稍后调整。
3. **选择执行权限：** Full Control 使用你的宿主机账户；Sandbox 要求本地隔离正常可用，不会静默降级。
4. **发送第一条提示：** 设置向导保存可编辑的文件，然后打开聊天。在你发送提示之前，不会发出模型请求。

先试一个范围明确的任务：

```text
Explain this repository's main entry point and tests. Do not modify any files.
```

> [!WARNING]
> Full Control 使用宿主机账户的文件系统和网络权限运行命令，并非沙箱。Sandbox 要求受支持的 Linux/macOS 隔离机制；Windows 内置执行仅支持 Full Control。见[执行权限](environments-and-projects.md#execution-permissions)。

[安装与升级](installation.md)介绍源码开发和依赖更新；[设置](setup.md)介绍登录、取消和高级选项。

## 配置在哪里？

在 shell 中运行：

```console
a13n-harness-ui config path
a13n-harness-ui config show --format json
a13n-harness-ui config validate
```

也可以在聊天中输入 `/config`。默认根配置文件是 **`~/.a13n-harness-ui/a13n-harness-ui.yaml`**。Model 和 Agent 资源位于同级目录中，而不是写在该文件内：

| 要修改的内容                   | 文件或操作                                                                               |
| ------------------------------ | ---------------------------------------------------------------------------------------- |
| 默认 Agent、显示、内置工具     | `a13n-harness-ui.yaml`，见[根配置参考](configuration.md#complete-root-document)          |
| 模型、端点、凭据、推理、上下文 | `models/*.yaml`，见[Model 参考](models-and-authentication.md#model-file-reference)       |
| 指令、工具、MCP 选择、子 agent | `agents/*.yaml`，见[Agent 参考](agents-and-subagents.md#agent-file-reference)            |
| 全局编程指导                   | 根 YAML 旁的 `AGENTS.md`                                                                 |
| 项目专用指导                   | 工作目录中的 `AGENTS.md`                                                                 |
| 额外工作区目录                 | `projects/*.yaml`，见[Project 参考](environments-and-projects.md#project-file-reference) |

需要可直接复制的修改示例时，从[常见配置用法](configuration-recipes.md)开始；要了解所有根字段和优先级，阅读[配置指南](configuration.md)。`--config PATH` 选择另一棵配置树，不会将其与默认配置树合并。

## 终端操作

| 任务                            | 聊天中的操作         |
| ------------------------------- | -------------------- |
| 查看可用命令                    | `/help`              |
| 切换 Agent                      | `/agent`             |
| 选择并记住每个 Project 的 Model | `/model`             |
| 修改推理或优先服务              | `/thinking`、`/fast` |
| 修改执行权限                    | `/environment`       |
| 查看当前配置和用量              | `/status`            |
| 浏览已保存的对话                | `/resume`            |
| 在执行期间补充指导              | 输入消息并按 Enter   |
| 取消任务                        | Ctrl+C 或 `/cancel`  |

附件、审批、提问、历史和恢复见[使用终端](everyday-use.md)。在聊天之外运行 `a13n-harness-ui add model` 或 `a13n-harness-ui add agent`，可以添加可复用资源。

## 进一步使用

- **自定义：**[Agent 与 subagent](agents-and-subagents.md)、[模型与身份验证](models-and-authentication.md)。
- **连接工具：**[MCP 与扩展](extensions-and-mcp.md)、[原生工具与 Web 提供方](native-and-web-tools.md)。
- **跨目录工作：**[环境与 Project](environments-and-projects.md)。
- **编写任务脚本或排查故障：**[自动化与故障排查](automation-and-troubleshooting.md)。
- **构建其他界面：**[嵌入 Python App](embedding.md)或[使用 HTTP API](http-api.md)。

## 共享与执行边界

WebUI 实例只应与可信协作者共享：大家共用凭据、配置和可访问文件，没有分别设置的参与者权限。宿主机原生文件访问和终端默认开启；`--no-share-computer` 可以关闭它们，且与 Agent 的执行模式相互独立。

关闭浏览器不会停止正在运行的 Run；停止应用才会。已保存的对话可以从最后一个检查点恢复，但该检查点之后的输入或输出可能丢失。需要受管理的身份和可恢复执行时，使用 [Service](../a13n-service/index.md)。
