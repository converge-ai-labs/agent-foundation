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

支持直接配置 token；环境引用是可选项。包含凭据的源文件应保持私有，不要提交到版本控制。Harness UI 不会将 MCP 源文本或字面的 environment/header 值复制到 `config show`、已接受配置版本或 Run 组合中：它保留源位置和摘要，再在 Run 启动时读取值。捕获的 Run 要求含字面值的源文件在客户端构造前仍存在且字节不变。可以编辑文件供新捕获的 Run 使用，但较早捕获的 Run 或子级续接可能报 `mcp_source_changed`；新 Run 应使用当前配置。已构造的普通客户端保留 Run 本地值。选择启用的 [MCP Apps 连接](mcp-apps.md) 可在 Run 后继续存在，但后续操作前会重新检查当前绑定和凭据。环境变量引用可以轮换而不修改源文件。

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

Agent 的 `mcp_servers: null` 继承根默认值；`[]` 显式不选任何服务器。现有会话保留确切的持久选择。普通客户端/进程为每次 Run 重新构造，不会保存到续接中。选择启用的 [MCP Apps](mcp-apps.md) 使用 App 所有的连接，Run 完成后仍可交互；它们也绝不会序列化到续接中。选择模型 Sandbox 不代表任意外部 MCP 命令或远程服务也处于沙箱中。

## MCP 字段参考

共享字段为 `schema_version: "1"`、`kind: mcp_server`、唯一的 `mcp-` `id` 和 `name`。`transport` 恰好接受一种形式：

| 形式 | 字段                                                                                           |
| ---- | ---------------------------------------------------------------------------------------------- |
| 命令 | 必需 `command`；`arguments` 默认 `[]`；`environment` 默认 `{}`，值为字符串或 `{env: VARIABLE}` |
| 远程 | 必需 `url`；`headers` 默认 `{}`，值为字符串或 `{env: VARIABLE}`                                |
