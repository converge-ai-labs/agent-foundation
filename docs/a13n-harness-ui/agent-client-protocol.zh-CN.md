---
title: 通过 ACP 将客户端连接到 Harness UI
description: 在 Zed 或 JetBrains 中使用 Harness UI 的 ACP v1 Agent，或从其他程序通过 stdio 以无人值守模式启动。
---

`a13n-harness-ui acp` 在 macOS 和 Linux 上通过标准输入与标准输出提供 [Agent Client Protocol（ACP）](https://agentclientprotocol.com/) v1 服务，底层复用本地 Harness UI 配置与 SQLite 持久化 Thread，诊断日志输出至标准错误。

## 连接编辑器

先按 [Harness UI 设置指南](setup.md)配置好 Model 与 Agent，再在终端运行 `command -v a13n-harness-ui` 获取可执行文件的绝对路径（图形界面编辑器通常无法继承终端的 `PATH`）。

在 Zed 的设置中添加自定义 Agent：

```json
{
  "agent_servers": {
    "Harness UI": {
      "type": "custom",
      "command": "/absolute/path/to/a13n-harness-ui",
      "args": ["acp"]
    }
  }
}
```

在 JetBrains AI Assistant 中添加自定义 ACP Agent（通过 `~/.jetbrains/acp.json` 配置）：

```json
{
  "agent_servers": {
    "Harness UI": {
      "command": "/absolute/path/to/a13n-harness-ui",
      "args": ["acp"]
    }
  }
}
```

在客户端中选择 **Harness UI** 并发送一条简短提示即可验证连接。在交互模式下，Harness UI 会将工具审批请求转发给编辑器，提供单次生效的 **Allow once**（仅允许本次）和 **Reject once**（仅拒绝本次）选项，不修改已保存的权限规则。

如需指定其他配置文件，请将全局参数写在 `acp` 子命令之前：

```json
"args": ["--config", "/absolute/path/to/a13n-harness-ui.yaml", "acp"]
```

`acp` 支持 `--config`、`--data-root` 和 `--no-update-check`。`--resume`、`--agent`、`--environment-mode` 与 `--environment-profile` 会在启动时被拒绝，因为 ACP 通过协议选择 Thread，并直接使用配置中的默认 Agent 与 Environment。

## 由其他程序启动

通过管道启动 `a13n-harness-ui acp --unattended`，让 ACP v1 客户端经由标准输入与标准输出通信，并从标准错误读取诊断信息：

1. 调用 `initialize`。
2. 传入工作区绝对路径 `cwd` 调用 `session/new`，或传入已保存的会话 ID 及其首个本地根目录调用 `session/resume`（恢复失败直接返回错误，不会新建会话）。
3. 发送 `session/prompt`，在等待响应期间监听 `session/update` 实时更新，并通过 `session/cancel` 中止当前轮次。待收到终止响应后再确认最终答案。

无人值守模式会隐藏需要外部输入的工具，以及生效权限为 `ask` 或 `deny` 的工具。已配置的 MCP 工具和其他非交互式工具仍遵循常规权限。配置允许时仍可使用 Shell，若某条命令在审查后需要人工审批，该次工具调用会失败。已有待处理决定的 Thread 无法进入无人值守运行，请先在交互界面（TUI 或 WebUI）中处理。只需执行单条无头提示时，请使用始终以无人值守模式运行的 [`a13n-harness-ui run`](command-reference.md#run)。

## 使用 a13n 扩展

`initialize` 响应会在 `_meta` 中声明 `dev.a13n.harness-ui`（`version: 1`）。标准 ACP 客户端可忽略该命名空间；支持该扩展的客户端可使用资源清单、用量上报、严格中途引导及无人值守会话 MCP 声明：

| 消息 / 方法                              | 位置 / 方向                                      | 字段与行为                                                                                                                                     |
| ---------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `initialize`                             | 响应 `_meta["dev.a13n.harness-ui"]`              | 返回 `version: 1` 和 Agent 是否采用 `unattended` 模式。                                                                                        |
| `_a13n/inventory`                        | `sessionId`                                      | 列出模型、推理选项、Skills、已选 MCP 服务名及未纳入的项目和原因。工具列表根据最近一次准备运行时的结果报告未纳入的工具。                        |
| `session/set_config_option`              | 标准 ACP 请求（轮次之间调用）                    | 设置仅在当前连接生效的 `model` 与 `thought_level` 覆盖项，不修改已保存默认值（恢复会话时清除）。                                               |
| `session/prompt`                         | 请求 `_meta["dev.a13n.harness-ui"]`              | 可选的全新 `turnId`（用于启用 `_a13n/steer`）和 `inputIds`（每个内容块对应一个唯一 ID）。                                                      |
| `_a13n/input_accepted`                   | 服务端通知                                       | `{"sessionId", "turnId", "inputId"}`，表示输入已进入主模型请求检查点。                                                                         |
| `_a13n/steer`                            | 请求（`sessionId`、`turnId`、`inputId`、`text`） | 返回 `{"delivery": "accepted" \| "not_delivered" \| "unknown"}`。`not_delivered` 表示该轮未收到该输入；返回 `unknown` 或连接中断时请勿重发。   |
| `session/update` 与 `_a13n/usage_report` | 服务端通知                                       | 上下文容量和用量已知时，`usage_update` 携带 `usageReport`。否则，服务端发送 `_a13n/usage_report`。标准 ACP 客户端可以忽略此扩展。              |
| `session/prompt` 响应                    | 响应 `_meta["dev.a13n.harness-ui"]`              | 携带 `finalOutput`、`finalOutputOmitted` 及已提交的 `finalMessage`（`continuationId`、`position`），用于将最终提交答案与先前的实时输出区分开。 |

在同一连接的同一会话中，每次传入 `turnId` 时都要使用新 ID。服务端只跟踪当前轮次，由客户端保证 ID 不重复。

### 传入会话级 stdio MCP 服务

可在 `session/new` 或 `session/resume` 的 `mcpServers` 中传入 stdio MCP 配置（仅在当前连接有效，重连后需重新传入）。在 `--unattended` 模式下，请在同一请求的 `_meta["dev.a13n.harness-ui"]` 中列出你信任且允许无人值守启动的 stdio 服务。此声明仅允许启动服务。工具调用仍遵循配置的权限，MCP 服务也不能通过 elicitation 向用户请求输入：

```json
{
  "cwd": "/absolute/path/to/workspace",
  "mcpServers": [
    { "name": "repo-tools", "command": "/absolute/path/to/mcp-server", "args": [], "env": [] }
  ],
  "_meta": {
    "dev.a13n.harness-ui": {
      "version": 1,
      "unattendedSafeMcpServers": ["repo-tools"]
    }
  }
}
```

无人值守模式下未声明的服务不会启动，并在响应的 `_meta["dev.a13n.harness-ui"].omittedMcpServers` 中报告（`[{"name": "...", "reason": "unattended_safety_not_declared"}]`）。

如需管理 Project、Thread 与 Inbox，请单独使用 [WebUI 管理 MCP 端点](http-api.md#manage-projects-threads-and-inbox-over-mcp)。

> [!WARNING]
> ACP 进程运行在已配置的 Harness UI Environment 中，不受编辑器沙箱规则约束。请仅提供可信的 stdio MCP 命令，这些命令会直接在 ACP 宿主机上执行。
