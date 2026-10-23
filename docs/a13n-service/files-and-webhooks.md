---
title: Files and webhooks
description: Upload files, publish assets, and subscribe to events with webhooks.
---

## Uploads

An upload stages one file in a workspace for later use: as a [skill package](skills.md#add-a-skill) or as an asset. Send it as multipart form data with a part named `file` and an `Idempotency-Key`:

```sh
curl -X POST "$A13N_URL/api/v1/uploads" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: report-2026-09" -F file=@report.pdf
```

The response gives the `upload_id`, filename, content type, size and SHA-256 `digest`. Retrying with the same caller, workspace, key, bytes, filename and content type returns the same upload. Changing any upload content or metadata under that key is `409 conflict` (reason `idempotency_key_reused`). Uploads need `write`, are limited to `objects.upload_bytes` (1 MiB by default), and count against a per-principal rate limit (`objects.upload_limit` per `objects.upload_window_seconds`).

## Assets

An asset is immutable, named content. You create assets from uploads, agents can publish them, and you attach them to messages.

- **Create** from an upload with `POST /api/v1/assets` and `{"upload_id": ..., "name": ...}`. The first creation returns `201`; repeating it with the same upload and name returns the same asset with `200`. An upload becomes at most one asset.
- **Read** with `GET …/assets`, `GET …/assets/{asset_id}`, and `GET …/assets/{asset_id}/content`, which returns the bytes as an attachment.
- **Retire** with `DELETE …/assets/{asset_id}` and its `If-Match`. A retired asset cannot be attached to new messages, but its content stays readable for the history that references it.

Attach an asset to a message as a content part `{"type": "asset", "asset_id": "ast_..."}`; see [messages](agents-and-runs.md#submit-a-message). The run reads the content with the submitter's access and gives it to the model as media, as text, or as a file in the run's environment; see [attached files](agents-and-runs.md#attached-files).

Agents with the `assets` toolset enabled can publish a file from their environment with `publish_asset`. The new asset records the run, attempt and tool call that produced it in `source`, and is bounded by `objects.max_bytes`.

## Webhooks

A subscription delivers run lifecycle events of a workspace to an HTTPS endpoint. Managing subscriptions and reading their deliveries requires workspace `admin`.

### Manage in Console

1. Open the workspace's **Settings → Webhooks** and select **Add Webhook**.
2. Enter a name and endpoint URL. Select run or attempt events. **Common outcomes** selects `run.waiting`, `run.completed`, `run.failed` and `run.cancelled`.
3. Optionally limit notifications to an agent, session or thread. All specified conditions must match.
4. Select **Create Webhook**. Save the signing secret in your receiving application before closing the dialog. The secret is shown only once.

Open a Webhook to inspect **Deliveries** or its **Configuration**. The delivery details show the saved URL, event payload, attempt count and last error. **Retry delivery** queues an eligible failed delivery with its original ID, URL, payload and secret. Refresh the list to check the result. A queued retry does not confirm receipt.

Use **Edit** to change the endpoint, events, conditions or signing secret. Leave the secret field empty to keep the current secret. Configuration also offers enable, disable and delete. These changes affect future notifications; already queued deliveries continue with their saved configuration. If another administrator changes a Webhook while you edit, load the current version and review your preserved edits before saving.

### Create through the API

```sh
curl -X POST "$A13N_URL/api/v1/subscriptions" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "Content-Type: application/json" \
  -d '{"name": "Run outcomes", "url": "https://hooks.example.com/a13n",
       "kinds": ["run.completed", "run.failed", "run.waiting"],
       "filter": {"agent_id": "ap_..."}}'
```

- `kinds` selects events: `run.accepted`, `run.running`, `run.waiting`, `run.completed`, `run.failed`, `run.cancelled`, and for attempts `run_attempt.leased`, `run_attempt.running`, `run_attempt.succeeded`, `run_attempt.yielded`, `run_attempt.failed`, `run_attempt.cancelled`.
- `filter` optionally narrows events to an `agent_id`, `session_id`, `thread_id` or `schedule_id`. Conditions combine with AND; an event lacking a specified identity does not match. The Schedule filter also applies to Run and Attempt events through their origin.
- `signing_secret` is optional: `whsec_` followed by the standard base64 of 24 to 64 bytes, as [Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks) defines it. Without it the Service generates one from 32 random bytes. Any other value is rejected with `invalid_argument`. The secret is returned only in the creation response. Subscriptions created before this format must be re-keyed: send a `PATCH` with a new `whsec_…` secret as an admin, or recreate the subscription. Deliveries queued before the upgrade cannot be recovered: they fail with `signing_secret_unavailable`, and redelivering them reuses the old secret.
- `PATCH` changes the name, URL, kinds, filter, `enabled` or `signing_secret`; `DELETE` removes the subscription. Both take `If-Match`. Changes affect only deliveries queued afterwards.
- A workspace has at most `control.subscriptions` subscriptions (32 by default). The URL must pass the [outbound policy](configuration.md#outbound-requests) when saved and on every delivery.

Schedule events are `schedule.enabled`, `schedule.paused`, `schedule.completed` and `schedule.archived`. Occurrence events are `schedule_occurrence.submitted`, `.skipped`, `.failed`, `.cancelled` and `.completed`. Scheduling completion and execution completion are separate: see [schedule results](schedules.md#read-the-result). Paused creation emits nothing; enabled creation emits `schedule.enabled`. Retries and unchanged commands do not create duplicate transition notices. Historical completion records are not backfilled and past events are not replayed.

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

Schedule payloads replace `run` and `attempt` with `schedule` containing `id`, `session_id`, `agent_id`, `name`, `state`, `pause_reason` and `version`. Occurrence payloads contain `occurrence`, the compact history record with `schedule_id`, status/reason and completion fields, without the input snapshot. All event families share `id`, `type`, `occurred_at` and `workspace_id`. Completion reports `execution_outcome`, `completion_run_id` and `completed_at`. Run payloads include nullable `schedule_origin: {schedule_id, occurrence_id}`.

Deliveries are signed with the [Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks) scheme, so an unmodified Standard Webhooks library verifies them. Every request carries:

| Header              | Value                                                                                                                                |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `webhook-id`        | The delivery ID, also the payload `id`. Use it as the idempotency key to discard duplicates.                                         |
| `webhook-timestamp` | Unix seconds when the request was signed.                                                                                            |
| `webhook-signature` | `v1,` followed by the base64 HMAC-SHA256 of `{webhook-id}.{webhook-timestamp}.{body}`, keyed with the base64-decoded signing secret. |

Verify with a Standard Webhooks library over the raw body, exactly as received and not re-serialized. The library rejects a timestamp more than five minutes from your clock, so keep the receiver's clock synchronized. The earlier `X-A13n-*` headers and hex signature are no longer sent.

```python
from standardwebhooks import Webhook, WebhookVerificationError

def handle(secret: str, headers, body: bytes) -> None:
    try:
        event = Webhook(secret).verify(body, headers)  # webhook-id, webhook-timestamp, webhook-signature
    except WebhookVerificationError:
        return  # reject with a 4xx
    ...  # skip the event when headers["webhook-id"] was already processed
```

### Delivery and retries

Delivery is at least once and not ordered. Any `2xx` response within `control.webhook_timeout` (10 seconds by default) counts as delivered; redirects are not followed. Otherwise the delivery is retried with exponential backoff (up to 2, 4, 8, … seconds with 20% jitter, capped at one hour) until it has used the `webhook` outbox policy's `max_attempts` attempts (`outbox.by_kind.webhook`, else `outbox.defaults`; 12 by default), and is then marked `dead`.

`GET …/subscriptions/{subscription_id}/deliveries` lists deliveries newest first with their status (`pending`, `delivered`, `dead`), attempts, last error and payload. `POST …/deliveries/{delivery_id}/redeliver` sends a dead delivery again with the same ID, URL, payload and signing secret it was queued with. Settled deliveries are purged after the webhook retention period: by default one day after delivery, and fourteen days after a delivery became dead.
