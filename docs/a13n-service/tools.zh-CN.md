---
title: 工具与连接
description: 在 Service 中挂载内置能力、接入远程 Model Context Protocol (MCP) 服务器、配置 OAuth 授权并启用动态工具发现。
---

Service 中的智能体通过**内置工具集**、**平台工具**以及**连接（Connections）**（远程 MCP 服务器与 Composio SaaS 连接器）与运行环境、互联网和业务系统交互。

## 内置工具集

通过 `GET /api/v1/toolsets` 查看部署支持的完整工具集目录，并在智能体版本上按需启用：

| 工具集     | 提供的能力                                                | 前提条件                                    |
| ---------- | --------------------------------------------------------- | ------------------------------------------- |
| `files`    | 查看、搜索、读取与编辑工作区文件                          | 挂载[运行环境](environments.md)（默认开启） |
| `shell`    | 执行命令并管理进程                                        | 挂载[运行环境](environments.md)（默认开启） |
| `computer` | 桌面截图、鼠标键盘控制与无障碍树                          | 支持桌面的沙箱模板                          |
| `web`      | `search`、`scrape`、`fetch` 与 `download`                 | 为搜索/抓取绑定 Web 提供方                  |
| `memory`   | 读写已挂载的记忆空间文档与记录                            | 挂载[记忆空间](memory.md)                   |
| `assets`   | 将环境中的产物发布到工作区资产存储                        | 在智能体版本上启用                          |
| `browser`  | 自动化控制托管或外部接入的 Chromium [浏览器](browsers.md) | 线程浏览器绑定                              |

## 平台工具

在智能体配置的 **Advanced / Platform Features** 中按组启用平台工具（`agents`、`models`、`connections`、`skills`、`environment_templates`、`schedules`、`providers`、`execution`），让智能体在调用方工作区权限内查询或管理平台资源（读取默认自动放行，修改操作默认请求审批）。

## 联网搜索与抓取

在 **Providers → Add provider** 创建搜索或抓取提供方（Tavily、Brave Search、Firecrawl、Exa 等），并在智能体 `web` 工具集中绑定其 ID：

```json
{
  "toolsets": {
    "web": {
      "enabled": true,
      "tools": {
        "search": {"enabled": true, "config": {"provider_id": "wprov_search_123", "max_results": 5}},
        "fetch": {"enabled": true}
      }
    }
  }
}
```

## 连接

在 **Connections → New connection** 或通过 `POST /api/v1/connections` 注册共享的远程 Streamable HTTP MCP 服务器或 SaaS 连接器（创建需 `write`，使用需 `run`）：

```json
{
  "type": "mcp",
  "name": "Internal Knowledge Base",
  "auth": "bearer",
  "config": {
    "url": "https://mcp.internal.corp/v1",
    "tools": ["query_handbook", "search_docs"]
  },
  "credential": {"token": "secret_mcp_token_xyz"}
}
```

- **认证方式：** 支持 `none`、`bearer`、`headers` 与 `oauth`（`authorization_code` 浏览器授权或 `client_credentials` 机器授权，自动刷新令牌）。可通过 `POST /api/v1/connections/{id}/test` 测试连通性，通过 `GET /api/v1/connections/{id}/tools` 查看已发现的工具 Schema。
- **绑定到智能体：** 在 `connection_tools` 中引用连接并配置默认 `permission`（`"ask"`、`"allow"`、`"deny"`）及单工具 `permissions` 覆盖；非敏感上下文头可通过会话/线程的 `mcp_headers` 透传。禁用连接会立即阻断后续调用。
- **大规模工具发现：** 在智能体上配置 `tool_search`（`{"mode": "native", "sources": {}}` 或按业务领域划分的 `groups`），按需动态加载工具定义。交互式工具界面见[嵌入 MCP Apps](mcp-apps.md)。

### Composio 连接

在工作区设置中添加 `composio` 连接器提供方，随后在 **Connections** 中选择目标 SaaS 应用（Jira、Slack、GitHub、Google Drive 等），完成浏览器 OAuth 授权后加入 `connection_tools`。

### MCP 服务器推荐

运维人员可在 `service.toml` 中预置推荐企业 MCP 服务器卡片：

```toml title="service.toml"
[[providers.mcp_servers]]
key = "internal-handbook"
name = "Enterprise Handbook"
description = "Internal company wiki and documentation search"
url = "https://mcp.internal.corp/handbook"
auth = "oauth"
```
