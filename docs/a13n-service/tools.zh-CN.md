---
title: 工具与连接
description: 为 agent 配置内置工具集、MCP 和应用连接、客户端工具以及 skill。
---

Agent 的工具来自四个地方，都在[修订版本](agents-and-runs.md#agent-configuration)中选择：

- **内置工具集：** 由 Service 自身执行，包括文件、终端、web、记忆、资产发布和 agent 配置。
- **连接：** 远程 MCP 服务器，以及 Composio 等 connector provider 的应用账号。
- **客户端工具：** 由你的应用执行，并通过[恢复](agents-and-runs.md#waits-approvals-and-questions)提交结果。
- **Skill：** 添加指令和文件；参阅 [Skill](skills.md)。

每个工具在修订版本中都有[权限](agents-and-runs.md#tool-permissions)：允许、请求审批、由 reviewer 模型决定，或拒绝。

## 内置工具集

`GET /api/v1/toolsets` 返回工具目录，包括各工具的 key、模型可见名称、默认状态与权限，以及配置 schema。

| 工具集          | 工具（模型可见名称）                                                                                                                          | 默认状态                                       |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `files`         | `view`, `write`, `edit`, `multi_edit`, `mkdir`, `move`, `copy`, `delete`, `ls`, `glob`, `grep`                                                | 启用                                           |
| `shell`（终端） | `shell_exec`, `shell_info`, `shell_wait`, `shell_input`, `shell_signal`                                                                       | 启用                                           |
| `web`           | `search`, `scrape`, `fetch`, `download`                                                                                                       | 各工具均禁用                                   |
| `memory`        | `memory_file_view`, `_grep`, `_create`, `_edit`, `_append`, `_move`, `_delete`; `memory_record_search`, `_list`, `_add`, `_update`, `_delete` | 启用                                           |
| `assets`        | `publish_asset`                                                                                                                               | 禁用                                           |
| `configuration` | `find_resources`, `read_resource`, `describe_agent_config`, `create_agent`, `create_agent_revision`                                           | 禁用；参阅 [Agent Composer](agent-composer.md) |

文件和终端工具操作运行挂载的[环境](environments.md)，只有挂载环境时才提供给模型。记忆工具操作挂载的[记忆](memory.md)：文件工具用于文件型记忆，记录工具用于记录型记忆；`read` 挂载只提供查看、列出和搜索。`publish_asset` 将环境文件转换为工作空间[资产](files-and-webhooks.md#assets)。

## Web 搜索与抓取

`search` 和 `scrape` 调用 [web provider](resources.md#providers)：在 **Workspace settings → Providers → Web** 添加 provider，再在各工具配置中选择。`fetch` 和 `download` 使用 Service 自身的 HTTP 客户端，不需要 provider。四种工具都遵循部署的[出站策略](configuration.md#outbound-requests)。

| Provider 类型                                                | 操作           | 凭据      |
| ------------------------------------------------------------ | -------------- | --------- |
| `duckduckgo`                                                 | search         | 无        |
| `brave`, `perplexity`, `serpapi`                             | search         | `api_key` |
| `exa`, `parallel`, `tavily`, `firecrawl`, `jina`, `tinyfish` | search, scrape | `api_key` |

![Console 中的搜索与抓取 provider 目录，包含 TinyFish](../../.github/assets/console-search-providers.webp)

必须逐个显式启用 web 工具；仅启用工具集不会启用任何工具。工具配置如下：

```json
{
  "web": {
    "enabled": true,
    "tools": {
      "search": {"enabled": true, "config": {"provider_id": "wprov_...", "max_results": 5}},
      "fetch": {"enabled": true, "config": {"deny_domains": ["internal.example.com"]}}
    }
  }
}
```

- `search` 接受 `provider_id` 和 `max_results`（1–10）。`scrape` 接受 `provider_id` 和 `max_content_bytes`（最多 4 MiB）。`fetch` 最多返回 256 KiB。
- 每个 web 工具都接受 `allow_domains` 和 `deny_domains`；裸主机名包含其子域，空列表表示不限制。所有内置 provider 类型都不支持按域名限制抓取，因此 `scrape` 拒绝域名列表。
- Provider 类型必须支持所需操作，保存修订版本时会检查。

## 连接

连接是带有一份凭据的工具来源。连接属于工作空间；拥有 `run` 的成员可在运行中使用，修改则需要 `write`。在 Console 中打开 **Connections → New connection**。

| 类别                               | `type`                                     | `auth`                                 |
| ---------------------------------- | ------------------------------------------ | -------------------------------------- |
| 远程 MCP 服务器（Streamable HTTP） | `mcp`                                      | `none`, `bearer`, `headers` 或 `oauth` |
| Connector 应用账号                 | Connector provider 的类型，例如 `composio` | `account`                              |

连接的 `status` 在凭据可用前为 `pending`，可用后为 `ready`，token 刷新失败导致凭据丢失后为 `reauthorization_required`。`failure` 描述最后一次失败的授权操作。凭据只写不读，视图显示 `credential_configured` 和 `client_secret_configured`。`POST …/authorize` 用于浏览器授权；`auth` 不为 `oauth` 的 MCP 连接没有此流程，返回 `409 conflict`，原因为 `no_browser_authorization`。

连接没有删除操作。`PATCH {"enabled": false}` 立即停止所有使用，包括运行中的 agent 调用；`{"enabled": true}` 恢复使用。`POST …/revoke` 立即清除凭据，并尽力请求 provider 在远程撤销，结果记录在 `remote_revocation` 中（`revoked`、`failed`，或没有远程撤销目标时的 `skipped`）。`PATCH` 和 `revoke` 需要连接的 `If-Match`；修改前重新读取，因为授权和 token 刷新会改变版本。已禁用连接或已归档工作空间中的连接也可撤销：移除凭据属于退出管理操作。

### 远程 MCP 服务器

在 Console 中打开 **Connections → New connection**，搜索或浏览远程 MCP server 目录。选择目录中的 server，或通过 **Custom Remote MCP** 输入自己的 endpoint。根据 server 的要求，连接支持 OAuth、Bearer token、静态请求头或无需认证。

![Console 连接目录中的多个远程 MCP server](../../.github/assets/console-mcp-connection.webp)

```sh
curl -X POST "$A13N_URL/api/v1/connections" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "mcp", "name": "Docs search", "auth": "bearer",
       "config": {"url": "https://mcp.example.com/mcp", "tools": ["search_docs", "read_doc"]},
       "credential": {"token": "..."}}'
```

- `config.url` 必须符合[出站策略](configuration.md#outbound-requests)。凭据不能放在 URL 中：名称类似凭据的查询参数（`api_key`、`token` 等）会被拒绝；请使用 `headers` 认证。
- 可选的 `config.tools` 将连接限制到指定工具。省略时会暴露服务器列出的所有工具；如果超过 128 个，运行以 `connection_tools_exceeded` 失败。
- `bearer` 接受 `{"token": "..."}`，以 `Authorization: Bearer` 发送。`headers` 在 `config.headers` 中列出请求头名称，并以 `{"headers": {"x-api-key": "..."}}` 提供值。不能设置传输和协议请求头（`host`、`content-type`、`cookie`、`mcp-*`、`sec-*`、`proxy-*` 等）。
- 修改 URL、认证方式或 OAuth 客户端会移除已保存凭据；只修改 `config.tools` 则保留。

每次 worker attempt 为每个精确授权的 Connection/定义/调用方绑定懒加载并持有一个已进入的 MCP 客户端。内部 Run 借用新的工具投影，不共享可变请求头，也不在 Run 之间重连。客户端随 attempt 关闭，不按 URL 建立共享池，也不跨 worker 持久化。SDK 使用现代自动发现并保留旧版协商。客户端保持已进入时工具目录仍会刷新，派发继续检查当前 Connection 可用性和 worker 权限。没有持久响应通道时，Service 不声明 MCP 人工输入；这与客户端工具等待和审批不同。

### OAuth

使用 `auth: "oauth"` 时，连接从 MCP 服务器的授权服务器获取 token。连接持有**一份** 凭据，供工作空间中所有运行使用，与授权人无关。

- **浏览器授权**（`grant_type: "authorization_code"`，默认值）。在 Console 选择 **Authorize connection** ，或调用 `POST …/connections/{connection_id}/authorize` 并传入 `{"return_url": ...}`，在 `expires_at` 前引导用户访问返回的 `redirect_url`；在此之前 `authorization_pending` 保持 true。Service 发现授权服务器，使用 PKCE（S256）和资源指示符，在自身回调处完成流程，再携结果将浏览器重定向到 `return_url`。浏览器流程始终需要登录会话，并通过限定到回调路径的 cookie 绑定启动流程的浏览器；其他浏览器完成会返回 `browser_mismatch`。API 密钥不能启动流程（`403 forbidden`）：此流程发出的链接可由任何持有者完成，只允许用户自己的会话执行。超过 `expires_at` 的回调返回 `authorization_expired`。启动新流程只终止已在进行的完成操作；保留可用凭据和未完成的刷新，刷新继续执行，直到新流程完成。
- **客户端注册。** 未设置 `config.oauth.client_id` 时，每次授权都会动态注册公共客户端。预先注册的客户端需设置 `client_id` 和 `token_endpoint_auth_method`（`none`、`client_secret_basic` 或 `client_secret_post`），并提供只写的 `client_secret`。在 provider 中注册 Service 重定向 URI：`GET /api/v1/connections/redirect-uri` 返回它（`{public_url}/api/v1/connections/callback`），Console 连接表单也会显示。
- **机器凭据**（`grant_type: "client_credentials"`，需客户端密钥）。`authorize` 直接获取 token，无需浏览器，返回 `redirect_url: null`。与 bearer token 一样，它供工作空间所有运行使用。这是 API 密钥唯一可以调用的授权方式。
- `config.oauth.scopes` 请求指定 scope；为空时请求服务器声明的 scope。
- 服务器提供 refresh token 时，token 在使用时通过连接唯一的刷新操作刷新；并发调用者等待同一结果，取消某个调用者不会丢失轮换后的凭据。只有服务器以 `invalid_grant` 拒绝，或请求可能已到达服务器而结果未知时，才清除凭据（`reauthorization_required`）。Service 从未发送的请求保留凭据：例如出站策略拒绝（token 请求为 `token_endpoint_denied`，发现阶段为 `authorization_server_denied`），或发送前连接失败（`token_endpoint_unreachable`）。新授权会尽力撤销被替换的授权；来自同一 OAuth 客户端的替换除外，因为撤销可能同时终止新授权。

`return_url` 必须是 [`server.public_url`](configuration.md#required-infrastructure) 同源页面，或与部署 [`providers.return_urls`](configuration.md#outbound-requests) 中的一个 URL 完全一致；Console 发送 `{its origin}/connections/callback`。公共回调按客户端地址限流。

### Composio 连接

[Composio](https://composio.dev) 托管应用集成和外部账号凭据。配置一次后，按应用连接账号：

1. 使用 Composio 项目 API 密钥添加 `composio` 类型的 connector provider：在 **Workspace settings → Providers → Connector** 中操作，或向 `POST /api/v1/connector-providers` 发送 `{"type": "composio", "name": ..., "credential": {"api_key": "..."}}`。
2. 浏览应用和操作：`GET /api/v1/connector-providers/{provider_id}/apps`（`query`、`refresh=true`）、`…/apps/{app}` 和 `…/apps/{app}/actions`。
3. 创建连接，设置 `type: "composio"`、`auth: "account"`、`connector_provider_id`，并在 `config` 中指定 `app`、固定的 `actions`（1–128）和 `setup`：`auth_config_id`（已有 Composio auth config，或 `create:OAUTH2` 等 `create:<SCHEME>`）以及固定的 `toolkit_version`（`YYYYMMDD_NN`）。
4. 执行授权（需登录会话，与其他[浏览器授权](#oauth)相同）：用户在浏览器完成 Composio 托管的账号设置，经 Service 回调返回。连接随后绑定该外部账号，token 由 Composio 保存和刷新。

每个连接绑定一个账号，且只暴露固定的操作。工具调用为每次调用发送稳定的请求 ID；操作结果未知时，会告知 agent 先检查外部状态，再决定是否重试。

### 测试与发现工具

- `POST …/connections/{connection_id}/test` 立即发现工具，并将结果记录在 `last_test`；需要 `run`。
- `GET …/connections/{connection_id}/tools` 列出工具的输入、输出 schema 和注解，缓存时长为 `providers.discovery_ttl`。只读提示等注解按服务器声明显示，不授予权限。
- Connector 连接尚未绑定账号时，`test` 失败，`/tools` 返回 connector 应用的操作目录，而不是实时发现结果。

一个连接最多向模型暴露 128 个工具。

### 在 agent 中使用连接

修订版本在 `connection_tools` 中列出连接：

```json
{
  "connection_tools": [
    {"connection_id": "conn_...", "tools": ["search_docs"], "permission": "allow"},
    {"connection_id": "conn_...", "tools": null, "defer_loading": true, "permission": "ask",
     "permissions": {"delete_page": "deny"}}
  ]
}
```

- `tools: null` 选择连接暴露的全部工具；列表最多选择 128 个。
- `defer_loading`（仅 MCP）让模型通过工具搜索发现工具，不必预先接收全部定义。
- `permission` 应用于连接的所有工具，`permissions` 按工具覆盖。
- 修订版本最多选择 128 个连接。保存时检查每个连接已启用，并暴露所选工具。

执行时每个连接必须为 `ready`。每次调用前，Service 检查连接仍启用且已授权，以及运行仍有可用额度；否则不会发送调用。调用受 `providers.tool_call_seconds` 限制，永不自动重试。

### 调用方请求头

线程可在 `mcp_headers` 中为 MCP 连接携带非凭据上下文，例如对话或租户标识符，格式为 `{connection_id: {header: value}}`。参阅[线程](agents-and-runs.md#caller-headers)。连接自身认证使用的请求头名称不可设置。

## MCP 服务器建议

`GET /api/v1/mcp-servers?query=...` 列出常见远程 MCP 服务器的 URL、认证方式和要求；Console 用它预填新连接。Service 自带建议列表，运维人员可用 `providers.mcp_servers` 按 key 添加或替换条目：

```toml
[[providers.mcp_servers]]
key = "handbook"
name = "Team handbook"
description = "Search the internal handbook."
url = "https://mcp.example.com/handbook"
auth = "oauth"
```

建议仅预填表单；连接仍与其他连接一样进行验证。
