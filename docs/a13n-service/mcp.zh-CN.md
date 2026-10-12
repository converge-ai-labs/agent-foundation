---
title: 通过 MCP 管理 Service
description: 让外部 agent 访问工作区资源管理、Trace 查询和随包文档。
---

Service 的 `control` 和 `all` 进程在 **`/api/v1/mcp/`** 提供 Streamable HTTP MCP 端点。支持 MCP 的客户端可以直接管理工作区资源，无需自行维护 API 包装。仅运行 worker 的进程不提供 MCP。

## 使用工作区密钥连接

创建[工作区 API key](identity.md#api-keys)，在客户端的 HTTP MCP 连接中配置：

```json
{
  "url": "https://service.example.com/api/v1/mcp/",
  "headers": {"Authorization": "Bearer a13n_REPLACE_WITH_YOUR_WORKSPACE_KEY"}
}
```

这只是连接信息，不是所有客户端通用的配置格式。请使用客户端对应的 URL 和请求头字段，并将密钥保存在其秘密存储中。工具发现和调用都需要密钥，不接受浏览器登录 Cookie。密钥保留原有的工作区范围和权限。可选的 `X-Workspace-ID` 请求头只能指定该工作区。认证属于连接上下文，不是工具参数。

将 `server.public_url` 设置为外部可访问的 Service URL，并让代理保留公开 Host。MCP 根据公开入口配置检查 Host 和 Origin。它使用无状态 HTTP 请求：副本之间无需 MCP 粘性会话，也不会启动第二个 Service runtime。

## MCP 提供哪些能力

| 可通过 MCP 使用                                                                                           | 需要直接调用 HTTP                                            |
| --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Agent 及修订；模型与 Provider 配置；Skill；Connection；环境模板与实例；Memory；Asset 元数据；Webhook 订阅 | 提交输入，启动、恢复、取消或等待执行，读取实时输出与线程 SSE |
| 只读 Trace、Span 和 Run/Attempt Trace 定位                                                                | Multipart 上传，归档、图片或文件下载，原始二进制内容         |
| 本地文档搜索                                                                                              | 组织、成员和权限管理，浏览器登录，OAuth 授权流程             |

工具由部署实际组装的 OpenAPI 中显式允许的操作生成，也包括显式允许的 Distribution 扩展。新增 HTTP 路由不会自动暴露。工具名称由 operation ID 派生；请从部署发现准确的名称和参数 schema。没有任何工具接受任意 URL、HTTP 方法或操作名。

MCP 也提供只读的[发现项和分析历史](agents-and-runs.md#find-and-improve-execution-issues)查询。提交或审阅发现项、准备 Finding Agent 和启动分析仍通过直接 HTTP API 完成。

MCP 不授予额外权限或审批权。例如，订阅管理仍要求工作区 `admin` 权限，Trace 查询仍遵循 HTTP API 的租户过滤和脱敏规则。

## 读取和更新资源

API 工具使用原操作的路径、查询和已声明请求头参数。JSON 请求体放在 `request_body` 下；空对象、显式 `null` 和省略字段含义不同。例如更新 Memory：

```json
{
  "memory_id": "mem_REPLACE_WITH_RETURNED_ID",
  "If-Match": "\"mem_REPLACE_WITH_RETURNED_ID:1\"",
  "request_body": {"guide": null}
}
```

每个 API 结果包含 `status`、`headers` 和未经改写的 JSON `body`。如果原响应存在相应请求头，`headers` 会提供 `etag`、`x-request-id` 和 `retry-after`。`204` 响应的 `body` 为 `null`。分页信息保留在 API 响应体中。HTTP 失败会作为 MCP 工具错误返回，同时保留相同的结构化封装，包括 Service 错误码和详情。

先读取资源，再把返回的 `headers.etag` 作为 **`If-Match`** 参数。缺少前置条件返回 `428`，过期的条件返回 `412`。不要在未检查资源变化的情况下替换过期 ETag。MCP 不自动重试写入，也不生成幂等键。响应丢失后，先检查资源当前状态，再决定是否重新提交写入。

## 搜索已安装版本的文档

调用 `search_documents`，传入关键词、1 到 10 的 `limit` 和 `language`（默认 `en`，也可选 `zh-CN`）：

```json
{"query": "上传 skill", "limit": 3, "language": "zh-CN"}
```

工具在随当前 Service 版本打包的标题、章节标题和 Markdown 正文中搜索。结果标明已安装版本、源文档、章节路径、源行号和有长度限制的文本片段。`truncated` 表示有匹配项或片段文本被省略；没有匹配时，`results` 为空数组。用更具体的关键词缩小长结果的范围。

搜索离线运行，不调用模型，也不执行业务操作。指向 `docs/a13n-service/` 之外的链接不在包内，可能描述其他版本。包内包含 Markdown API 参考入口页，但不包含网站生成的各操作页面。完整 HTTP schema 请使用本部署的 `/api/v1/openapi.json`。

## 通过 HTTP 上传和下载

外部 agent 必须自带 HTTP 或 Shell 能力，并持有适当凭证，才能执行下面的示例。文档只提供指导，不提供执行能力或权限。

将 `A13N_URL` 设置为部署地址，`A13N_API_KEY` 设置为工作区密钥。直接上传 Skill ZIP；重试同一次上传时保留原幂等键：

```sh
UPLOAD_KEY=$(uuidgen)
curl --fail-with-body "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Idempotency-Key: $UPLOAD_KEY" -F file=@release-notes.zip
```

从响应取得 `upload_id`。随后可通过生成的 **create skill** MCP 工具执行 JSON 管理操作：

```json
{"request_body": {"source": {"kind": "upload", "upload_id": "upl_REPLACE_WITH_UPLOAD_ID"}}}
```

下载修订时，使用返回的 Skill 和修订 ID 直接调用 HTTP：

```sh
curl --fail-with-body \
  "$A13N_URL/api/v1/skills/$SKILL_ID/revisions/$REVISION_ID/content" \
  -H "Authorization: Bearer $A13N_API_KEY" -o release-notes.zip
```

限制和其他内容类型参见 [Skill 包](skills.md#add-a-skill)及[上传和 Asset](files-and-webhooks.md#uploads)。

## 通过 HTTP 执行 Agent 并观察结果

通过 MCP 选择 Agent，再通过 HTTP 提交输入。为这条消息生成一个键，仅在重试同一次提交时复用：

```sh
MESSAGE_KEY=$(uuidgen)
curl --fail-with-body "$A13N_URL/api/v1/threads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $MESSAGE_KEY" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"payload\":{\"content\":[{\"type\":\"text\",\"text\":\"Summarize these notes.\"}]}}"
```

保存返回的 `thread.id` 和 `entry.id`。输入仍在排队时，返回的 `run` 可能为 null。跟踪[线程流](agents-and-runs.md#follow-a-thread-stream)，或读取该 Entry，直到 `assigned_run_id` 指出消费它的 Run：

```sh
curl --fail-with-body "$A13N_URL/api/v1/threads/$THREAD_ID/inbox/$ENTRY_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
curl --fail-with-body "$A13N_URL/api/v1/runs/$RUN_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

分别处理 `completed`、`waiting`、`failed` 和 `cancelled`。完成 Run 的回答在 `output` 中；等待中的 Run 需要明确回答、审批或工具结果。后续输入和恢复可能创建后继 Run：应跟踪输入及其分配的 Run，而不只是会话的第一个 Run。客户端断开连接不会取消执行。SDK 和完整流程见[应用集成](connect-application.md)。
