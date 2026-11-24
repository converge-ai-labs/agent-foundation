---
title: HTTP API 约定
description: Service HTTP API 的鉴权请求头、工作区路由、Idempotency-Key 幂等重放、ETag 乐观锁条件更新、游标分页与结构化错误格式。
---

所有 Service JSON API 路径均以 `/api/v1` 开头（OpenAPI 规范位于 `/api/v1/openapi.json`，交互式文档位于 `/api/v1/docs`）。请求体拒绝未知字段，时间采用 RFC 3339 格式，资源 ID 带类型前缀（`ws_`、`ap_`、`sk_`、`sess_`、`thread_`、`run_`；模型使用 `key`）。

## 认证

在请求头中携带[工作区 API 密钥](identity.md#api-keys)（`Authorization: Bearer $A13N_API_KEY`）：

```sh
curl -i "$A13N_URL/api/v1/agents" -H "Authorization: Bearer $A13N_API_KEY"
```

使用登录 Cookie（`/api/v1/auth/login`）的浏览器客户端在发起非 `GET`/`HEAD`/`OPTIONS` 请求时须携带 `X-CSRF-Token`，且 `Origin` 须匹配 `server.public_url`。账号设置、签发密钥与发送邀请必须使用登录会话。

### 工作区选择

- **API 密钥：** 自动绑定所属工作区，无需额外请求头。
- **登录会话：** 访问工作区资源（`/agents`、`/threads`、`/runs` 等）时须携带 `X-Workspace-ID: ws_...`。

## 并发控制（ETag / If-Match）

修改资源时通过强 ETag（`"{id}:{version}"`）防止并发覆盖：

1. 先通过 `GET` 读取资源并保留响应头中的 `ETag`（包含双引号）。
2. 在 `PATCH` / `PUT` / `DELETE` 请求头中传入 `If-Match: "<id>:<version>"`（缺失返回 `428 precondition_required`；版本过期返回 `412 precondition_failed`，需重新读取后再提交）：

```sh
ETAG='"ap_example:1"'
curl -i -X PATCH "$A13N_URL/api/v1/agents/ap_example" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" -H "If-Match: $ETAG" \
  -d '{"description": "Answers billing questions"}'
```

线程收件箱调整以及环境/记忆挂载变更统一使用**线程**的 ETag。

## 幂等请求（Idempotency-Key）

向 `/threads`、`/threads/{id}/inbox`、`/runs/{id}/fork`、`/runs/{id}/resume`、`/schedules`、`/schedules/{id}/dispatch`、`/finding-analyses` 与 `/uploads` 发起 `POST` 时须携带唯一的 `Idempotency-Key`（1–512 个可见 ASCII 字符）。网络超时重试时保持相同键与请求体即可安全重放（重放返回 `200`；若以同一键提交不同内容则返回 `409` `idempotency_key_reused`）。

## 分页、流式订阅与错误处理

- **游标分页：** 传入 `limit`（`1`–`100`，默认 `50`），在相同筛选条件下沿 `next_cursor` 翻页直至为 `null`。
- **SSE 事件流：** 订阅 `GET /api/v1/sessions/{session_id}/stream`（见[会话流指南](stream-and-read-results.md#follow-a-session-stream)）或按需分段读取（见[读取大型 API 结果](reading-api.md)）。
- **结构化错误：** 代码按 `error.code` 分支（`invalid_argument` 400、`unauthenticated` 401、`forbidden` 403、`not_found` 404、`already_exists` / `conflict` 409、`precondition_failed` 412、`payload_too_large` 413、`disabled` 422、`precondition_required` 428、带 `Retry-After` 的 `rate_limited` 429、`internal` 500、`unavailable` 503），并保留 `X-Request-Id`（`error.request_id`）用于日志排障。
