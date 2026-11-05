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

<span id="manage-in-console"></span> <span id="create-through-the-api"></span> <span id="payload-and-signature"></span> <span id="delivery-and-retries"></span>

- [接收生命周期 webhook](webhooks.md)
