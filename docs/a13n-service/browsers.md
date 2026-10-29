---
title: Managed browsers
description: Bind a shared browser to a thread, prepare it on demand, and return human control safely.
---

A managed browser keeps a live browser session across runs. Several threads can use the same browser and login state. Service manages allocation, tab ownership and human control independently of [environments](environments.md).

> [!IMPORTANT]
> This release supplies the lifecycle API and native agent tools. The built-in distribution has no live browser supplier or Console takeover interface. A deployment must register a compatible browser provider, and an integrating application must implement the control interface. Inspect `GET /api/v1/provider-types/browser` before configuring a browser.

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

## Choose preparation and retention

| Choice                                                        | Behavior                                                                                        |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `preparation: "first_use"`                                    | Default. Allocate on the first admitted browser call. Unused tools cost no supplier allocation. |
| `preparation: "run_start"`                                    | Start preparation when execution begins.                                                        |
| `prepare: true` on creation, or `POST /browsers/{id}/prepare` | Prepare explicitly before agent use.                                                            |
| `idle_seconds: 1800`                                          | Default. Release after eligible idle time. Accepts 60–604800 seconds.                           |
| `idle_seconds: null`                                          | Retain until explicit release or supplier expiry.                                               |

Preparation and retention are independent. Read `status`, `control`, `expires_at` and `failure` from the browser resource. `reserved` means no live allocation is ready; `creating` means preparation is unresolved; `ready` confirms the live session. Metadata reads never allocate. Concurrent preparation converges on the same allocation.

Active runs, unsettled browser calls, human control and valid pending handback notifications prevent idle release. Supplier expiry can still end the session. A `lost` or `deleted` browser does not automatically restart. Create and bind another browser when replacement is intended.

Run completion and binding detachment leave the browser and its tabs alive. To terminate the session, read the browser's current ETag and call `POST /api/v1/browsers/{id}/release` with `If-Match`. Service refuses release while active use makes it unsafe. Release does not save a resumable profile or preserve login state for a replacement.

## Choose tab access

`thread` is the default. Native tools see exact tabs owned by that thread and their popups. `global` exposes the live inventory, including other threads' tabs. Both modes share cookies and login state; thread mode is a tool-target boundary, not account isolation.

Each thread selects a tab independently of native browser focus. If its selected tab closes, select or create another tab explicitly. Service never selects a different page merely because its URL or title matches. A worker reconnect restores exact ownership and selection, not old actionable references; observe again before acting.

## Take over and return control

An integrating application implements this sequence with direct HTTP calls. Takeover, handback and viewer access are excluded from MCP discovery.

1. Read the browser's current ETag and call `POST /api/v1/browsers/{id}/takeover` with `If-Match` and `{"thread_id": "<bound-thread-id>"}`.
2. Read the browser until `control` becomes `human`. `draining` means Service has closed browser-tool admission and is waiting for previously admitted work to settle. Unknown completion is not treated as success after a timeout.
3. As the controlling principal, call `POST /api/v1/browsers/{id}/viewer`. Treat the returned URL as a secret. Respect its expiry and the response's `no-store` policy.
4. Finish the human work. Read a fresh ETag and call `POST /api/v1/browsers/{id}/handback` with `If-Match`.
5. Wait for `control: "automation"`. Service revokes viewer input, synchronizes exact tabs and invalidates old references before reopening tools.

Human control blocks every browser tool on that browser, including reads and waits. Other tools, model work and other browsers continue. A blocked tool returns immediately and registers its thread for handback; the agent should continue unrelated work or finish rather than poll.

Handback delivers an internal availability notification only to threads whose calls were actually blocked. The existing inbox and run boundaries determine when it is incorporated. Waiting approvals stay waiting, and failed or cancelled runs keep their normal explicit-continuation rules. Stopping or archiving a thread suppresses its pending notifications. Blocked or uncertain actions are never replayed automatically.

Viewer expiry or provider disablement revokes viewer access but does not implicitly return control to automation. Request handback explicitly. If revocation fails, browser tools remain blocked until cleanup succeeds.
