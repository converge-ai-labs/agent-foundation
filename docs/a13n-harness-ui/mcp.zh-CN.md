---
title: MCP 服务器
description: 配置命令和 Streamable HTTP MCP 服务器，并在 Agent 上启用。
---

配置命令或 Streamable HTTP 服务器，再在 Agent 上选择其资源 ID。创建服务器文件会注册资源；在 Agent 或默认值中选择 ID 才会启用。Harness UI shell 命令见[命令参考](command-reference.md)。MCP 命令传输会启动外部程序。

## 复制 JSON 配置

在选中的根配置旁创建 `mcp/servers.json`。常见的客户端式 `mcpServers` 对象可以直接使用：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/workspace"]
    },
    "docs": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "Authorization": "Bearer example-token",
        "X-Workspace": "${WORKSPACE_ID}"
      }
    }
  }
}
```

替换示例端点、路径和 token。在 Agent 或根 `defaults` 中设置 `mcp_servers: [mcp-filesystem, mcp-docs]` 以启用这些条目。只创建 JSON 文件不会启用它们。

一个 `mcpServers` 文件可定义多个服务器。名称会规范化为 `mcp-` ID：`My_Server` 变为 `mcp-my-server`。不同文件中的重复 ID 会导致配置被拒绝，因此添加第二个文件前先检查规范化 ID。

命令条目使用 `command`、可选 `args` 和可选 `env`；远程条目使用 `url` 和可选 `headers`。可选 `type` 对命令接受 `stdio`，对远程端点接受 `http` 或 `streamable-http`。远程连接使用 Streamable HTTP，不使用旧版 SSE。不支持 OAuth 登录、JSONC 注释、末尾逗号、`disabled` 和无关的客户端专用字段。`mcpServers` 是常见客户端惯例，不是通用的 MCP 协议配置标准。

`.yaml` 和 `.json` 都可以使用下述单资源格式，也都支持 `mcpServers` 包装。现有 YAML 文件无须迁移。

## 字面值与环境变量引用

命令 `env`（或规范字段 `transport.environment`）和远程 `headers` 支持：

| 值                      | 行为                                        |
| ----------------------- | ------------------------------------------- |
| `"example-token"`       | 字面字符串，包括普通非密钥设置              |
| `"${API_TOKEN}"`        | Run 启动时读取环境变量                      |
| `"Bearer ${API_TOKEN}"` | 替换字符串中出现的 `${NAME}`                |
| `{"env": "API_TOKEN"}`  | 已有的显式环境变量引用；YAML 和 JSON 均支持 |

空字面字符串和空白会保留。引用要求 **Harness UI 进程** 环境中的变量非空；在其他 shell 导出不会改变已运行的进程。展开只有一轮，只支持变量名合法的 `${NAME}`，不会执行 shell，也没有默认值语法。替换仅适用于 environment/header 值，不适用于命令、参数或 URL。

支持直接配置 token；环境引用是可选项。包含凭据的源文件应保持私有，不要提交到版本控制。Harness UI 不会将 MCP 源文本或字面的 environment/header 值复制到 `config show`、已接受配置版本或 Run 组合中：它保留源位置和摘要，再在 Run 启动时读取值。捕获的 Run 要求含字面值的源文件在客户端构造前仍存在且字节不变。可以编辑文件供新捕获的 Run 使用，但较早捕获的 Run 或子级续接可能报 `mcp_source_changed`；新 Run 应使用当前配置。已构造的普通客户端保留 Run 本地值。[Host 持有的连接](#connection-lifetime-and-protocol) 可在 Run 后继续存在，但保留精确的实际绑定；后续操作和 SDK 后续轮次不能使用已退役的绑定。环境变量引用可以轮换而不修改源文件。

编辑后运行 `a13n-harness-ui config validate`；如果修改了默认 MCP 选择，应启动新会话。验证不会连接服务器或检查凭据。

## 命令传输

创建 `mcp/github.yaml`：

```yaml title="mcp/github.yaml"
schema_version: "1"
kind: mcp_server
id: mcp-github
name: GitHub
transport:
  command: npx
  arguments: ["-y", "@modelcontextprotocol/server-github"]
  environment:
    GITHUB_TOKEN:
      env: GITHUB_TOKEN
```

此示例要求可执行文件/包和访问 token 可用。Harness UI 不会在解析文件时通过测试调用检查外部服务权限。启用前检查命令和包。

## 远程传输

使用实际 MCP 端点创建 `mcp/docs.yaml`：

```yaml title="mcp/docs.yaml"
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Documentation service
transport:
  url: https://mcp.example.com/mcp
  headers:
    Authorization:
      env: DOCS_MCP_AUTHORIZATION
