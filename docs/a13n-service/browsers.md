---
title: Browsers
description: Attach an existing browser or reserve a managed one, then bind it to native agent tools.
---

A browser resource shares a live browser across runs and threads. Choose `managed` for a provider-owned allocation or `external_cdp` to attach an existing browser. Service manages access, tab ownership and logical selection independently of [environments](environments.md).

> [!IMPORTANT]
> External CDP attachment works without a browser provider. The built-in distribution has no live managed-browser supplier or Console takeover interface. Managed allocation requires a deployment-registered provider; inspect `GET /api/v1/provider-types/browser`. Human control requires a compatible managed provider and an integrating application.

## Reserve and bind a browser

Use [authenticated HTTP requests](http.md) in one workspace. Browser creation, binding and use require workspace `run`; release requires workspace `write`. Provider accounts retain their own permissions.

1. Create a browser provider account through `POST /api/v1/browser-providers`, using a registered type's schema. Store credentials through that API, never in agent messages.
2. Reserve a browser with `POST /api/v1/browsers`. Set `provider_id`, `name` and the provider's `recipe`. Omit `prepare` to reserve an identity without allocating a supplier session.
3. Enable the agent revision's `browser` toolset. Select only the necessary tools and [permissions](agents-and-runs.md#tool-permissions).
4. Read the thread's current ETag. Send `PUT /api/v1/threads/{thread_id}/browser` with that `If-Match` and the binding below. Do this before submitting the run that should use it.

```json
{
  "browser_id": "brw_example",
  "tab_access": "thread",
  "preparation": "first_use"
}
```

Replace the example ID with the returned browser ID. Read the desired binding with `GET /api/v1/threads/{thread_id}/browser`; send JSON `null` to the PUT endpoint to detach it. Binding changes apply to future runs. An accepted run keeps its original binding. Child and forked threads require an explicit binding.

The native tools cover observation, reading, navigation, actions, waits, tabs, dialogs, screenshots and download inspection. Uploads are unavailable until a provider exposes an authorized transfer boundary. Browser-host file paths are not paths in the run's mounted environments.

## Attach an existing browser

Use a direct browser-level `ws://` or `wss://` debugger endpoint for one stable existing Chromium instance. HTTP discovery URLs and page-level sockets are rejected. The URL and optional headers are secrets: supply them only to the attachment API, never in an agent message. Service encrypts them and ordinary reads never return them. Endpoint changes require another attachment and explicit rebinding.

The endpoint must be reachable from the Service worker. Loopback refers to that process's network environment. Destination policy applies at registration and every connection; plain WS requires an explicit allowed HTTP origin in deployment `providers.http_origins`. Run `allowed_hosts` restrictions apply too. Redirects are refused.

