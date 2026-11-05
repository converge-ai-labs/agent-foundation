---
title: Receive lifecycle webhooks
description: Configure a receiver, verify signatures, and deduplicate lifecycle notifications.
---

Use webhooks when your server needs lifecycle notifications without keeping a stream open. Prepare a reachable receiver that verifies signatures and durably deduplicates events; a workspace administrator can then create a subscription and verify one delivered notification.

## Webhooks

A subscription sends selected workspace lifecycle events to your receiving application's HTTPS endpoint. You need workspace `admin` permission to manage subscriptions and inspect deliveries. The endpoint must pass the deployment's [outbound policy](configuration.md#outbound-requests), both when saved and on every delivery.

The first successful test has three parts: create a subscription, verify a notification in your receiver, then find the corresponding delivered record in Console. Prepare the receiver to verify raw request bytes as described below before triggering the event.

### Manage in Console

1. Open the workspace's **Settings → Webhooks** and select **Add Webhook**.
2. Enter a name and your receiver's endpoint URL. Select **Common outcomes** for `run.waiting`, `run.completed`, `run.failed` and `run.cancelled`.
3. Optionally restrict events to an agent, session or thread. All specified conditions must match.
4. Select **Create Webhook**. Save the signing secret in your receiver before closing the dialog; it is shown only once.
5. Submit a message to a matching agent and let the run reach one of the selected outcomes.
6. Open the webhook's **Deliveries**, refresh the list, and inspect its status and payload. In your receiver, confirm the same `webhook-id` was verified and accepted.

Use **Configuration → Edit** to change the endpoint, events, conditions or signing secret. Leave the secret field empty to retain the current value. You can also enable, disable or delete the subscription. Changes affect future notifications; queued deliveries keep their saved URL, payload and secret. If another administrator edits the subscription first, load the current version and review your preserved edits before saving.

### Create through the API

Set `AGENT` to an existing agent ID and `WEBHOOK_URL` to your receiver's HTTPS URL. Use an admin key for this request:

```sh
curl -sS -X POST "$A13N_URL/api/v1/subscriptions" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"Run outcomes\", \"url\": \"$WEBHOOK_URL\", \
       \"kinds\": [\"run.completed\", \"run.failed\", \"run.waiting\"], \
       \"filter\": {\"agent_id\": \"$AGENT\"}}"
```

Save the returned subscription ID and signing secret. Service generates the secret from 32 random bytes when you omit `signing_secret`. A supplied secret must be `whsec_` followed by the standard base64 of 24–64 bytes; other formats return `invalid_argument`. The secret is returned only on creation.

Choose `kinds` for the events your application needs:

| Event family | Kinds                                                                                                                                                          |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Run          | `run.accepted`, `run.running`, `run.waiting`, `run.completed`, `run.failed`, `run.cancelled`                                                                   |
| Attempt      | `run_attempt.leased`, `run_attempt.running`, `run_attempt.succeeded`, `run_attempt.yielded`, `run_attempt.failed`, `run_attempt.cancelled`                     |
| Schedule     | `schedule.enabled`, `schedule.paused`, `schedule.completed`, `schedule.archived`                                                                               |
| Occurrence   | `schedule_occurrence.submitted`, `schedule_occurrence.skipped`, `schedule_occurrence.failed`, `schedule_occurrence.cancelled`, `schedule_occurrence.completed` |

`filter` supports `agent_id`, `session_id`, `thread_id` and `schedule_id`. Every supplied identity must be present and match. A schedule filter also matches runs and attempts through their schedule origin. A schedule-only event cannot match a thread filter.

For schedules, distinguish [scheduling from execution completion](schedules.md#read-the-result). Creating a paused schedule emits no event; enabled creation emits `schedule.enabled`. Retries and unchanged commands do not duplicate transition notices. Historical events are not replayed or backfilled.

`PATCH /api/v1/subscriptions/{id}` changes the name, URL, kinds, filter, `enabled` or `signing_secret`; `DELETE` removes the subscription. Both require its current `If-Match`. A workspace supports up to `control.subscriptions` subscriptions, 32 by default.

### Payload and signature

Every notification is a JSON `POST`. A run event has this shape; IDs below are placeholders:

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

Use the event to identify the transition, then fetch the run through the API when you need its current state or result. Attempt events populate `attempt` with `id`, `number`, `status`, `start_reason`, `yield_reason` and `failure`.

Schedule events use `schedule` in place of `run` and `attempt`, with `id`, `session_id`, `agent_id`, `name`, `state`, `pause_reason` and `version`. Occurrence events use `occurrence`, a compact history record with identities, status/reason and completion fields, without the input snapshot. Completion includes `execution_outcome`, `completion_run_id` and `completed_at`. All families share `id`, `type`, `occurred_at` and `workspace_id`; scheduled runs carry `run.schedule_origin: {schedule_id, occurrence_id}`.

Verify notifications using a [Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks) library. Pass the exact raw body and these request headers to the verifier:

| Header              | Meaning                                                                                                               |
| ------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `webhook-id`        | Delivery ID, also the payload's `id`; use it to deduplicate processing.                                               |
| `webhook-timestamp` | Signing time in Unix seconds.                                                                                         |
| `webhook-signature` | `v1,` plus the base64 HMAC-SHA256 of `{webhook-id}.{webhook-timestamp}.{body}`, keyed with the base64-decoded secret. |

For a Python receiver with the `standardwebhooks` package installed, verification can be a small function:

```python
from standardwebhooks import Webhook


def verify_event(secret: str, headers: dict[str, str], body: bytes) -> dict:
    return Webhook(secret).verify(body, headers)
```

Call it before processing the event. Map `WebhookVerificationError` to a `4xx` response in your HTTP framework. After verification, deduplicate by `webhook-id`, durably accept the event for processing, and return `2xx`. Do not re-serialize the body before verification. Keep the receiver's clock synchronized: the verifier rejects timestamps more than five minutes away.

### Delivery and retries

Service delivers at least once, without ordering guarantees. Any `2xx` within `control.webhook_timeout` (10 seconds by default) marks a delivery successful. Redirects are not followed. A timeout can occur after your receiver has accepted the event, so retries must use the same deduplication path.

Failed requests retry with exponential backoff of up to 2, 4, 8, … seconds, with 20% jitter and a one-hour cap. The attempt budget comes from `outbox.by_kind.webhook.max_attempts`, falling back to `outbox.defaults.max_attempts` (12 by default). Exhausted deliveries become `dead`.

Inspect `GET /api/v1/subscriptions/{subscription_id}/deliveries` or Console's **Deliveries**. Records are newest first and include status (`pending`, `delivered`, `dead`), attempts, last error and payload. Correct the receiver problem, then select **Retry delivery** or call `POST /api/v1/subscriptions/{subscription_id}/deliveries/{delivery_id}/redeliver` for an eligible dead delivery. Redelivery gives it a fresh attempt budget while retaining the original ID, URL, payload and secret. Refresh to confirm the result; queued means pending, not received.

Settled delivery records are retained for one day after success or fourteen days after becoming dead by default. The webhook outbox policy controls these periods.

Upgrading an existing deployment? Check [Webhook signatures](upgrade-notes.md#webhook-signatures).
