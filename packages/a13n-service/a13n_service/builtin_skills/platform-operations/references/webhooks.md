# Webhooks

Every subscription operation, including reads, requires workspace admin within the Run's accepted authority. Use `list_subscriptions`, `get_subscription` and `list_deliveries` to inspect state. Follow cursors. `update_subscription` changes only nonsecret fields; preserve the original `expected_version`. `delete_subscription` requires that version too. Writes use ordinary approval.

Use `configure_webhook` with a nonsecret `request` for creation or signing-key replacement. The Console admin enters a Standard Webhooks key privately or lets Service generate one on creation. Never request a key in chat. The generated key is shown once to the user; the result includes only status, subscription ID and version. Receivers verify `webhook-signature` and deduplicate by `webhook-id`.

Disabled/deleted subscriptions stop future matches; queued deliveries retain their original URL and signing key. Rotation applies to newly queued deliveries. `redeliver` requeues only a dead delivery and retains its ID; `signing_secret_unavailable` cannot be repaired by rotating the subscription. A queued response is not delivered: reread delivery evidence. Preserve HTTP endpoint policy and event filters.

A private-card cancellation before sending is `cancelled`; an unconfirmed write is `unknown`. Reconcile on the resource page before a fresh proposal; never repeat an uncertain write. An ETag conflict requires a new read and review. Without a supported client, direct the user to Settings → Webhooks.
