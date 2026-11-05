---
title: Embed MCP Apps
description: Render interactive MCP tool results in your application with current authorization and isolation.
---

Use this page if you are building an application that renders interactive results from a remote MCP connection. You need an authenticated Service client, a compatible MCP server and an isolated iframe bridge. First [configure and test the connection](tools.md#connections), then open the saved App without repeating its original tool call.

## MCP Apps for embedding applications

An embedding application can render an MCP App from a tool result. Service saves the original arguments, declaration and full result, including `_meta`; it fetches HTML when the application opens the App. Console does not render Apps.

For live output:

1. Subscribe to `GET /api/v1/sessions/{session_id}/stream`.
2. Treat a delta containing `a13n.mcp_apps` as a loading reference. Even `unavailable: false` is provisional here.
3. Wait for `app_ready`, which confirms that the original data and reference are committed. Identify each App by `(run_id, app_id)`; readiness events can repeat after reconnect.
4. Open the App using the event's `run_id` and `apps[].app_id`.

The event follows committed `items` after a checkpoint containing the App; the run need not have finished. Failed publication produces an unavailable reference and no ready event. Available committed references loaded from `GET /api/v1/runs/{run_id}/items` can be opened directly, including after a disconnected run has ended. The thread stream does not emit `app_ready`.

| Request                                                   | Body                                  | Result                                          |
| --------------------------------------------------------- | ------------------------------------- | ----------------------------------------------- |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/open`           | None                                  | Saved original data and current HTML            |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/tools/call`     | `{"name":"increment","arguments":{}}` | Raw MCP result under `result`                   |
| `POST /api/v1/runs/{run_id}/apps/{app_id}/resources/read` | `{"uri":"ui://counter/app.html"}`     | Current HTML and metadata for the saved App URI |

Each request uses a fresh MCP client and current access checks. Opening never repeats the original tool; servers requiring the worker's original MCP session are unsupported. Button calls must satisfy the original agent's tool selection and permission, the current connection's selection, and App visibility. Operations requiring approval or model review are refused. Deployments with an admission policy also need explicit App admission support.

Create the iframe, enforce resource CSP and isolation, and supply the original arguments and result through your MCP App bridge. Your application owns view state and submits context or messages through the existing run API. Follow-up tool results stay with the caller; they do not replace the original or enter model history. Calls are not retried automatically: inspect external state before retrying `outcome_unknown`. HTML is fetched afresh and may differ from the version that produced an old result. When upgrading to checkpoint format 4, upgrade control processes and readers before starting new workers.
