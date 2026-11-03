---
title: Browsers
description: Choose a browser, connect it to an agent, and manage its lifetime and human control.
---

Give an agent a browser to navigate websites, read pages, fill forms and take screenshots. A browser can stay available across runs and be shared by several threads. You manage it separately from the agent's [environments](environments.md).

## Choose a browser

Use **Docker** for self-hosting, **Browserbase, Kernel, or Browser Use Cloud** to run browsers in the cloud, or **external CDP** to connect a browser you already operate. **Native** is for debugging on the Service host.

All six options support the same core agent browser tools. The table shows the additional capabilities integrated into Service.

| Option            | Browser runs on                 | Stop/start, keeping profile | Console viewer and takeover | Upload/download |
| ----------------- | ------------------------------- | --------------------------- | --------------------------- | --------------- |
| Docker            | Your Linux host, in a container | Yes                         | Yes                         | Yes             |
| Native            | Your Linux Service host         | Yes                         | Yes                         | Yes             |
| Browserbase       | Browserbase cloud               | No                          | No                          | No              |
| Kernel            | Kernel cloud                    | No                          | No                          | No              |
| Browser Use Cloud | Browser Use cloud               | No                          | No                          | No              |
| External CDP      | An existing Chromium instance   | Managed externally          | No                          | No              |

Docker and Native keep cookies and login state when stopped. The cloud integrations use temporary profiles that end with the supplier session. External CDP leaves browser lifetime and profile management to you.

## Use Console

