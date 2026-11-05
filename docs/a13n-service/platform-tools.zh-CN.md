---
title: 内部平台工具参考
description: 理解 agent 内部权限、配置差量与外部管理边界。
---

配置 [Agent Composer](agent-composer.md) 或自定义平台工具时使用本页。外部客户端请使用[管理 MCP](mcp.md)。

## 内部 agent 工具

Service 管理的 agent 使用独立的内部平台工具目录。Host 将 Run 凭证提供给 `/api/v1/platform-tools/`；外部工作空间密钥使用下文介绍的管理连接。内部工具接收直接的任务字段和带版本条件的差量，返回业务结果，不会回退到原始 API 工具。例如，修改 agent 的模型只提交 `agent_id`、原读取状态的 `expected_version` 和 `model`，其余配置由 Service 保留。Schedule 工具根据任务指令和执行选项构造 invocation。连接秘密仍通过受控配置或授权流程处理。内部 Run 凭证不能使用外部管理 MCP 或原始产品 HTTP 绕过工具准入。

旧版保存的 agent 请在升级前完成[平台工具切换](upgrade-notes.md#internal-platform-tools)。

修改 Agent 配置时，指定 `base_revision_id` 就必须显式指定 `make_default`，否则返回 `invalid_argument`。缺少必填版本会导致参数校验失败，版本过期则返回 `precondition_failed`。遇到冲突应重新读取并审核新的变更，不能悄悄替换已获批准的版本。

外部客户端使用[管理 MCP](mcp.md)，其参数包括 HTTP 响应封装和 `request_body`。
