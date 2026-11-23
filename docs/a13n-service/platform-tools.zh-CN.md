---
title: 内置平台工具权限与边界参考
description: 了解 Service Agent 如何在调用方委托权限下安全执行平台管理工具，以及配置差量更新与外部治理边界。
---

配置 [Agent Composer](agent-composer.md) 或自定义平台工具时使用本页。外部客户端请使用[管理 MCP](mcp.md)。

## 内部 agent 工具

Service 管理的 agent 使用独立的内部平台工具目录。Host 将 Run 凭证提供给 `/api/v1/platform-tools/`；外部工作空间密钥使用下文介绍的管理连接。内部工具接收直接的任务字段和带版本条件的差量，返回业务结果，不会回退到原始 API 工具。例如，修改 agent 的模型只提交 `agent_id`、原读取状态的 `expected_version` 和 `model`，其余配置由 Service 保留。Schedule 工具根据任务指令和执行选项构造 invocation。连接秘密仍通过受控配置或授权流程处理。内部 Run 凭证不能使用外部管理 MCP 或原始产品 HTTP 绕过工具准入。

旧版保存的 agent 请在升级前完成[平台工具切换](upgrade-notes.md#internal-platform-tools)。

修改 Agent 配置时，指定 `base_revision_id` 就必须显式指定 `make_default`，否则返回 `invalid_argument`。缺少必填版本会导致参数校验失败，版本过期则返回 `precondition_failed`。遇到冲突应重新读取并审核新的变更，不能悄悄替换已获批准的版本。

外部客户端使用[管理 MCP](mcp.md)，其参数包括 HTTP 响应封装和 `request_body`。

## 用量、Webhook 与环境凭据

`usage` 工具集提供九个现有查询操作，需要工作空间读权限；成员归属查询另需管理员权限。`subscriptions` 提供三个读取操作和三个需要审核的非敏感写操作，全部受 Run 接受时的管理员权限限制。`egress_credentials` 只提供四个元数据或应用状态查询。普通 Agent 按需启用这些工具集，当前 Composer 定义默认启用。

Composer 的原生客户端工具 `configure_webhook` 和 `configure_egress_credential` 会暂停 Run，等待 Console 私密表单。当前用户通过现有 HTTP 接口执行修改，密钥和令牌不进入工具参数或结果。凭据写操作仍不向内部或外部 MCP 开放。使用流程、版本冲突和异步应用规则见 [Composer](agent-composer.md#configure-webhooks-and-credentials)。
