---
title: Browsers
description: Attach managed cloud or containerized Chromium browsers to Service agents, stream live browser views in Console, and support human takeover for logins.
---

Give an agent a `browser` when a task needs website navigation, JavaScript rendering, form filling, screenshots, or human login takeover. Browsers are managed independently of [environments](environments.md).

## Choose a browser backend

| Option                                                           | Stop/start keeping profile | Console live viewer & takeover | File upload/download  |
| ---------------------------------------------------------------- | -------------------------- | ------------------------------ | --------------------- |
| **Kubernetes** (`ghcr.io/converge-ai-labs/a13n-browser`)         | Yes (PVC retained on stop) | Yes                            | No                    |
| **Docker** / **Native** (Linux Service host)                     | Yes                        | Yes                            | Yes (`browser_files`) |
| **Cloud** (`browserbase`, `kernel`, `browser_use`, `cloudflare`) | No (ephemeral session)     | No                             | No                    |
| **External CDP** (`ws://` or `wss://` browser debugger endpoint) | Managed externally         | No                             | No                    |

## Quick start in Console

1. On the agent revision, enable the `browser` toolset and set its [tool permissions](agent-configuration.md#tool-permissions).
2. Add a provider under **Providers → Browser providers** (for cloud, Docker, Kubernetes, or Native), then create a browser under **Browsers**—or attach an External CDP URL directly under **Browsers**.
3. Open the thread's **Browser** panel, select the browser, choose tab scope (`thread` for this thread's tabs, `global` for existing/shared tabs), and save.
4. For Kubernetes, Docker, or Native browsers, click **Take control** anytime to log in or solve steps manually in the live viewer, then click **Return control** (**Hand back to agent**).

## Configure self-hosted and cloud providers

### Kubernetes

[Enable Kubernetes Browsers in Helm](configuration.md#kubernetes-browsers), then create a Kubernetes Browser provider in Console (`cluster: "in-cluster"`, using the namespace printed by Helm). Keep CDP and VNC private inside the cluster and restrict egress via `NetworkPolicy`.

### Docker and Native

Configure `service.toml` on a Linux host where `control` and `worker` share the browser root and Docker Engine (use host networking if Service runs in a container):

```toml
[browsers]
root = "/var/lib/a13n/browsers"
docker_host = "unix:///var/run/docker.sock"
```

Create a provider via `POST /api/v1/browser-providers` (`type: "docker"`, `browserbase`, `kernel`, `browser_use`, or `cloudflare` with its `config` and `credential.api_key`).

## Reserve, attach, and bind via API

1. **Create a managed browser:** `POST /api/v1/browsers` with `{"provider_id": "...", "name": "Ops browser", "recipe": {}}` (or attach an existing browser-level `ws(s)://` debugger via `POST /api/v1/browsers/attach` with `{"name": "...", "cdp_url": "...", "headers": {}}`).
2. **Bind to a thread:** Pass `browser_binding` on `POST /api/v1/threads` or `PUT /api/v1/threads/{thread_id}/browser` (with thread `If-Match`; pass `null` to detach):

```json
{
  "browser_id": "browser_0123456789abcdef01234567",
  "tab_access": "thread",
  "preparation": "first_use"
}
```

- **Lifecycle (`If-Match` required):** `POST /api/v1/browsers/{id}/prepare`, `/stop` (pauses self-hosted runtime while keeping profile), and `/release` (destroys managed browser/profile or forgets external CDP). Set `stop_after_seconds` / `delete_after_seconds` (self-hosted) or `idle_seconds` (cloud, default `1800`).
- **Programmatic takeover:** `POST /api/v1/browsers/{id}/takeover` → `/viewer` → `/renew` → `/handback` (lease bounds configured under `[control] browser_control_*` in `service.toml`).
