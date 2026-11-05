---
title: 接收生命周期 webhook
description: 配置接收端、验证签名并对生命周期通知去重。
---

服务器需要接收生命周期通知且不保持事件流时，使用 webhook。先准备可访问、能验证签名并持久去重的接收端，再由工作空间管理员创建订阅并验证一次投递。

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

升级已有部署前，请检查[兼容性说明](upgrade-notes.md#webhook-signatures)。
