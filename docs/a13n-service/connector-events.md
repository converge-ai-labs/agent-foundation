---
title: Connector events
description: Receive signed Composio events in a13n, save event history, or start an Agent conversation for each event using a service account.
---

Connector events let an external account start an Agent when something happens, such as a new issue or email. a13n receives and saves the event, then starts a conversation using the Agent and service account you selected. You can also choose to save events without running an Agent.

## Create a subscription

1. Add a Composio Provider and authorize the account under **Connections**. Ask your operator to configure the public callback URL below.
2. Open the Connection's **Events** tab. A workspace administrator clicks **Configure callback** once for the Provider's Composio project.
3. Click **Add subscription**, choose an event type, and complete its parameters and any setup instructions. Available events and forms come from Composio.
4. Choose **Save event** or **Start an agent**. Starting an Agent requires an administrator to select the Agent and a [service account](identity.md#service-accounts) with permission to run it. Saving fixes the selected Agent revision.
5. Wait for **Updating subscription…** to finish, generate a matching event in the external service, and open the subscription's **Recent events**. Click **Open conversation** after submission to inspect the Run.

The Composio project shares one callback across its subscriptions. If another application already uses the project's callback, use a separate project and Provider. a13n reports the conflict and preserves the existing callback.

## Configure the public callback

The operator sets a publicly reachable HTTPS base URL in Service configuration:

```toml
[connector_events]
public_url = "https://events.example.com"
```

Route `POST /api/v1/connector-webhooks/*` on that origin to the deployment's Control service. The Events tab displays the exact Provider callback URL. Keep management API authentication enabled; the callback authenticates Composio signatures itself. Deploy the new version to every serving Control before configuring the callback.

**Rotate signing key** updates the project's signing key. API key rotation uses the ordinary Provider editor and must retain the same Composio project. Recovery checks the current remote key after an interrupted rotation; **Retry callback setup** appears if automatic attempts are exhausted.

## Manage subscriptions and history

**Pause** stops accepting new executable events for the subscription; **Enable** rechecks the account and execution permissions. **Retire** ends the subscription permanently. Events already accepted continue using their saved target even after these changes.

**Edit** changes the name or execution target for future events. To change the source account, event type or parameters, retire the subscription and create a replacement. A revoked or changed Connection automatically pauses its subscriptions.

| Event status | Meaning and next action                                                   |
| ------------ | ------------------------------------------------------------------------- |
| Stored       | Saved without starting an Agent                                           |
| Pending      | Accepted and waiting for submission                                       |
| Submitted    | Handed to Inbox; open the conversation for execution status               |
| Ignored      | Arrived while the subscription was paused or retired                      |
| Failed       | Inspect the reason; an administrator can retry eligible delivery failures |

**Retry subscription setup** recovers a failed remote subscription operation. **Retry delivery** retries the saved event after its underlying problem is fixed. Delivery retry preserves the original Agent target; an incompatible event payload cannot be retried into execution.

Event payloads remain for seven days by default, while event identities and receipts remain for thirty days. Conversations retain their own history. Operators can adjust these values and callback/backlog bounds in the [configuration reference](configuration-reference.md).

## Use the HTTP API

Use a workspace API key and the usual workspace selection. Paths below start with `/api/v1`; list endpoints use cursor pagination.

| Task                              | Method and path                                                         |
| --------------------------------- | ----------------------------------------------------------------------- |
| Discover event types              | `GET /connector-providers/{provider_id}/apps/{app}/event-types`         |
| Inspect/configure shared callback | `GET` / `POST /connector-providers/{provider_id}/event-webhook`         |
| List/create subscriptions         | `GET` / `POST /connector-triggers`                                      |
| Read/edit target                  | `GET` / `PATCH /connector-triggers/{trigger_id}`                        |
| Change subscription state         | `POST /connector-triggers/{trigger_id}/{enable,pause,retire,reconcile}` |
| Read events                       | `GET /connector-events?trigger_id={trigger_id}`                         |
| Retry delivery                    | `POST /connector-events/{event_id}/retry`                               |

Creation requires `Idempotency-Key`; updates and commands require the current `If-Match` ETag. Callback setup uses the Provider's ETag. See the [API reference](api-reference/index.md) for request schemas. HTTP 204 on the signed callback confirms saved receipt; execution results are available through the returned event's Inbox/Thread references.
