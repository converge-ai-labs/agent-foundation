---
title: 文件与 webhook
description: 上传文件、发布资产，并通过 webhook 订阅事件。
---

## 上传

上传将一个文件暂存到工作空间，供后续作为 [skill 包](skills.md#add-a-skill)或资产使用。使用 multipart form data 发送，part 名为 `file`，并提供 `Idempotency-Key`：

```sh
curl -X POST "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: report-2026-09" -F file=@report.pdf
```

响应返回 `upload_id`、文件名、内容类型、大小和 SHA-256 `digest`。上传 ID 从工作空间、调用者和幂等 key 派生，因此响应丢失后使用相同 key 和文件重试会返回同一上传；相同 key 对应不同内容则返回 `409 conflict`（原因为 `idempotency_key_reused`）。上传需要 `write`，大小受 `objects.upload_bytes` 限制（默认 1 MiB），并计入按主体计算的限流额度（每 `objects.upload_window_seconds` 允许 `objects.upload_limit` 次）。

## 资产

资产是带名称的不可变内容，可用于消息，也可由 agent 发布。

- **创建：** 使用 `POST /api/v1/assets` 和 `{"upload_id": ..., "name": ...}` 从上传创建。首次返回 `201`；相同上传和名称重复创建返回同一资产及 `200`。一次上传最多转换为一个资产。
- **读取：** 使用 `GET …/assets`、`GET …/assets/{asset_id}` 和 `GET …/assets/{asset_id}/content`；内容端点将字节作为附件返回。
- **停用：** 使用 `DELETE …/assets/{asset_id}` 和对应 `If-Match`。停用资产不能添加到新消息，但引用它的历史仍可读取内容。

使用内容部分 `{"type": "asset", "asset_id": "ast_..."}` 将资产附加到消息；参阅[消息](agents-and-runs.md#submit-a-message)。运行按提交者权限读取内容，并以媒体、文本或运行环境中的文件形式提供给模型；参阅[附件](agents-and-runs.md#attached-files)。

启用 `assets` 工具集的 agent 可通过 `publish_asset` 发布环境文件。新资产在 `source` 中记录生成它的运行、执行尝试和工具调用，大小受 `objects.max_bytes` 限制。

## Webhook

订阅将工作空间的运行生命周期事件发送到 HTTPS 端点。管理订阅和读取投递记录需要工作空间 `admin` 权限。

```sh
curl -X POST "$A13N_URL/api/v1/subscriptions" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Run outcomes", "url": "https://hooks.example.com/a13n",
       "kinds": ["run.completed", "run.failed", "run.waiting"],
       "filter": {"agent_id": "ap_..."}}'
```

- `kinds` 选择事件：`run.accepted`、`run.running`、`run.waiting`、`run.completed`、`run.failed`、`run.cancelled`，以及执行尝试事件 `run_attempt.leased`、`run_attempt.running`、`run_attempt.succeeded`、`run_attempt.yielded`、`run_attempt.failed`、`run_attempt.cancelled`。
- `filter` 可选限定到 `agent_id`、`session_id` 或 `thread_id`。
- `signing_secret`（16–256 个字符）可选；未提供时由 Service 生成。密钥仅在创建响应中返回。
- `PATCH` 修改名称、URL、kinds、filter、`enabled` 或 `signing_secret`；`DELETE` 移除订阅。两者都需要 `If-Match`。修改只影响之后排队的投递。
- 一个工作空间最多 `control.subscriptions` 个订阅（默认 32）。保存时和每次投递时，URL 都必须符合[出站策略](configuration.md#outbound-requests)。

### 载荷与签名

每个事件通过 JSON `POST` 发送：

```json
{
  "id": "obx_...",
  "type": "run.completed",
  "occurred_at": "2026-09-24T08:00:00+00:00",
  "workspace_id": "ws_...",
  "run": {"id": "run_...", "session_id": "sess_...", "thread_id": "thread_...", "agent_id": "ap_...",
          "agent_revision_id": "apr_...", "status": "completed", "trigger": "input", "wait_reason": null, "failure": null},
  "attempt": null
}
```

Run 事件包含 `attempt: null`；执行尝试事件添加 attempt 的 `id`、`number`、`status`、`start_reason`、`yield_reason` 和 `failure`。运行结果等详情请通过 API 获取。

每个请求包含：

| 请求头                     | 值                                                                                          |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `X-A13n-Delivery-Id`       | 投递 ID，也是载荷的 `id`。用它去重。                                                        |
| `X-A13n-Webhook-Timestamp` | 请求签名时的 Unix 秒数。                                                                    |
| `X-A13n-Webhook-Signature` | `v1=` 加上以签名密钥计算 `{timestamp}.{delivery_id}.{body}` 所得 HMAC-SHA256 的十六进制值。 |

使用原始请求体验证签名，并拒绝过旧时间戳：

```python
import hashlib, hmac

def verify(secret: str, headers, body: bytes) -> bool:
    signed = f"{headers['X-A13n-Webhook-Timestamp']}.{headers['X-A13n-Delivery-Id']}.".encode() + body
    expected = "v1=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers["X-A13n-Webhook-Signature"])
```

### 投递与重试

投递至少一次，不保证顺序。`control.webhook_timeout`（默认 10 秒）内的任何 `2xx` 响应都视为成功，不跟随重定向。否则按指数退避重试（最多 2、4、8、… 秒，20% 抖动，上限一小时），直到用完 `outbox.defaults.max_attempts` 次尝试（默认 12），随后标为 `dead`。

`GET …/subscriptions/{subscription_id}/deliveries` 按从新到旧列出投递及其状态（`pending`、`delivered`、`dead`）、尝试次数、最近错误和载荷。`POST …/deliveries/{delivery_id}/redeliver` 使用排队时相同的 ID、URL、载荷和签名密钥重新发送 dead 投递。已结束投递按该类型从结束时间计算的保留策略清理（成功默认一天，dead 默认十四天）。
