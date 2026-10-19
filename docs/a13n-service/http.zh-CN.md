---
title: HTTP 约定
description: 所有端点共用的认证、错误、并发、幂等、分页和流式规则。
---

所有 API 操作位于 Service 公共 URL 的 `/api/v1` 下。除非操作另有说明（上传、文件内容、图片和会话事件流），请求和响应均为 JSON。运行中的 Service 在 `/api/v1/openapi.json` 发布 OpenAPI 文档，在 `/api/v1/docs` 提供交互式页面；[HTTP 参考](api-reference/index.md)从同一文档生成。

请求体采用严格校验，未知字段会被拒绝。语言集成或 shell 脚本请参阅 [SDK 与 CLI](sdks.md)；详细用法由各客户端自己的 Markdown 文档维护。

## 认证

应用使用 [API 密钥](identity.md#api-keys)作为 bearer token 认证：

```sh
curl "$A13N_URL/api/v1/agents" -H "Authorization: Bearer $A13N_API_KEY"
```

提供 `Authorization` 请求头时，cookie 会被忽略。API 密钥仅在自身工作空间生效，不能修改它所属的用户或服务账号的账号信息。

浏览器使用 `POST /api/v1/auth/login` 设置的登录会话 cookie。除 `GET`、`HEAD` 和 `OPTIONS` 外，使用 cookie 认证的请求必须：

- 在 `X-CSRF-Token` 中发送登录会话的 CSRF token；登录响应返回它，已有登录会话也可通过 `GET /api/v1/auth/session` 再次获取；
- 若发送 `Origin` 请求头，其值必须等于 `server.public_url` 的源；回环公共 URL 中，`localhost` 和 `127.0.0.1` 可以互换。

账号操作（修改资料、密码、邮箱、禁用账号、列出登录会话和自己的审计记录）、`GET /api/v1/auth/session`、`POST /api/v1/auth/logout`，以及创建 API 密钥、发送或重发邀请、启动浏览器授权（OAuth 授权码或 connector 账号配置）均需要登录会话。API 密钥调用这些操作统一返回 `403 forbidden`：它不能创建寿命可超过自身的凭据，也不能向第三方发出代你完成操作的链接。没有有效凭据时返回 `401 unauthenticated`；已禁用主体的凭据视为无效。

每个认证后的响应，包括路由响应和认证后发生的错误，都包含 `Cache-Control: no-store`；登录会话续期时还包含更新的 cookie。

### 工作空间

工作空间中的 agent、线程、运行、provider、模型、skill 和记忆等资源，路径不包含工作空间，例如 `/api/v1/agents`。每个此类请求作用于凭据选定的一个工作空间：

- API 密钥作用于自身工作空间，无需请求头。`X-Workspace-ID` 指定其他工作空间时返回 `403 forbidden`。
- 登录会话通过 `X-Workspace-ID: ws_…` 指定工作空间 ID。缺少该请求头时返回 `400 invalid_argument`，`details.field` 为 `X-Workspace-ID`。

管理操作和部署级读取不接受该请求头。管理范围由路径指定：`/api/v1/auth/…`、`/api/v1/users/…`、`/api/v1/organizations/…`、`/api/v1/workspaces`，以及 `/api/v1/workspaces/{workspace_id}` 下的 `icon`、`archive`、`audit-events`、`grants`、`invitations`、`service-accounts` 和 `keys`。部署级读取为 `/api/v1/model-catalog`、`/api/v1/provider-types/{kind}`、`/api/v1/mcp-servers` 和 `/api/v1/connections/redirect-uri`。

## 错误

所有错误采用同一结构，每个响应都包含 `X-Request-Id` 请求头：

```json
{
  "error": {
    "code": "precondition_failed",
    "message": "Resource changed",
    "details": {"current_etag": "\"ap_...:4\""},
    "request_id": "req_..."
  }
}
```

| 错误码                  | 状态 | 含义                                                                                                                                 |
| ----------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `invalid_argument`      | 400  | 请求格式错误或引用不可用内容。`details.field` 指出错误字段；请求校验在 `details.fields` 中列出 `{field, reason}`。                   |
| `invalid_cursor`        | 400  | 分页游标格式错误，或属于其他集合、范围或筛选条件。                                                                                   |
| `unauthenticated`       | 401  | 没有有效凭据。                                                                                                                       |
| `forbidden`             | 403  | 凭据缺少操作权限（`details.verb`），或 CSRF、源检查失败。                                                                            |
| `not_found`             | 404  | 目标不存在或无权读取（`details.kind`、`details.id`）。                                                                               |
| `request_timeout`       | 408  | 请求体未在 `server.request_timeout` 内到达。                                                                                         |
| `already_exists`        | 409  | Key 或其他唯一值已被占用。                                                                                                           |
| `conflict`              | 409  | 目标状态不允许该操作；`details.reason` 说明原因，例如 `archived`、`builtin`、`last_organization_admin` 或 `idempotency_key_reused`。 |
| `precondition_failed`   | 412  | `If-Match` 已过期；`details.current_etag` 包含当前值。                                                                               |
| `payload_too_large`     | 413  | 请求体或文件超过限制（`details.limit`）。                                                                                            |
| `disabled`              | 422  | 请求用到的目标、其主体或工作空间已禁用或归档。修改已归档的 agent 或 skill 则返回 `409 conflict`。                                    |
| `precondition_required` | 428  | 操作需要 `If-Match`。                                                                                                                |
| `rate_limited`          | 429  | 请求过多；等待 `Retry-After` 秒后重试（`details.retry_after_seconds`）。                                                             |
| `internal`              | 500  | 未预期错误；报告时请提供 `request_id`。                                                                                              |
| `unavailable`           | 503  | 依赖（`details.dependency`：`database`、`redis`、`objects`、`mail`、provider 类型等）暂时无法服务，请稍后重试。                      |

不存在的路由或路径不接受的方法返回 `not_found`，详情为 `{"kind": "route", "id": "<METHOD> <path>"}`；无法解析为 JSON 的请求体返回 `invalid_argument`，详情为 `{"field": "body", "reason": "unparsable"}`。

消息供人阅读，程序应根据 `code` 和 `details.reason` 判断。错误详情不会包含提交的值或密钥。

## 并发控制

单资源响应包含强 `ETag`，例如 `"ap_…:4"`（模型为 `"{key}:{version}"`），视图包含相同 `version`。修改已有资源的操作必须在 `If-Match` 中提供上次读取的 ETag：

```sh
curl -X PATCH "$A13N_URL/api/v1/agents/$AGENT" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -H 'If-Match: "ap_...:4"' -d '{"description": "Answers billing questions"}'
```

缺少 `If-Match` 时返回 `428 precondition_required`；值已过期时返回 `412 precondition_failed` 和当前 ETag。请重新读取资源、重新应用修改并重试。比较是精确匹配，弱验证器和 `*` 永不匹配。OpenAPI 将 `If-Match` 声明为可选请求头，但每个声明它的操作实际上都要求提供。

收件箱操作（编辑、撤回、重排排队消息）、环境和记忆挂载修改以及线程更新使用**线程** 的 ETag。创建 agent 或 skill 修订版本使用其主记录的 ETag。其他创建操作和 interrupt 等运行命令不需要 ETag。

## 幂等请求

以下操作需要 `Idempotency-Key` 请求头，长度为 1–512 个可见 ASCII 字符：

- `POST …/threads`（携首条消息创建线程）和 `POST …/threads/{thread_id}/inbox`（提交消息）
- `POST …/runs/{run_id}/fork` 和 `POST …/runs/{run_id}/resume`
- `POST …/uploads`

每个逻辑请求生成唯一 key，响应丢失后重试时复用。首次提交返回 `201`；上传返回 `200`。使用相同 key 和请求重试时，返回 `200` 和同一组已创建对象的当前状态，而非首次响应的副本。相同 key 搭配不同请求体或目标返回 `409 conflict`，原因为 `idempotency_key_reused`。Key 的范围为调用者和工作空间，不会过期。

## 分页

集合返回 `{"items": [...], "next_cursor": "..."}`。提供 `limit`（1–100，默认 50），下一页提供 `cursor=<next_cursor>`；最后一页的 `next_cursor` 为 `null`。游标是不透明值，绑定集合、范围和产生它的查询全部筛选条件；改用不同筛选条件时返回 `invalid_cursor`。有界目录、工具集、挂载和执行尝试可以一次返回所有项而不分页；请检查具体操作的 schema。

## 读取摘要和指定详情

使用 `view=compact` 查找相关 run、修订、模型、收件箱记录、显示条目或 span，并结合接口支持的过滤条件。现有调用默认使用 `view=detailed`，保留原来的响应。精简列表默认每页十条；为控制响应大小，实际可能更少。即使过滤后当前页为空，也要按返回的游标或 ordinal 继续读取。

预览包含 `text` 和 `truncated`。`omitted_sections` 列出可以单独读取的字段。`source_truncated` 表示保存的内容已经被截短，继续读取也无法恢复被丢弃的数据。字段被省略或预览被截短，不代表原始数据没有相关信息。

例如，查找失败的 run，再读取其中一次失败：

```http
GET /api/v1/runs?view=compact&status=failed&limit=10
GET /api/v1/runs/{run_id}?section=failure
GET /api/v1/runs/{run_id}/items?view=compact&kind=tool_call&state=failed
GET /api/v1/runs/{run_id}/items/{item_id}/content?ordinal=7&field=result&offset=0
```

条目 ID 必须配合该条目的实际 `ordinal`。精简条目查询默认从最早的 ordinal 开始，也可以指定 `before` 或 `after`，然后按 `next_before` 或 `next_after` 继续。未加过滤的详细查询保留读取最新条目及完整尾部的原有行为。

部分内容和正文查询返回 `representation=fragment`、内容 `version`、`text`、`next_offset` 和 `has_more`。offset 按 Unicode 码点计数。继续读取时，同时传入返回的 offset **和 version**。内容变化会返回 `409 conflict`，原因是 `content_changed`，此时应重新开始读取。JSON 片段必须拼接完整后才能解析整个值。字面关键词 `query` 搜索返回下一处命中及其上下文；此时 next offset 用于继续搜索，而不是逐字提取全部内容。

检查配置时，可通过修订的 `section=all` 或 `instructions` 等配置字段读取。run 的 `/agent` 接口使用 `section=original|effective|overrides`，original/effective 还可用 `part` 选择字段。修订的 `/compare` 接口接受 `base_revision_id` 和可选 section。编辑前应取得完整原配置及 agent 主记录的 ETag，并保留无关字段。不能把摘要、片段或差异当成完整配置提交。

通过 `/toolsets/index`、`/connections/{connection_id}/tools/index` 或 `/connector-providers/{provider_id}/apps/{app}/actions/index` 搜索工具目录。使用对应的 `/definitions` 接口读取定义，每次最多重复传入五个准确的 `name`。检查 `missing` 和 `oversized`。超大的定义可在同一接口中指定一个名称，按版本分段读取；接口不会静默截短定义。模型目录支持精简搜索、能力过滤和分页；读取能力或价格部分时，应准确选择 provider/model。

Trace 列表支持 `status`、`kind` 和 `tool_name`。读取单个 span 的输入或输出时，使用 `/traces/{trace_id}/spans/{span_id}/content` 和 `section=input|output`。若 `items` 为空，应按 `next_cursor` 继续查找。找到后，后续文本分段请求继续使用定位该 span 时的请求游标。Trace 数据仍受部署的保存期限和后端可用性限制。

## 限制

- 请求体受 `server.request_bytes` 限制（`413`），且必须在 `server.request_timeout` 内到达（`408`）。上传和图片有更小的限制。
- 密码登录和其他凭据检查、上传、授权回调会限流（`429`，包含 `Retry-After`）。收件箱已满的线程也返回 `429`。

## 标识符与时间

ID 是带类型前缀的不透明字符串，例如 `ws_`、`ap_`（agent）、`sk_`（skill）、`sess_`、`thread_`、`run_`。模型不暴露 ID：路径和引用使用 [key](resources.md#common-conventions)。时间戳采用带偏移量的 RFC 3339 格式。

## 事件流

`GET …/sessions/{session_id}/stream` 是会话中线程状态及其活跃运行实时输出的 server-sent event 流。参阅[会话事件流](agents-and-runs.md#follow-a-session-stream)。
