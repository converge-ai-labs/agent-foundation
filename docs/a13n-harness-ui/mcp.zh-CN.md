---
title: MCP 服务器
description: 在 Harness UI 中接入 stdio 与 Streamable HTTP MCP 服务器，配置 OAuth、工具过滤、宿主托管连接与人工输入。
---

通过 Model Context Protocol（MCP）服务器为智能体接入外部工具与数据源。在 `mcp_servers/*.yaml` 或 **Settings → Connections → MCP** 中定义服务器。

## 添加 MCP 服务器

在根配置旁创建 stdio 或 Streamable HTTP 服务器文件：

```yaml title="mcp_servers/docs.yaml — stdio 服务器"
schema_version: "1"
kind: mcp_server
id: mcp-docs
name: Docs MCP
transport: stdio
command: npx
args: ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/docs"]
```

```yaml title="mcp_servers/remote.yaml — Streamable HTTP 服务器"
schema_version: "1"
kind: mcp_server
id: mcp-remote
name: Remote MCP
transport: streamable_http
url: https://mcp.example.com/mcp
headers:
  Authorization:
    env: REMOTE_MCP_TOKEN
```

在项目 `defaults.mcp_servers`、智能体 `defaults.mcp_servers` 或对话设置中启用该服务器，然后运行 `a13n-harness-ui config validate`。

## MCP 字段参考

| 字段                     | 用途                                  |
| ------------------------ | ------------------------------------- |
| `id`、`name`             | 唯一 `mcp-` 前缀 ID 与显示名称        |
| `transport`              | `stdio` 或 `streamable_http`          |
| `command`、`args`、`env` | `stdio` 模式的进程启动配置            |
| `url`、`headers`         | `streamable_http` 模式的端点与请求头  |
| `tools`                  | 可选的暴露工具白名单/黑名单           |
| `oauth`                  | 远程鉴权服务器的可选 OAuth / DCR 配置 |

### 字面值与环境变量引用

在 `env` 与 `headers` 中请使用 `{env: VAR_NAME}` 或 `{credential_ref: key-id}` 引用密钥，避免写入明文字面值。

## OAuth 授权

对于需要 OAuth（含动态客户端注册 DCR）的 MCP 服务器，可在配置中声明 `oauth` 或在 WebUI **Settings → Connections** 中添加，通过 WebUI 完成登录与重连，令牌保存在本地凭据库中。

## 连接生命周期与协议

- 默认情况下 MCP 客户端作用域仅限单次 Run。将服务器 ID 加入 `a13n-harness-ui.yaml` 的 `mcp.host_owned_servers` 可跨运行保持连接。
- 通过 `mcp.protocol_overrides` 按服务器覆盖协议协商（`auto`、`legacy` 或 `2026-07-28`）。

## 来自 MCP 服务器的人工输入

当 MCP 服务器发起交互式问询（elicitation）或采样审批时，Harness UI 会在 iframe 之外的宿主界面展示决策卡片，在此回答或拒绝即可继续执行。
