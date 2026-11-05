---
title: Upload and retrieve files
description: Attach a local file to a message and receive verified lifecycle notifications.
---

Use assets to pass files into a conversation or retrieve files an agent produces. Use the separate [webhook guide](webhooks.md) for lifecycle notifications.

## Uploads

To attach a local file, first upload its bytes, then create an asset from the returned upload ID. You need a workspace API key with `write` permission. Set `A13N_URL` to your Service origin and `A13N_API_KEY` to that key.

1. Choose a local file such as `report.pdf`. The default upload limit is 1 MiB (`objects.upload_bytes`).
2. Choose an idempotency key for this upload. Keep the key when retrying the same request.
3. Send multipart form data with a part named `file`:

```sh
curl -sS -X POST "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Idempotency-Key: report-upload-1" \
  -F 'file=@report.pdf;type=application/pdf'
```

Save the response's `upload_id` for the next step. The response also reports the filename, content type, size and SHA-256 `digest`. An upload stages bytes in the workspace; it can become an asset or a [skill package](skills.md#add-a-skill).

A retry returns the same upload when the caller, workspace, key, bytes, filename and content type match. Changing the file or its metadata under that key returns `409 conflict` with reason `idempotency_key_reused`. Uploads also count against the per-principal limit of `objects.upload_limit` requests per `objects.upload_window_seconds`.

## Assets

Create the named, immutable asset using the upload ID. Set `UPLOAD` to the `upload_id` returned above:

```sh
curl -sS -X POST "$A13N_URL/api/v1/assets" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"upload_id\": \"$UPLOAD\", \"name\": \"report.pdf\"}"
```

The first creation returns `201` and an asset `id`. Repeating the same upload and name returns that asset with `200`. Each upload produces at most one asset; changing the name on a repeat request conflicts.

Add the asset ID to a [message's content](agents-and-runs.md#submit-a-message), alongside your question:

```json
[
  {"type": "text", "text": "Summarize the attached report."},
  {"type": "asset", "asset_id": "ast_..."}
]
```

Submit the message with an identity authorized to run the agent. The run reads the attachment with the submitter's access and supplies it as media, text or an environment file, depending on the [attachment type](agents-and-runs.md#attached-files).

To retrieve or retire assets:

| Task                            | Request                                                |
| ------------------------------- | ------------------------------------------------------ |
| List assets                     | `GET /api/v1/assets`                                   |
| Read metadata and its ETag      | `GET /api/v1/assets/{asset_id}`                        |
| Download bytes as an attachment | `GET /api/v1/assets/{asset_id}/content`                |
| Retire an asset                 | `DELETE /api/v1/assets/{asset_id}` with its `If-Match` |

Retirement prevents new messages from attaching the asset. Its content remains readable for existing history.

For agent-produced files, enable the `assets` toolset and give the agent an environment containing the file. The agent calls `publish_asset`; the returned asset records its producing run, attempt and tool call in `source`. Published files are limited by `objects.max_bytes`.

## Webhooks

<span id="manage-in-console"></span> <span id="create-through-the-api"></span> <span id="payload-and-signature"></span> <span id="delivery-and-retries"></span>

- [Receive lifecycle webhooks](webhooks.md)