```

环境变量应包含服务器要求的完整 header 值，包括身份验证方案。也可以用 `"Bearer ${DOCS_MCP_TOKEN}"` 只插入 token。URL 必须为不含凭据的 HTTPS；仅字面回环主机且未配置 headers 时允许明文 HTTP。经身份验证的重定向不能降低传输安全性，也不能将 headers 泄露到其他 origin。

## 启用服务器

在 Agent 文件中：

```yaml
mcp_servers: [mcp-github, mcp-docs]
```

或在根 YAML 中：

```yaml
defaults:
  mcp_servers: [mcp-github]
```

Agent 的 `mcp_servers: null` 继承根默认值；`[]` 显式不选任何服务器。现有会话保留确切的持久选择。默认情况下，客户端/进程为每次逻辑 Run 重新构造。根生命周期策略可以保留所选客户端，启用的 [MCP Apps](mcp-apps.md) 会自动保留其客户端。客户端状态和待处理 MCP 输入都不会序列化到续接中。选择模型 Sandbox 不代表任意外部 MCP 命令或远程服务也处于沙箱中。

## 连接生命周期与协议

要在 Run 之间保留服务器的进程内状态，在根文档配置：

```yaml
mcp:
  host_owned_servers: [mcp-docs]
  protocol_overrides:
    mcp-docs: auto
```

`host_owned_servers` 默认为 `[]`，只改变生命周期：仍须在 Agent 或 Thread 上选择服务器，已有工具过滤和权限规则仍适用。每个所属 Thread/服务器为精确的传输、凭据和协议绑定保留独立的已进入客户端。子 Thread 不借用父级客户端。没有空闲或浏览器观看者超时；Host 关闭、显式关闭、Thread 释放或绑定退役会结束该代连接。客户端和代 ID 属于进程内状态，不是持久化 MCP session。断开的代不会静默重连或重放业务调用；App 激活可以显式建立替代连接。

`protocol_overrides` 默认为 `{}`。每个配置的服务器 ID 接受 `auto`、`legacy` 或 `2026-07-28`；省略的 ID 使用 `auto`。覆盖控制上游 SDK 的 Core 协商，与连接生命周期和 [MCP Apps UI 线协议](mcp-apps.md) 相互独立。所有策略 ID 都须指向已有 MCP 资源。缺少这些字段的现有 YAML/JSON 和已保存 recipe 保留 Run 本地生命周期与自动协商。

## MCP 服务器请求的人工输入

CLI 和 WebUI 在原操作保持活动时呈现受支持的 MCP 表单与 URL 请求。它们不是 `ask_user_question`、延迟续接决策，也不会开启另一次 Run。根对话可以看到子 Thread 的请求，注明来源 Thread/服务器以及可用的 Run、工具调用或 App View 关联。

表单支持原始字符串、数值和布尔字段、单选枚举和字符串多选枚举，包括带标题的选项。Host 根据原 schema 验证答案。不支持的 schema、远程引用和声明为敏感的字段会被拒绝；切勿在 MCP 表单输入密码、token 或其他密钥。URL 请求显示目的地，需要在 App iframe 之外主动打开浏览器；完成外部流程后再确认。

在五分钟内选择接受、拒绝或取消。取消、过期、连接退役或进程关闭会使实时请求不可用。响应绕过业务派发通道，避免等待中的调用死锁。完全相同的重复响应核实同一请求，冲突响应会被拒绝。交付不确定时，仅重试已提交的确切答案或查看当前状态。接受只确认输入交付，不代表业务完成或远程无副作用。无界面嵌入默认不声明输入处理器，除非显式启用；见[嵌入](embedding.md#mcp-input-and-integration-control)。

## MCP 字段参考

共享字段为 `schema_version: "1"`、`kind: mcp_server`、唯一的 `mcp-` `id` 和 `name`。`transport` 恰好接受一种形式：

| 形式 | 字段                                                                                           |
| ---- | ---------------------------------------------------------------------------------------------- |
| 命令 | 必需 `command`；`arguments` 默认 `[]`；`environment` 默认 `{}`，值为字符串或 `{env: VARIABLE}` |
| 远程 | 必需 `url`；`headers` 默认 `{}`，值为字符串或 `{env: VARIABLE}`                                |
