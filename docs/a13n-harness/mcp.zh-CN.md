---
title: MCP
description: 在 Harness 中接入 stdio 与 Streamable HTTP MCP 服务器，支持按运行解析请求头、宿主连接复用与工具搜索分组。
---

通过 `AgentDefinition.capabilities` 或 `RunBindings.capabilities` 挂载 Pydantic AI 的 `MCP` / `ContextualMCP` 能力，为 Harness 智能体接入 Model Context Protocol（MCP）服务器。

## 连接服务器

- **Stdio 服务器：** 以显式参数与环境变量启动本地命令。
- **Streamable HTTP 服务器：** 连接远程 `/mcp` 端点，并通过 `ContextualMCP` 按次解析请求头（如 Bearer Token 或租户 ID）。

## 宿主连接复用与工具搜索

- **连接复用：** 默认情况下 MCP 客户端随每次 Run 创建和关闭。宿主可维护长连接池并在 `RunBindings` 中传入已连接客户端，避免重复建连开销。
- **大型工具目录：** 将 `MCP` 或 `ContextualMCP` 能力直接作为 [ToolSearchGroup](tool-search.md#group-a-run-bound-mcp-capability) 来源传入，按需加载 Schema 并保留按次请求头解析。
- **权限控制：** 可在 MCP 工具之上叠加工具白名单与[受管工具权限](managed-tools.md#select-tool-permissions)，无需改动服务器实现。
