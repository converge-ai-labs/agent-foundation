---
title: MCP 服务器
description: 在 Harness UI 中接入 stdio 与 Streamable HTTP MCP 服务器，配置 OAuth DCR 与工具过滤，并管理宿主常驻连接与人工输入。
---

分三步接入模型上下文协议（MCP）服务器：在根配置 `a13n-harness-ui.yaml` 同级的 `mcp/*.yaml` 下注册服务定义（或在 **Settings → Connections → MCP** 中添加），在智能体或项目中勾选启用，最后运行 `a13n-harness-ui config validate` 校验。

## 添加 MCP 服务器

在 `mcp/` 目录下保存 **Streamable HTTP** 远程传输或本地 **stdio 命令**传输定义：

```yaml title="mcp/docs.yaml — Streamable HTTP 远程服务"
schema_version: "2"
kind: mcp_server
id: mcp-docs
name: Documentation service
transport:
  url: https://mcp.example.com/mcp
  init_timeout_seconds: 20
  headers:
    Authorization:
      env: DOCS_MCP_AUTHORIZATION
```

```yaml title="mcp/local.yaml — stdio 本地命令服务"
schema_version: "2"
kind: mcp_server
id: mcp-local
name: Local tools
transport:
  command: /absolute/path/to/mcp-server
  arguments: ["--readonly"]
  environment:
    API_TOKEN:
      env: LOCAL_MCP_TOKEN
```

在智能体文件（`agents/*.yaml`）的 `mcp_servers: [mcp-docs]`、项目的 `defaults.mcp_servers` 或会话设置中启用该服务（`null` 继承默认，`[]` 不启用任何服务）。也可在 **Settings → Connections** 中直接粘贴标准客户端 `{"mcpServers": {...}}` JSON 配置。

## MCP 字段参考

| 字段                     | 用途                                                                                                                                   |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `schema_version`、`kind` | 固定为 `"2"` 与 `mcp_server`                                                                                                           |
| `id`、`name`             | 唯一 `mcp-` 前缀 ID 与显示名称                                                                                                         |
| `transport`              | `{url, init_timeout_seconds, headers, oauth}`（HTTPS 或无请求头的本机回环 HTTP）或 `{command, arguments, environment}`（在宿主机启动） |
| `tools`                  | `null`/省略（允许全部工具，含后续新增）、`[]`（禁用全部）或 `[exact_name, ...]` 精确工具名白名单                                       |
| `object_ref_tools`       | 启用[对象引用传输](../a13n-harness/capabilities.md#object-reference-transport)的工具名称或通配模式列表                                 |
| `expose_roots`           | 共享 Thread 已保存的本地根目录。stdio 默认为 `true`，HTTP 默认为 `false`。                                                             |
| `on_error`               | 启动失败策略（如 `fail` 或 `skip`）                                                                                                    |
| `authority`              | 服务器指令的应用方式：`operator`（默认，直连时作为系统指令）、`user`（用户上下文）或 `none`（仅作参考数据）。保存资源时省略默认值。    |

可在资源顶层或 `mcpServers` 的指定条目中设置 `authority`。在 WebUI 中编辑已有服务器时，打开 **设置 → 连接**，选择服务器，再修改 **服务器指令 → 应用方式**。编辑界面不会显示已保存的凭据，工具权限保持不变。

MCP Roots 通过目录名和 `file://` URI 向服务器提供 Thread 已保存的本地目录。未关联 Project 的 Thread 也可以共享自己的本地目录；设备绑定中的目录不包含在内。编辑 Project 只会更新新 Thread 的默认值；如需更新已有 Thread，请显式应用 Project 环境。

升级后，未设置此字段的 stdio 服务器会开始收到 Roots。若不想向某个命令服务器透露路径，请设为 `expose_roots: false`。只有信任某个 HTTP 服务器、愿意向它透露本地绝对路径时，才设为 `expose_roots: true`。该字段可写在资源顶层，也可写在 `mcpServers` 的单个服务器条目中。

### 字面量与密钥引用

在 `transport.headers` 与 `transport.environment` 中，请使用 `{env: VAR_NAME}` 或 `{credential_ref: key-id}`（或受支持的字符串环境变量插值）引用凭据，切勿将明文密钥写入 YAML。

## OAuth 授权

对于支持 OAuth 动态客户端注册（DCR）的 HTTPS 服务，省略 `headers` 并配置 `transport.oauth`：

```yaml title="mcp/oauth-docs.yaml"
schema_version: "2"
kind: mcp_server
id: mcp-oauth-docs
name: OAuth documentation service
transport:
  url: https://mcp.example.com/mcp
  oauth:
    registration: dcr
    scope: read
```

在 WebUI 中打开 **Settings → Connections**，选中该服务并点击 **Connect** 完成浏览器授权（远程访问 WebUI 时，需在 `webui.allowed_origins` 中配置精确的公网 HTTPS 源并转发 `/oauth/mcp/callback`）。保存后的令牌会在共享同一数据目录的 WebUI 与 TUI 运行中自动刷新。

## 连接生命周期与协议覆盖

- **单次运行连接与宿主常驻连接：** MCP 客户端默认随每次运行建立与关闭。将服务 ID 加入 `a13n-harness-ui.yaml` 的 `mcp.host_owned_servers` 可跨运行保持长连接存活（可通过 `mcp_status` 或 WebUI **Connections** 查看与断开）。
- **协议版本覆盖：** 当服务端要求特定协议版本时，可在 `mcp.protocol_overrides` 中按服务 ID 指定 `auto`、`legacy` 或 `2026-07-28`。

## 来自 MCP 服务器的人工输入

当运行中的 MCP 服务器发起表单征询（Elicitation）或 URL 确认请求时，Harness UI 会在工具 iframe 之外弹出原生 WebUI 或 TUI 交互卡片，选择 `accept`（附表单数据）、`decline` 或 `cancel` 即可继续当前调用。