1. **Enable browser tools.** In the agent revision, enable the `browser` toolset and choose the [tool permissions](agents-and-runs.md#tool-permissions) the agent needs.
2. **Add a browser.** For a managed browser, configure an account under **Providers → Browser providers**, then create a browser under **Browsers**. For external CDP, attach the endpoint directly under **Browsers**; no provider account is needed.
3. **Bind the thread.** Open the thread's **Browser** action, select the browser and save. Keep `thread` tab access for tabs created by that thread; use `global` when the agent needs existing tabs.
4. **Submit work.** The default `first_use` setting prepares the browser on the first browser-tool call. Ask the agent to open or select a tab, observe the page, then perform the task.

For Docker and Native, you can also choose **Take control**, wait for human control, and open the viewer to work in the browser yourself. Choose **Return control** when finished. Closing the viewer alone does not return control.

A completed run leaves the browser available for reuse, subject to its retention policy and supplier timeout. Use **stop** to pause Docker or Native while keeping its profile; use **release** when you are finished with the browser.

## Configuration and API details

The following sections cover provider setup, direct HTTP integration and lifecycle options. If you already have a working browser in Console, consult only the section you need.

### Configure cloud browsers

Browserbase, Kernel, and Browser Use Cloud run the browser for you; none requires Docker or Chromium on the Service host. Create an account through Console or `POST /api/v1/browser-providers`. Store the API key in `credential`, and allow the supplier API and secure browser endpoint through Service's outbound policy.

| Account field            | Browserbase                       | Kernel                                                               |
| ------------------------ | --------------------------------- | -------------------------------------------------------------------- |
| `type`                   | `browserbase`                     | `kernel`                                                             |
| `config`                 | `{"project_id": "..."}`           | `{"organization_id": "...", "project_id": null}`                     |
| `credential`             | `{"api_key": "..."}`              | `{"api_key": "..."}`                                                 |
| Browser `recipe` timeout | `timeout`, default 300 seconds    | `timeout_seconds`, default 300 seconds                               |
| Timeout meaning          | Maximum supplier session lifetime | Standby timeout; a connected client or viewer can extend the session |

For a project-scoped Kernel key, replace `project_id: null` with its effective project ID. Service verifies the key's organization and project before using the session. Kernel's recipe also accepts `headless`, which defaults to `true`.

For Browser Use Cloud, select type `browser_use`, set `config` to `{"project_id": "..."}` and `credential` to `{"api_key": "..."}`. Its recipe accepts `timeout_minutes` (1–240, default 5); this is a supplier session timeout. Service verifies the key's project before lifecycle operations.

The supplier timeout and Service's [idle retention](#choose-preparation-and-retention) are separate. Setting Service retention to a longer duration does not extend the supplier session. Keep API keys and browser connection URLs out of agent messages.

### Configure self-hosted browsers

#### Docker

Run Service and a local Docker Engine on the same Linux host. Set a persistent, private directory that the Service account can write:

```toml
[browsers]
root = "/var/lib/a13n/browsers"
docker_host = "unix:///var/run/docker.sock"
```

Create a provider with `type: "docker"` and an empty `config`. The defaults use `ghcr.io/converge-ai-labs/a13n-browser:latest` with `pull_policy: "always"`. An empty browser `recipe` uses the default desktop size; set `width` and `height` to change it.

To build and use a local image:

```sh
make image-browser BROWSER_IMAGE=a13n-browser:local
```

```json
{
  "image": "a13n-browser:local",
  "pull_policy": "never"
}
```

Use that JSON as the provider's `config`. The pull policies are `always`, `if_missing` and `never`. `always` pulls before creating a new container and fails if the pull fails. Restarting a retained browser reuses its original container and image. Its profile lives in that container: do not prune containers you intend to keep.

#### Native

Install Chromium, `Xvfb`, `openbox`, `xauth`, `x11vnc` and `websockify` on the Linux host, with the supporting executables on PATH. Enable Native explicitly:

```toml
[browsers]
root = "/var/lib/a13n/browsers"
native_enabled = true
native_chromium = "/usr/bin/chromium"
```

Create a provider with `type: "native"` and an empty `config`. Native runs as the Service account and provides no tenant isolation. A Service exit can end the native browser; a later authorized start reuses its profile after the previous process has stopped. Docker browsers survive a Service-only exit.

#### Deployment and network

- Keep all control and worker processes that manage self-hosted browsers on the same host, using the same browser directory and Docker Engine. Remote Engines and multi-host scheduling are unsupported.
- For containerized Service with Docker browsers, mount the Engine socket and browser directory and use host networking. Browser ports are published on the Engine host's loopback interface; a bridge-network Service container cannot reach them through its own loopback.
- Keep CDP and viewer ports private. Docker Engine access is privileged, and containers on the browser's Docker network can reach its CDP endpoint. Only trusted processes should have those forms of access.
- Forward WebSocket upgrades through your reverse proxy. Console connects through Service's authenticated relay and uses `wss` under HTTPS.
- Apply browser outbound restrictions at the deployment network boundary, including restrictions on internal services and metadata endpoints. Service's provider endpoint policy does not restrict browser page navigation.

### Reserve and bind a browser

Use [authenticated HTTP requests](http.md) within one workspace. Creating, binding and using a browser require workspace `run` permission and access to the provider account. Stopping or releasing a browser requires workspace `write`.

1. Read `GET /api/v1/provider-types/browser` for available types and schemas, then create an account through `POST /api/v1/browser-providers`.
2. Send `POST /api/v1/browsers` with the account's `provider_id`, a `name` and a `recipe`. The default reserves a browser ID; allocation waits until preparation.
3. Enable the agent revision's `browser` toolset.
4. Read the thread's current ETag, then send the following body to `PUT /api/v1/threads/{thread_id}/browser` with that ETag in `If-Match`. Replace `brw_example` with the returned browser ID.

```json
{
  "browser_id": "brw_example",
  "tab_access": "thread",
  "preparation": "first_use"
}
```

Submit the run after binding. Binding changes affect future runs; accepted runs keep their captured binding. Child and forked threads need their own explicit binding.

Read the desired binding with `GET /api/v1/threads/{thread_id}/browser`. To detach it, send JSON `null` to the PUT endpoint with a fresh thread ETag. Detachment does not release the browser or close its tabs.

### Attach an existing browser

External CDP connects Service to a stable Chromium instance that you operate. Supply a direct **browser-level** `ws://` or `wss://` debugger endpoint reachable from the Service worker. HTTP discovery URLs and page-level sockets are unsupported.

Using the [HTTP authentication variables](http.md#authentication), put the secret endpoint in `CDP_URL` and an optional authorization value in `CDP_AUTHORIZATION`, then attach it:

```sh
jq -n --arg url "$CDP_URL" --arg auth "${CDP_AUTHORIZATION:-}" \
  '{name: "Existing browser", cdp_url: $url, headers: (if $auth == "" then {} else {Authorization: $auth} end)}' | \
  curl --fail-with-body -sS "$A13N_URL/api/v1/browsers/attach" \
    -H "Authorization: Bearer $A13N_API_KEY" \
    -H 'Content-Type: application/json' --data-binary @-
```

[Bind the returned browser ID](#reserve-and-bind-a-browser), using `tab_access: "global"` if the agent needs pre-existing pages. The first browser call connects to the endpoint. To test the connection earlier, call `POST /api/v1/browsers/{id}/prepare`; this creates no new browser.

Service encrypts the endpoint and headers and omits them from ordinary reads. To change the endpoint, create another attachment and rebind. Releasing the attachment removes Service access and stored connection secrets; the external process, tabs and login state remain intact.

Destination checks apply at attachment and on each connection, including run `allowed_hosts` restrictions. Plain WS requires an explicit allowed HTTP origin in `providers.http_origins`; redirects are refused. Remember that loopback addresses refer to the worker's network environment.

### Choose preparation and retention

Preparation decides **when the browser becomes ready**. Retention decides **what happens when it is idle**.

#### Preparation

The binding defaults to `preparation: "first_use"`. Choose `run_start` to begin preparation when execution starts. To prepare explicitly, set `prepare: true` on creation or call `POST /api/v1/browsers/{id}/prepare`.

Reading browser metadata never starts a browser. `reserved` means the browser is not yet prepared, `creating` means preparation is in progress or unresolved, and `ready` means a live browser is available.

#### Retention

| Source                                   | Default when idle                                      | Profile after that action         |
| ---------------------------------------- | ------------------------------------------------------ | --------------------------------- |
| Docker / Native                          | Stop after 1800 seconds; retain until explicit release | Kept                              |
| Browserbase / Kernel / Browser Use Cloud | Release after 1800 seconds                             | Removed with the supplier session |
| External CDP                             | Keep Service access until explicit release             | Managed externally                |

For Docker and Native, set `stop_after_seconds` and `delete_after_seconds` independently. Each accepts 60–604800 seconds or `null` and measures time since last authorized use. Stopping does not reset the deletion clock; when both actions are due, deletion wins.

`idle_seconds` selects terminal idle release instead. It defaults to 1800 for cloud browsers and `null` for external CDP. Do not combine it with the stop/delete settings. Supplier expiry can still end a cloud session while Service considers it in use.

Accepted, running or waiting runs that use the browser, unsettled calls, human control and pending handback obligations prevent automatic stop or idle release.

#### Stop and release

Call `POST /api/v1/browsers/{id}/stop` or `POST /api/v1/browsers/{id}/release` with the current browser ETag in `If-Match`.

- **Stop** keeps the Docker or Native browser's ID, binding and profile. The next authorized use or `prepare` starts it again. A runtime restart advances its generation and clears old references and tab ownership; restored tabs are not reassigned by URL or title.
- **Release** is terminal. It removes a managed browser's runtime and profile, or disconnects Service from an external browser. Service refuses release while active use makes it unsafe.

A `lost` or `deleted` browser cannot be restarted. Detach it or bind a replacement before submitting more work to the thread.

### Choose tab access

Use `thread` for tabs created by that thread and their popups. Use `global` for the browser's full tab inventory, including pre-existing pages and other threads' tabs. Both modes share cookies and login state; use separate browsers when you need separate accounts.

Each thread selects its own tab. If that tab closes, the agent must select or create another one. Reconnecting to the same live browser restores exact tab ownership and selection, but the agent must observe again to obtain fresh references.

### Transfer files

Docker and Native support `browser_files`. Uploads use `asset:<asset_id>` values in `paths`, referring to workspace assets the run is permitted to read. Host paths and mounted environment paths are not browser upload paths.

Completed tracked downloads are published as workspace assets under the run's `write` authority. Repeated inspection returns the same publication. The browser's filesystem remains separate from mounted environments.

### Take over and return control

For Docker and Native, Console handles the takeover flow. Integrating applications can use these HTTP calls; they are excluded from MCP discovery.

1. Call `POST /api/v1/browsers/{id}/takeover` with a current browser `If-Match` and `{"thread_id": "<bound-thread-id>", "lease_seconds": 300}`.
2. Wait for `control: "human"`. During `draining`, Service blocks new browser calls and waits for admitted work to finish.
3. As the controller, call `POST /api/v1/browsers/{id}/viewer` and open the returned viewer.
4. Renew before `control_expires_at` by sending the current `generation`, `episode` and optional `lease_seconds` to `POST /api/v1/browsers/{id}/renew`. Renewal needs no `If-Match` and is also available during draining.
5. When finished, call `POST /api/v1/browsers/{id}/handback` with a fresh browser `If-Match`. Wait for `control: "automation"` before using browser tools again.

Human control blocks all agent browser tools on that browser, including reads and screenshots. Other tools and browsers remain available. Verified handback notifies threads whose calls were blocked; it does not replay their actions or automatically resume failed, cancelled or approval-waiting work. Stopping or archiving a thread suppresses its pending notification.

#### Control lease

The default lease is 300 seconds, with permitted values from 60 to 3600 seconds. The deployment can change those bounds in `service.toml`:

```toml
[control]
browser_control_min_seconds = 60
browser_control_max_seconds = 3600
browser_control_seconds = 300
```

All three values must be positive integers with `min <= default <= max`. The same policy applies to takeover and renewal. Omitted `lease_seconds` uses the configured default; out-of-range requests are rejected.

The lease starts when takeover blocks new browser calls, including time spent draining. Renewal never shortens the deadline, and only the current authorized controller can renew. An open viewer does not renew control by itself: the application decides when to send renewals. Expiry starts handback. An expired or returning episode must finish handback before a new takeover.

#### Viewer integration

Treat viewer URLs, passwords and tokens as secrets and honor `Cache-Control: no-store`. A viewer can expire before the control lease; respect both deadlines and request a fresh viewer when needed.

For `protocol: "vnc"`, use the returned same-origin WebSocket URL and password, offering the `binary` and `a13n.browser.<token>` subprotocols. For `protocol: "iframe"`, use the returned URL. The self-hosted viewer enforces expiry and revocation even while Service is offline.

### Troubleshooting

Read the browser's `status`, `control`, `expires_at` and `failure` before choosing a recovery action.

- **Cloud creation has an unknown outcome.** Service retries creation only after confirming that the earlier request was not sent or was definitively rejected without creating a session. Otherwise, inspect the supplier account before creating a replacement: the original session may still be running and incurring charges. Kernel cannot automatically find or release it without the returned session ID. Browserbase and Browser Use Cloud can search exact allocation metadata, but no match leaves cleanup unresolved.
- **An external connection fails.** `browser_connection_failed` does not by itself mark the browser lost. Reconnect to the same instance and observe again. A gateway that silently substitutes another browser cannot preserve continuity. Failed or uncertain actions are never replayed automatically.
- **Takeover stays in `draining`.** Previously admitted work has not settled. A timeout does not prove completion. Handback can cancel a takeover before any possible viewer grant once active work permits it; this cancellation does not announce browser availability or clear unknown calls.
- **Handback stays in `returning`.** Viewer revocation or tab synchronization has not completed, so browser tools remain blocked. Provider disablement, lost controller authority or viewer expiry can also trigger handback. After runs and active operations finish, a workspace writer may release the managed browser instead. Failed termination remains `deleting` and can be retried.
- **An action's outcome remains unknown.** After runs and active operations finish, release can retire an automation-controlled browser. This does not resolve the old action's outcome or undo external effects. The retired browser ID cannot be reused.

Lifecycle operations have a cancellation budget of 120 seconds by default, set by `browsers.operation_seconds`. Cleanup and blocking provider I/O can continue beyond that budget, so it is not a hard response deadline.
