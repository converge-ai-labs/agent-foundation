# Files and webhooks

## Uploads

An upload stages one file in a workspace for later use: as a [skill package](skills.md#add-a-skill) or as an asset. Send it as multipart form data with a part named `file` and an `Idempotency-Key`:

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: report-2026-09" -F file=@report.pdf
```

The response gives the `upload_id`, filename, content type, size and SHA-256 `digest`. The upload ID is derived from the workspace, the caller and the idempotency key, so retrying a lost request with the same key and file returns the same upload; different content with the same key is `409 conflict` (reason `idempotency_key_reused`). Uploads need `write`, are limited to `objects.upload_bytes` (1 MiB by default), and count against a per-principal rate limit (`objects.upload_limit` per `objects.upload_window_seconds`).

## Assets

An asset is immutable content with a name, usable in messages and published by agents.

- **Create** from an upload with `POST /api/v1/workspaces/{workspace_id}/assets` and `{"upload_id": ..., "name": ...}`. The first creation returns `201`; repeating it with the same upload and name returns the same asset with `200`. An upload becomes at most one asset.
- **Read** with `GET …/assets`, `GET …/assets/{asset_id}`, and `GET …/assets/{asset_id}/content`, which returns the bytes as an attachment.
- **Retire** with `DELETE …/assets/{asset_id}` and its `If-Match`. A retired asset cannot be attached to new messages, but its content stays readable for the history that references it.

Attach an asset to a message as a content part `{"type": "asset", "asset_id": "ast_..."}`; see [messages](agents-and-runs.md#submit-a-message). The run reads the content with the submitter's access and gives it to the model as media, as text, or as a file in the run's environment; see [attached files](agents-and-runs.md#attached-files).

Agents with the `assets` toolset enabled can publish a file from their environment with `publish_asset`. The new asset records the run, attempt and tool call that produced it in `source`, and is bounded by `objects.max_bytes`.

## Webhooks

A subscription delivers run lifecycle events of a workspace to an HTTPS endpoint. Managing subscriptions and reading their deliveries requires workspace `admin`.

```sh
curl -X POST "$A13N_URL/api/v1/workspaces/$WORKSPACE/subscriptions" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Run outcomes", "url": "https://hooks.example.com/a13n",
       "kinds": ["run.completed", "run.failed", "run.waiting"],
       "filter": {"agent_id": "ap_..."}}'
```

- `kinds` selects events: `run.accepted`, `run.running`, `run.waiting`, `run.completed`, `run.failed`, `run.cancelled`, and for attempts `run_attempt.leased`, `run_attempt.running`, `run_attempt.succeeded`, `run_attempt.yielded`, `run_attempt.failed`, `run_attempt.cancelled`.
- `filter` optionally narrows events to an `agent_id`, `session_id` or `thread_id`.
- `signing_secret` (16–256 characters) is optional; without it the Service generates one. The secret is returned only in the creation response.
- `PATCH` changes the name, URL, kinds, filter, `enabled` or `signing_secret`; `DELETE` removes the subscription. Both take `If-Match`. Changes affect only deliveries queued afterwards.
- A workspace has at most `control.subscriptions` subscriptions (32 by default). The URL must pass the [outbound policy](configuration.md#outbound-requests) when saved and on every delivery.

### Payload and signature

Each event is a JSON `POST`:

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

Run events carry `attempt: null`; attempt events add the attempt's `id`, `number`, `status`, `start_reason`, `yield_reason` and `failure`. Fetch details, such as the run's result, through the API.

Every request carries:

| Header                     | Value                                                                                                       |
| -------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `X-A13n-Delivery-Id`       | The delivery ID, also the payload `id`. Use it to discard duplicates.                                       |
| `X-A13n-Webhook-Timestamp` | Unix seconds when the request was signed.                                                                   |
| `X-A13n-Webhook-Signature` | `v1=` followed by the hex HMAC-SHA256 of `{timestamp}.{delivery_id}.{body}`, keyed with the signing secret. |

Verify the signature over the raw body and reject old timestamps:

```python
import hashlib, hmac

def verify(secret: str, headers, body: bytes) -> bool:
    signed = f"{headers['X-A13n-Webhook-Timestamp']}.{headers['X-A13n-Delivery-Id']}.".encode() + body
    expected = "v1=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers["X-A13n-Webhook-Signature"])
```

### Delivery and retries

Delivery is at least once and not ordered. Any `2xx` response within `control.webhook_timeout` (10 seconds by default) counts as delivered; redirects are not followed. Otherwise the delivery is retried with exponential backoff (up to 2, 4, 8, … seconds with 20% jitter, capped at one hour) until it has used `outbox.defaults.max_attempts` attempts (12 by default), and is then marked `dead`.

`GET …/subscriptions/{subscription_id}/deliveries` lists deliveries newest first with their status (`pending`, `delivered`, `dead`), attempts, last error and payload. `POST …/deliveries/{delivery_id}/redeliver` sends a dead delivery again with the same ID, URL, payload and signing secret it was queued with. Settled deliveries are purged according to the kind’s settlement-based retention (success: one day; dead: fourteen days by default).
