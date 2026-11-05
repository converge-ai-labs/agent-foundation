---
title: HTTP 约定
description: 认证请求、读取和更新资源，并处理重试与分页响应。
---

本页帮助你实现 Service 客户端共用的请求处理：先认证，保存读到的 ID 和版本，再携带相应请求头更新资源或提交工作。完整的 agent 对话流程见[连接应用](connect-application.md)；语言客户端见 [SDK 与 CLI](sdks.md)。

API 路径以 Service URL 下的 `/api/v1` 开头。除文件传输、图片和事件流外，请求和响应使用 JSON。请求体拒绝未知字段。实际部署在 `/api/v1/openapi.json` 发布准确 schema，在 `/api/v1/docs` 提供交互式浏览器；[HTTP 参考](api-reference/index.md)介绍各项操作。

## 认证

先准备一把所属主体具有 `read` 权限的工作空间 [API 密钥](identity.md#api-keys)。在 shell 中将 `A13N_URL` 设为不带末尾斜杠的 Service URL，将 `A13N_API_KEY` 设为该密钥。列出 agent：

```sh
curl -i "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

成功响应包含 `items` 和 `next_cursor`。下方更新练习需要一份未归档的自定义 agent，请选取它返回的 `id`。如果还没有，先[创建 agent](agents-and-runs.md)。提供 `Authorization` 请求头会选用该凭据并忽略 cookie。缺失、无效或已禁用主体的凭据返回 `401 unauthenticated`。

浏览器客户端使用 `POST /api/v1/auth/login` 返回的登录 cookie。使用 cookie 认证时，除 `GET`、`HEAD` 和 `OPTIONS` 外的方法都要发送 `X-CSRF-Token`，其值来自登录响应或 `GET /api/v1/auth/session`。若提供 `Origin`，必须匹配 `server.public_url` 的源；回环公共 URL 中的 `localhost` 与 `127.0.0.1` 可以互换。

账号修改、登录会话和个人审计读取、退出登录、创建 API 密钥、邀请以及个人 Connection 授权都使用登录会话。API 密钥调用这些操作（包括 `GET /auth/session`）会返回 `403 forbidden`。工作空间 Model Provider 授权接受具有所需权限的 API 密钥。权限模型见[身份与访问](identity.md)。

认证后的响应包含 `Cache-Control: no-store`，包括认证后发生的错误。登录会话续期时，响应还会返回更新的 cookie。

### 工作空间

工作空间资源使用 `/api/v1/agents` 这样的路径；由凭据选择工作空间：

| 凭据     | 工作空间选择                                                                                         |
| -------- | ---------------------------------------------------------------------------------------------------- |
| API 密钥 | 使用自身工作空间，无需额外请求头。在 `X-Workspace-ID` 中指定其他工作空间会返回 `403 forbidden`。     |
| 登录会话 | 发送 `X-Workspace-ID: ws_…`。省略时返回 `400 invalid_argument`，且 `details.field: X-Workspace-ID`。 |

账号和管理路由通过路径确定范围，包括 `/auth/…`、`/users/…`、`/organizations/…` 和 `/workspaces/{workspace_id}/…`，不使用工作空间请求头。`/model-catalog`、`/provider-types/{kind}`、`/mcp-servers` 和 `/connections/redirect-uri` 等部署级读取也无需选择工作空间。本段路径均相对于 `/api/v1`。

## 错误

程序根据 `error.code` 和结构化 `details` 判断，使用 `message` 向用户展示。报告失败时保留响应的 `X-Request-Id`；错误正文也包含同一个 ID：

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

| 错误码                  | 状态 | 检查或处理方式                                                                                           |
| ----------------------- | ---- | -------------------------------------------------------------------------------------------------------- |
| `invalid_argument`      | 400  | 修正 `details.field` 或 `details.fields` 中的 `{field, reason}` 条目。                                   |
| `invalid_cursor`        | 400  | 使用预期范围和筛选条件，重新开始同一集合的查询。                                                         |
| `unauthenticated`       | 401  | 提供有效凭据。                                                                                           |
| `forbidden`             | 403  | 检查权限（`details.verb`）、CSRF 和源。                                                                  |
| `not_found`             | 404  | 检查目标 ID 及你对目标的访问权限。                                                                       |
| `request_timeout`       | 408  | 在 `server.request_timeout` 内发送请求体。                                                               |
| `already_exists`        | 409  | 选择未占用的 key，或读取已有资源。                                                                       |
| `conflict`              | 409  | 查看 `details.reason`，如 `archived`、`builtin`、`last_organization_admin` 或 `idempotency_key_reused`。 |
| `precondition_failed`   | 412  | 重新读取已变化的资源，再决定如何应用修改。                                                               |
| `payload_too_large`     | 413  | 检查 `details.limit`；缩小请求体，或使用支持的 section/content 读取。                                    |
| `disabled`              | 422  | 检查目标、主体或工作空间是否被禁用或归档。                                                               |
| `precondition_required` | 428  | 提供资源的 `If-Match`。                                                                                  |
| `rate_limited`          | 429  | 等待 `Retry-After` 指定的秒数；`details.retry_after_seconds` 也包含该值。                                |
| `internal`              | 500  | 报告 `request_id`。                                                                                      |
| `unavailable`           | 503  | 检查 `details.dependency`，待该依赖恢复后重试。                                                          |

未知路由或不支持的方法返回 `not_found`，详情包含 `kind: route`，`id` 为方法和路径。无法解析的请求体返回 `invalid_argument`，详情为 `field: body` 和 `reason: unparsable`。修改已归档的 agent 或 skill 返回 `409 conflict`，而非 `422 disabled`。错误详情不会回显提交值或密钥。

## 并发控制

ETag 标识你读取的版本。修改 agent 时，所属主体需要工作空间 `write` 权限：

1. 读取 agent 并保留响应中的 `ETag`。
2. 提交修改，在 `If-Match` 中携带该值，包括其中的双引号。
3. 再次读取 agent，确认描述已保存。

将 `AGENT_ID` 替换为上面选取的 ID：

```sh
AGENT_ID='ap_REPLACE_WITH_RETURNED_ID'
curl -i "$A13N_URL/api/v1/agents/$AGENT_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

将返回的 ETag 复制到 `ETAG`；下方值是占位符：

```sh
ETAG='"ap_REPLACE_WITH_RETURNED_ID:1"'
curl -i -X PATCH "$A13N_URL/api/v1/agents/$AGENT_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "If-Match: $ETAG" \
  -d '{"description": "Answers billing questions"}'
curl "$A13N_URL/api/v1/agents/$AGENT_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"
```

如果修改返回 `412`，说明其他变更已使 ETag 过期。重新读取资源，判断原修改是否仍适用，再使用新读到的 ETag。不要盲目替换请求头。缺少 `If-Match` 返回 `428`；弱验证器和 `*` 不会匹配。每个声明 `If-Match` 的操作都要求它，即使 OpenAPI 将该请求头标为可选。

强 ETag 的格式为 `"{id}:{version}"`，模型使用 `"{key}:{version}"`。也可以根据带版本视图的 ID/key 和 `version` 组成该值。收件箱编辑、撤回和重排、线程更新，以及环境/记忆挂载变更，都使用**线程**的 ETag。发布 agent 或 skill 修订版本使用其主记录的 ETag。普通创建操作和 interrupt 等运行命令无需 ETag；例外请查看对应操作。

## 幂等请求

`If-Match` 防止并发变更覆盖你的编辑；`Idempotency-Key` 用于响应丢失后重试同一次逻辑提交。以下操作要求提供由 1–512 个可见 ASCII 字符组成的 key：

- `POST …/threads` 和 `POST …/threads/{thread_id}/inbox`
- `POST …/runs/{run_id}/fork` 和 `POST …/runs/{run_id}/resume`
- `POST …/schedules` 和 `POST …/schedules/{schedule_id}/dispatch`
- `POST …/finding-analyses`
- `POST …/uploads`

提交前生成一个唯一 key。重试时保留相同 key、目标和请求内容；新的逻辑请求使用新 key。Key 属于调用者和工作空间，不会过期。同一 key 用于不同内容或目标时返回 `409 conflict`，原因为 `idempotency_key_reused`。

首次提交通常返回 `201`；手动派发 Schedule 返回 `202`，上传返回 `200`。重放返回 `200` 和同一组已创建对象的**当前**状态。根据该状态继续处理；提交被接受不代表执行完成。[连接应用](connect-application.md#direct-http-integration)演示了先保存线程和收件箱记录，再跟踪分配到的运行。

创建 [Schedule](schedules.md) 会委托持久执行权限。撤销创建时的 API 密钥或等待其过期不会停止后续提交；请暂停或归档 Schedule。

## 分页

读取 `items`，然后按 `next_cursor` 继续，直到它为 `null`。普通集合分页接受 1–100 的 `limit`，默认 50。各页保持相同的集合、工作空间和筛选条件：

```sh
curl -G "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode 'archived=false' --data-urlencode 'limit=10'
```

如果响应中的 `next_cursor` 非空，将它复制到 `CURSOR`：

```sh
CURSOR='REPLACE_WITH_NEXT_CURSOR'
curl -G "$A13N_URL/api/v1/agents" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode 'archived=false' --data-urlencode 'limit=10' \
  --data-urlencode "cursor=$CURSOR"
```

将游标作为不透明值处理。游标用于其他集合、范围或筛选条件时返回 `invalid_cursor`。有界目录、工具集、挂载和执行尝试可能一次返回全部条目而不分页；对应操作的 schema 会说明响应结构。

## 限制

请求体不得超过 `server.request_bytes`，并须在 `server.request_timeout` 内到达，否则分别返回 `413` 和 `408`。上传和图片有更小的限制。凭据检查、上传和授权回调可能返回带 `Retry-After` 的 `429`；线程收件箱已满也返回 `429`。

## 标识符与时间

原样保存返回的 ID。前缀表示类型，例如 `ws_`、`ap_`（agent）、`sk_`（skill）、`sess_`、`thread_` 和 `run_`。模型在路径和引用中使用 [key](resources.md#common-conventions)。时间戳使用带偏移量的 RFC 3339 格式。

## 事件流

通过 `GET /api/v1/sessions/{session_id}/stream` 观察对话。它使用 server-sent events 传递会话中线程的状态和活跃运行的实时输出。连接、回读和执行控制见[会话事件流流程](stream-and-read-results.md#follow-a-session-stream)。

## 读取摘要和指定详情

详细配置参阅 [读取大型 API 结果](reading-api.md#read-summaries-and-selected-details).