The following sequence uses `curl` and `jq`, the [HTTP authentication variables](http.md#authentication), an existing idle `THREAD_ID`, and an `AGENT_ID` whose revision enables `toolsets.browser.enabled`. Keep the complete secret endpoint in `CDP_URL`; put an optional authorization value in `CDP_AUTHORIZATION`.

1. Attach without connecting or allocating. The response has `source: "external_cdp"`, `provider_id: null`, `status: "reserved"` and `idle_seconds: null`.

```sh
BROWSER_ID=$(jq -n --arg url "$CDP_URL" --arg auth "${CDP_AUTHORIZATION:-}" \
  '{name: "Existing browser", cdp_url: $url, headers: (if $auth == "" then {} else {Authorization: $auth} end)}' | \
  curl --fail-with-body -sS "$A13N_URL/api/v1/browsers/attach" \
    -H "Authorization: Bearer $A13N_API_KEY" -H 'Content-Type: application/json' --data-binary @- | jq -r '.id')
```

2. Bind the attachment before submitting work. Use `global` to inspect pre-existing pages; `thread` sees only tabs created by that thread and their popups.

```sh
THREAD_ETAG=$(curl --fail-with-body -sS "$A13N_URL/api/v1/threads/$THREAD_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" | jq -r '"\"" + .id + ":" + (.version|tostring) + "\""')
jq -n --arg id "$BROWSER_ID" '{browser_id: $id, tab_access: "global", preparation: "first_use"}' | \
  curl --fail-with-body -sS -X PUT "$A13N_URL/api/v1/threads/$THREAD_ID/browser" \
    -H "Authorization: Bearer $A13N_API_KEY" -H "If-Match: $THREAD_ETAG" \
    -H 'Content-Type: application/json' --data-binary @-
```

3. Submit native-tool work with a new idempotency key. The first admitted browser call verifies and connects to the existing browser. To verify earlier, call `POST /api/v1/browsers/$BROWSER_ID/prepare`; preparation creates no browser.

```sh
jq -n --arg agent "$AGENT_ID" \
  '{agent_id: $agent, payload: {content: [{type: "text", text: "Use browser_tabs to list pages. Select the relevant tab explicitly, then use browser_observe to inspect it. Do not close existing tabs."}]}}' | \
  curl --fail-with-body -sS "$A13N_URL/api/v1/threads/$THREAD_ID/inbox" \
    -H "Authorization: Bearer $A13N_API_KEY" -H "Idempotency-Key: $(uuidgen)" \
    -H 'Content-Type: application/json' --data-binary @-
```

4. Follow the returned entry and run using [Read the result](connect-application.md#read-the-result). Run completion leaves the external process and pages alive. Wait until active borrowers have ended before releasing.
5. Detach the desired binding with JSON `null` and a fresh thread ETag if future runs should no longer borrow it. Detachment does not close pages.
6. Read a fresh browser ETag and release Service access. Release erases encrypted connection material, including an attachment that was never prepared; it leaves the external process, tabs and login state intact.

```sh
BROWSER_ETAG=$(curl --fail-with-body -sS "$A13N_URL/api/v1/browsers/$BROWSER_ID" \
  -H "Authorization: Bearer $A13N_API_KEY" | jq -r '"\"" + .id + ":" + (.version|tostring) + "\""')
curl --fail-with-body -sS -X POST "$A13N_URL/api/v1/browsers/$BROWSER_ID/release" \
  -H "Authorization: Bearer $A13N_API_KEY" -H "If-Match: $BROWSER_ETAG"
```

Network failure reports `browser_connection_failed` without declaring the instance lost. A later connection retries the same endpoint and restores exact target ownership and selection. Observe again before acting; failed or uncertain actions are never replayed. A gateway that silently replaces its browser cannot guarantee continuity. External viewer, takeover and handback return `browser_viewer_unsupported` before any control transition.

## Choose preparation and retention

| Choice                                                        | Behavior                                                                            |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `preparation: "first_use"`                                    | Default. Prepare on the first admitted browser call; external sources only connect. |
| `preparation: "run_start"`                                    | Start preparation when execution begins.                                            |
| `prepare: true` on creation, or `POST /browsers/{id}/prepare` | Prepare explicitly before agent use.                                                |
| `idle_seconds: 1800`                                          | Managed default. Release after eligible idle time. Accepts 60–604800 seconds.       |
| `idle_seconds: null`                                          | External default. Retain until explicit release or managed supplier expiry.         |

Preparation and retention are independent. Read `status`, `control`, `expires_at` and `failure` from the browser resource. `reserved` means no live allocation is ready; `creating` means preparation is unresolved; `ready` confirms the live session. Metadata reads never allocate. Concurrent preparation converges on the same allocation. After a managed create fails, preparation inspects the original identity. It retries creation only when the provider confirms the request was not dispatched. If it remains unresolved, explicitly release it for supplier-fenced cleanup, then reserve and bind a new browser.

Active runs, unsettled browser calls, human control and valid pending handback notifications prevent idle release. Supplier expiry can still end the session. A `lost` or `deleted` browser does not automatically restart. Create and bind another browser when replacement is intended.

Run completion and binding detachment leave the browser and its tabs alive. To release Service access, read the browser's current ETag and call `POST /api/v1/browsers/{id}/release` with `If-Match`. Service refuses release while active use makes it unsafe. Managed release terminates the supplier session without saving a resumable profile. External release leaves the existing browser alive. Explicit release can retire an automation-controlled identity after its borrowers and active claims end, even if an old call's outcome remains unknown. That history stays unknown; external actions may still be running. The retired identity cannot be reused. A desired binding to a lost or deleted browser blocks later runs until you explicitly detach it or bind another browser.

## Choose tab access

`thread` is the default. Native tools see exact tabs owned by that thread and their popups. `global` exposes the live inventory, including other threads' tabs. Both modes share cookies and login state; thread mode is a tool-target boundary, not account isolation.

Each thread selects a tab independently of native browser focus. If its selected tab closes, select or create another tab explicitly. Service never selects a different page merely because its URL or title matches. A worker reconnect restores exact ownership and selection, not old actionable references; observe again before acting.

## Take over and return control

For managed sources with viewer support, an integrating application implements this sequence with direct HTTP calls. Takeover, renewal, handback and viewer access are excluded from MCP discovery.

1. Read the browser's current ETag and call `POST /api/v1/browsers/{id}/takeover` with `If-Match` and `{"thread_id": "<bound-thread-id>", "lease_seconds": 300}`. The control lease starts when Service closes the admission gate, including time spent draining.
2. Read the browser until `control` becomes `human`. `draining` means Service has closed browser-tool admission and is waiting for previously admitted work to settle. Unknown completion is not treated as success after a timeout.
3. As the controlling principal, call `POST /api/v1/browsers/{id}/viewer`. Treat the returned URL as a secret. Respect its expiry and the response's `no-store` policy.
4. While the application considers the person active, send `POST /api/v1/browsers/{id}/renew` with the returned `generation`, `episode` and optional `lease_seconds`. Renew before `control_expires_at`, including while draining. No `If-Match` is needed for renewal. The same controller must retain browser and workspace `run` permission.
5. Finish the human work. Read a fresh ETag and call `POST /api/v1/browsers/{id}/handback` with `If-Match`.
6. Wait for `control: "automation"`. Service revokes viewer input, synchronizes exact tabs and invalidates old references before reopening tools.

If takeover remains `draining` before any possible viewer grant, handback can cancel that intent once its active claim ends. Cancellation suppresses pending handback notifications rather than claiming the browser is available. Unknown calls still block tools; after borrowers end, explicitly release the identity to retire it. If a viewer grant may have occurred, handback must revoke it normally.

Human control blocks every browser tool on that browser, including reads and waits. Other tools, model work and other browsers continue. A blocked tool returns immediately and registers its thread for handback; the agent should continue unrelated work or finish rather than poll.

Handback delivers an internal availability notification only to threads whose calls were actually blocked. The existing inbox and run boundaries determine when it is incorporated. Waiting approvals stay waiting, and failed or cancelled runs keep their normal explicit-continuation rules. Stopping or archiving a thread suppresses its pending notifications. Blocked or uncertain actions are never replayed automatically.

Every takeover and renewal accepts a positive integer `lease_seconds` within deployment `control.browser_control_min_seconds` and `control.browser_control_max_seconds` (defaults 60 and 3600). Omitting it on either operation uses `control.browser_control_seconds` (default 300). Requests outside the configured bounds are rejected without changing control; values are not clamped. All three settings must be positive integers with `min <= default <= max`, or startup fails. Each accepted renewal returns the actual deadline and extends it to the later of the existing deadline and database time plus the requested duration. A shorter renewal cannot shorten control. Each request is a new keepalive, not an idempotency-key replay. After a lost response, read the current browser and use bounded retries; do not retry indefinitely. An expired or returning episode cannot be revived. Take over again only after verified handback.

For example, configure the deployment policy in `service.toml` (the same policy applies to takeover and renewal):

```toml
[control]
browser_control_min_seconds = 60
browser_control_max_seconds = 3600
browser_control_seconds = 300
```

The application decides whether the person is still active. Service does not infer activity from an open viewer or browser connection. If the application stops renewing, control expires and Service starts the same safe handback as an explicit request. Provider disablement, loss of controller authority, viewer expiry or failure to maintain viewer input also starts handback. If revocation or inventory fails, tools remain blocked in `returning` until cleanup succeeds. After borrowers and active claims end, a workspace writer can instead explicitly release a managed browser using its current ETag. This suppresses availability notices and terminates the supplier session; a failed termination remains `deleting` for retry, not a successful handback. A lost browser does not generate an availability notification.

Application renewal updates PostgreSQL only. Control-role maintenance extends the provider's same viewer grant before its actual expiry. The viewer URL can expire earlier than `control_expires_at`; respect both and fetch the current viewer as needed. Providers whose input lifetime cannot cover the maintenance window cause safe handback rather than indefinite control. Provider adapters must implement `ensure_viewer(state, grant_id, *, expires_at)` with monotonic deadlines and permanent revocation fencing, including pending requests and existing input connections. Termination must also prevent a late ensure from reviving the allocation.

Requests may reach any Service API replica. PostgreSQL row locks and database time decide renewal versus expiry or handback. Control replicas claim supplier work durably, release the transaction during I/O, and recheck the claim, generation, episode, phase and deadline before publishing results. Expired claims allow recovery; they do not stop an old supplier call. The provider's same-grant monotonicity and revocation fence protect external input. No sticky session or process-local timer is required.

A blocked-call registration follows its current control episode, including prolonged draining and returning. It does not expire merely because 24 hours elapsed. Verified handback starts a finite notification lifetime (`control.browser_notice_seconds`, default 86400) in the same transaction that queues delivery. Delivery and consumption recheck current authority, binding and episode; retries do not create duplicate notifications.
