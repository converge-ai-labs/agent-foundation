---
title: 通过 ACP 将客户端连接到 Harness UI
description: 在 Zed 或 JetBrains 中使用 Harness UI 的 ACP v1 Agent，或从脚本启动，无需人工审批。
---

## 连接编辑器

在 macOS 或 Linux 上运行 Harness UI 的 ACP 命令。Windows 不支持此命令。先按 [Harness UI 设置指南](setup.md) 配置模型和 Agent。在终端运行 `command -v a13n-harness-ui` 找到可执行文件。建议在编辑器中填写绝对路径，避免图形界面读取不到终端的 `PATH`。

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

在 JetBrains AI Assistant 中添加自定义 ACP Agent。对于通过 `~/.jetbrains/acp.json` 配置的版本，可以填写：

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

在客户端选择 **Harness UI**，发送一条简短的提示来验证连接。如果希望在编辑器中审批工具调用，请保留交互模式。Harness UI 提供「仅允许本次」和「仅拒绝本次」，不会因此修改长期权限。如果要使用其他配置文件，请在参数列表中依次填写 `--config`、YAML 文件的绝对路径和 `acp`。

## 由其他程序启动

使用管道启动 `a13n-harness-ui acp --unattended`，让 ACP v1 客户端通过标准输入和标准输出通信，从标准错误读取诊断信息。先调用 `initialize`，再用工作区绝对路径 `cwd` 调用 `session/new`；如需继续已有会话，请用保存的会话 ID 和第一个已保存的根目录调用 `session/resume`。恢复失败不会创建新会话。等待 `session/prompt` 响应时，可接收 `session/update` 实时更新；要停止当前轮次，请调用 `session/cancel`。以终止响应为准，不要把流式文本直接当作最终答案。

无人值守模式会过滤需要人工输入的工具。配置允许时仍可使用 Shell；如果某条命令需要人工审批，该次工具调用会失败。已有待处理决定的会话不能启动新的无人值守运行，请在交互界面打开该会话并处理决定。只需发送一条提示时，可以改用 [`run`](command-reference.md#run)，该命令始终以无人值守模式运行。

## 使用 a13n 扩展

在初始化响应中读取 `_meta["dev.a13n.harness-ui"]`，确认是否支持版本 1。通过 `_a13n/inventory` 可查看会话的模型、推理选项、Skills 和 MCP 服务名称。在两轮之间使用 `session/set_config_option` 设置 `model` 和 `thought_level`，不会修改已保存的默认值。

服务端已知上下文容量和用量时，通过 `usage_update` 报告 token 用量。如果希望在任一数值未知时收到 `_a13n/usage_report`，请在 `initialize` 请求的 `_meta["dev.a13n.harness-ui"]` 中传入 `{"version": 1}`。

在提示的元数据中提供新的 `turnId`，即可通过 `_a13n/steer` 向该轮追加输入。可选的 `inputIds` 用来标记提示中的各个内容块。收到 `_a13n/input_accepted` 通知，表示输入已进入主模型请求的检查点。若追加输入返回 `not_delivered`，则该轮未收到该输入。若连接中断或返回 `unknown`，请勿重发该输入。成功的提示响应会单独标识已保存的最终答案，便于与实时输出区分。

创建或恢复会话时，可在 `mcpServers` 中提供 stdio MCP 命令。无人值守模式下，需要在同一请求的 a13n 命名空间中提供 `version: 1` 和 `unattendedSafeMcpServers: ["server-name"]`。只声明不需要人工输入的服务。请检查响应中的 `omittedMcpServers`；列在其中的服务不会启动。重新连接后，需要再次提供 MCP 服务及其声明。

如需管理项目、会话或调用 `post_to_inbox` 发布收件箱消息，请单独使用 [WebUI 管理 MCP 端点](http-api.md#manage-projects-threads-and-inbox-over-mcp)。ACP 会话只使用 Harness UI 配置选定的 MCP 服务，以及本会话请求获准使用的 stdio 服务。

> [!WARNING]
> ACP 进程使用 Harness UI 中配置的环境，不受编辑器沙箱规则约束。请只提供可信的 MCP 命令；stdio 命令在 ACP 宿主机上运行。
