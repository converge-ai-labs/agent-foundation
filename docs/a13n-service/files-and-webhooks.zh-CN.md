---
title: 文件与 webhook
description: 将本地文件附加到消息，并接收经过验签的生命周期通知。
---

通过资产将文件传入对话，或下载 agent 生成的文件。通过 webhook，在运行或计划状态变化时通知你的应用。

## 上传

附加本地文件需要两步：先上传字节，再用返回的上传 ID 创建资产。准备一个具有工作空间 `write` 权限的 API key，将 `A13N_URL` 设为 Service 地址，`A13N_API_KEY` 设为该 key。

1. 选择本地文件，例如 `report.pdf`。默认上传上限为 1 MiB（`objects.upload_bytes`）。
2. 为这次上传选择幂等键。重试同一请求时保留该键。
3. 发送 multipart form data，part 名为 `file`：

```sh
curl -sS -X POST "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Idempotency-Key: report-upload-1" \
  -F 'file=@report.pdf;type=application/pdf'
```

保存响应中的 `upload_id`，供下一步使用。响应还包含文件名、内容类型、大小和 SHA-256 `digest`。上传将字节暂存到工作空间，后续可转为资产或 [skill 包](skills.md#add-a-skill)。

调用者、工作空间、key、字节、文件名和内容类型均相同的重试会返回同一次上传。在同一 key 下改变文件或元数据会返回 `409 conflict`，原因为 `idempotency_key_reused`。上传还计入按主体计算的额度：每 `objects.upload_window_seconds` 最多 `objects.upload_limit` 次请求。

## 资产

用上传 ID 创建带名称的不可变资产。将 `UPLOAD` 设为上一步返回的 `upload_id`：

```sh
curl -sS -X POST "$A13N_URL/api/v1/assets" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"upload_id\": \"$UPLOAD\", \"name\": \"report.pdf\"}"
```

首次创建返回 `201` 和资产 `id`。使用相同上传和名称重复创建，返回同一资产及 `200`。每次上传最多生成一个资产；重复请求若改变名称则会冲突。

将资产 ID 与问题一起放入[消息内容](agents-and-runs.md#submit-a-message)：

```json
[
  {"type": "text", "text": "Summarize the attached report."},
  {"type": "asset", "asset_id": "ast_..."}
]
```

使用有权运行该 agent 的身份提交消息。运行按提交者权限读取附件，并根据[附件类型](agents-and-runs.md#attached-files)，将内容作为媒体、文本或环境文件提供给模型。

读取或停用资产时使用以下接口：

| 任务               | 请求                                                  |
| ------------------ | ----------------------------------------------------- |
| 列出资产           | `GET /api/v1/assets`                                  |
| 读取元数据及 ETag  | `GET /api/v1/assets/{asset_id}`                       |
| 以附件形式下载字节 | `GET /api/v1/assets/{asset_id}/content`               |
| 停用资产           | `DELETE /api/v1/assets/{asset_id}`，携带其 `If-Match` |

停用后，新消息不能再附加该资产；已有历史仍可读取其内容。

对于 agent 生成的文件，启用 `assets` 工具集，并为 agent 提供包含该文件的环境。agent 调用 `publish_asset`；返回的资产在 `source` 中记录生成它的运行、执行尝试和工具调用。发布文件的大小受 `objects.max_bytes` 限制。

## Webhook

订阅将选定的工作空间生命周期事件发送到接收应用的 HTTPS 端点。管理订阅和查看投递记录需要工作空间 `admin` 权限。保存端点和每次投递时，地址都必须符合部署的[出站策略](configuration.md#outbound-requests)。

第一次验证分三步：创建订阅、在接收端验证通知、在 Console 中找到对应的成功投递记录。触发事件前，先按下文准备接收端，使其能对原始请求字节验签。

### 在 Console 中管理

1. 打开工作空间的**设置 → Webhooks**，选择**添加 Webhook**。
2. 填写名称和接收地址。选择**常用结果事件**，订阅 `run.waiting`、`run.completed`、`run.failed` 和 `run.cancelled`。
3. 如有需要，将事件限定到某个 agent、会话或线程。所有已填写的条件都必须匹配。
4. 选择**创建 Webhook**。关闭弹窗前，将签名密钥保存到接收端；密钥仅显示一次。
5. 向符合条件的 agent 提交消息，让运行到达所选结果之一。
6. 打开该 webhook 的**投递记录**，刷新列表并查看状态和载荷。在接收端确认同一个 `webhook-id` 已通过验证并被接收。

通过**配置 → 编辑**修改地址、事件、匹配条件或签名密钥。密钥留空则保留当前值。也可以启用、停用或删除订阅。修改影响之后的通知；已排队的投递保留原地址、载荷和密钥。如果另一位管理员先修改了订阅，请加载最新版本，检查保留的修改后再保存。

### 通过 API 创建

将 `AGENT` 设为已有 agent 的 ID，`WEBHOOK_URL` 设为接收端的 HTTPS 地址。此请求使用 admin key：

```sh
curl -sS -X POST "$A13N_URL/api/v1/subscriptions" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"Run outcomes\", \"url\": \"$WEBHOOK_URL\", \
       \"kinds\": [\"run.completed\", \"run.failed\", \"run.waiting\"], \
       \"filter\": {\"agent_id\": \"$AGENT\"}}"
```

保存返回的订阅 ID 和签名密钥。省略 `signing_secret` 时，Service 用 32 个随机字节生成密钥。自行提供的密钥必须为 `whsec_` 加 24–64 字节的标准 base64；其他格式返回 `invalid_argument`。密钥仅在创建时返回。

根据应用需要选择 `kinds`：

| 事件类别 | Kinds                                                                                                                                                          |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 运行     | `run.accepted`、`run.running`、`run.waiting`、`run.completed`、`run.failed`、`run.cancelled`                                                                   |
| 执行尝试 | `run_attempt.leased`、`run_attempt.running`、`run_attempt.succeeded`、`run_attempt.yielded`、`run_attempt.failed`、`run_attempt.cancelled`                     |
| 计划     | `schedule.enabled`、`schedule.paused`、`schedule.completed`、`schedule.archived`                                                                               |
| 触发记录 | `schedule_occurrence.submitted`、`schedule_occurrence.skipped`、`schedule_occurrence.failed`、`schedule_occurrence.cancelled`、`schedule_occurrence.completed` |

`filter` 支持 `agent_id`、`session_id`、`thread_id` 和 `schedule_id`。提供的每个身份字段都必须存在且匹配。计划筛选条件也会通过计划来源匹配运行和执行尝试。仅包含计划的事件不能匹配线程条件。

对于计划，需要区分[调度结束与执行完成](schedules.md#read-the-result)。创建暂停状态的计划不发事件；启用状态创建发送 `schedule.enabled`。重试和无状态变化的命令不重复生成通知。历史事件不补发，历史记录不补齐。

`PATCH /api/v1/subscriptions/{id}` 修改名称、URL、kinds、filter、`enabled` 或 `signing_secret`；`DELETE` 删除订阅。两者都要求当前 `If-Match`。每个工作空间最多支持 `control.subscriptions` 个订阅，默认 32 个。

### 载荷与签名

每条通知都是 JSON `POST`。运行事件采用以下结构，其中 ID 为占位符：

```json
{
  "id": "obx_...",
  "type": "run.completed",
  "occurred_at": "2026-09-24T08:00:00+00:00",
  "workspace_id": "ws_...",
  "run": {"id": "run_...", "session_id": "sess_...", "thread_id": "thread_...", "agent_id": "ap_...",
          "agent_revision_id": "apr_...", "status": "completed", "trigger": "input", "wait_reason": null,
          "failure": null, "schedule_origin": null},
  "attempt": null
}
```

事件用于识别这次状态变化；需要当前状态或结果时，再通过 API 读取运行。执行尝试事件会填充 `attempt`，包含 `id`、`number`、`status`、`start_reason`、`yield_reason` 和 `failure`。

计划事件用 `schedule` 替代 `run` 和 `attempt`，包含 `id`、`session_id`、`agent_id`、`name`、`state`、`pause_reason` 和 `version`。触发记录事件使用 `occurrence`，即包含身份、状态/原因和完成字段的紧凑历史记录，不含输入快照。完成字段包括 `execution_outcome`、`completion_run_id` 和 `completed_at`。所有类别共有 `id`、`type`、`occurred_at` 和 `workspace_id`；计划触发的运行带有 `run.schedule_origin: {schedule_id, occurrence_id}`。

使用 [Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks) 库验签。将收到的原始请求体及以下请求头传给验证器：

| 请求头              | 含义                                                                                                          |
| ------------------- | ------------------------------------------------------------------------------------------------------------- |
| `webhook-id`        | 投递 ID，也是载荷的 `id`；用于处理去重。                                                                      |
| `webhook-timestamp` | 签名时间，Unix 秒数。                                                                                         |
| `webhook-signature` | `v1,` 加 `{webhook-id}.{webhook-timestamp}.{body}` 的 HMAC-SHA256 的 base64，密钥为 base64 解码后的签名密钥。 |

Python 接收端安装 `standardwebhooks` 包后，可用一个小函数验签：

```python
from standardwebhooks import Webhook


def verify_event(secret: str, headers: dict[str, str], body: bytes) -> dict:
    return Webhook(secret).verify(body, headers)
```

处理事件前调用该函数。在 HTTP 框架中将 `WebhookVerificationError` 映射为 `4xx` 响应。验签通过后，按 `webhook-id` 去重，持久保存待处理事件，再返回 `2xx`。验签前不要重新序列化请求体。保持接收端时钟同步：验证器会拒绝相差超过五分钟的时间戳。

### 投递与重试

Service 至少投递一次，不保证顺序。在 `control.webhook_timeout`（默认 10 秒）内返回任意 `2xx` 即标记投递成功，不跟随重定向。超时可能发生在接收端已接受事件之后，因此重试必须经过相同的去重逻辑。

失败请求按指数退避重试，间隔最多为 2、4、8、… 秒，带 20% 抖动，上限一小时。尝试次数取 `outbox.by_kind.webhook.max_attempts`，未配置时取 `outbox.defaults.max_attempts`（默认 12）。次数耗尽后，投递变为 `dead`。

通过 `GET /api/v1/subscriptions/{subscription_id}/deliveries` 或 Console 的**投递记录**检查结果。记录按从新到旧排列，包含状态（`pending`、`delivered`、`dead`）、尝试次数、最后一次错误和载荷。修复接收端问题后，对符合条件的 dead 投递选择**重新投递**，或调用 `POST /api/v1/subscriptions/{subscription_id}/deliveries/{delivery_id}/redeliver`。重新投递会重置尝试额度，保留原 ID、地址、载荷和密钥。刷新以确认结果；入队表示待投递，不代表已收到。

已结束的投递记录默认保留到成功后一天，或变为 dead 后十四天。这些期限由 webhook outbox 策略控制。

对于 Standard Webhooks 签名启用前创建的订阅，以管理员身份通过 `PATCH` 换成有效的 `whsec_…` 密钥，或重新创建订阅。旧投递保留旧密钥，会以 `signing_secret_unavailable` 失败，重新投递无法修复。新投递使用替换后的密钥。接收端使用上面的 `webhook-*` 请求头；原来的 `X-A13n-*` 签名格式已不再发送。
