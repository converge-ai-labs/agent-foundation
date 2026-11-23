---
title: 工具与连接
description: 在 Service 中配置内置工具集、远程 MCP 与 Composio 连接、联网搜索/抓取提供方以及平台工具。
---

Service 为智能体提供**内置工具集**（文件、Shell、Web、记忆、浏览器、技能）以及外部**连接**（远程 MCP 服务器与 Composio 连接器）。

## 内置工具集

在 Console 或 `agent.config` 中为智能体版本启用所需工具集：

| 工具集    | 用途                             |
| --------- | -------------------------------- |
| `files`   | 在挂载环境中读写、搜索与列出文件 |
| `shell`   | 在挂载环境中执行命令             |
| `web`     | 搜索、抓取、获取与下载公开网页   |
| `memory`  | 读写文件记忆并检索/更新记录记忆  |
| `browser` | 自动化操作托管 Chromium 浏览器   |
| `skills`  | 发现并读取 `SKILL.md` 技能包     |

### 平台工具

当智能体需要在调用方工作区权限内查询或管理 Service 资源（如运行、线程或技能）时，启用平台工具。

## 联网搜索与抓取

在 **Workspace settings → Providers → Web** 添加第三方搜索与抓取提供方（Tavily、Exa、Firecrawl、Brave Search、Jina、Perplexity 等），随后在智能体的 `web` 工具集中选用。

## 连接

在 Console 的 **Connections**（或 `/api/v1/connections`）接入外部业务系统：

- **远程 MCP：** 通过 OAuth、Bearer Token 或静态请求头接入 Streamable HTTP MCP 服务器，并使用 `config.tools` 指定工具白名单。
- **禁用生效：** 禁用连接会立即拦截后续工具调用，包括正在进行中的运行。

### MCP 服务器推荐

运维人员可通过 Service 配置中的 `providers.mcp_servers` 在 Console 中预置推荐 MCP 服务器。

### Composio 连接

在 **Workspace settings → Providers → Connector** 添加 Composio 提供方，以托管连接形式接入 OAuth SaaS 集成。
