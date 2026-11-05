---
title: Work with a desktop
description: Prepare a desktop environment, observe it, and hand control between people and agents.
---

Start with an [environment provider and template](environments.md), then prepare a desktop target supported by your deployment. Enable Computer tools only after verifying observation and the required permissions. This page covers agent interaction and human takeover; browser-only automation uses the separate [browser guide](browsers.md).

## Computer Use

A desktop task needs three things: a compatible graphical environment, the agent's enabled **Computer** toolset, and Computer permissions on the thread mount. Prepare the desktop first, then enable observation or interaction.

### Create a Docker desktop

1. Configure the Docker provider's **Environment operation channel** and **Host access address**. Workers must reach that address and the Docker-assigned port; Engine access is a separate connection.
2. Create a Docker template and choose **Desktop environment**. The preset selects the desktop image, HTTP execution and desktop readiness requirement.
3. Select the template as the agent's default environment, or create an instance and add it to a conversation.
4. Once it is running, choose **Check desktop** in **Environments and memory**, then grant permissions below.

Use loopback or an explicitly trusted private HTTP route with the matching outbound policy. Other hostnames need an explicit listen IP. For a custom graphical image compatible with Envd HTTP, set `execution: "envd_http"` and `desktop: true`. Human desktop control also needs a browser-reachable viewer route, described below.

### Automatic Vercel desktops

1. Add a Vercel provider with an access token. Choose **Load Vercel teams**, then select a team and project accessible to that token.
2. Create a template with **Desktop environment → Automatic desktop**.
3. Create an instance. The first build can take several minutes; Console shows its phase and elapsed time. Return to **Environment instances** to check progress after leaving the page.
4. When ready, inspect the desktop preview and enable the agent's permissions.

The token needs sandbox and snapshot creation, read and deletion permissions, with a lifetime suitable for unattended use. Subsequent creations reuse a validated snapshot within the same workspace, provider, credential version, team, project and build revision. Temporary builders are removed; the reusable snapshot stays in your Vercel project.

### Enable observation and interaction

1. Enable the agent's **Computer** toolset and save. Per-tool approvals still apply.
2. In **Environments and memory**, open the mount's **Computer permissions**. Allow observation for screenshots, or interaction for mouse and keyboard actions.
3. Send a new message asking the agent to describe the screen. Expand its work and choose **View screenshot** on the observation step.
4. For an interaction task, inspect the next screenshot to confirm the application changed as intended.

Computer permissions default off. Changes apply to newly accepted runs, including for `workspace`. Desktop readiness is separate: **Check desktop** probes a running instance without capturing pixels, sending input, granting permissions or starting a stopped instance. A saved step screenshot is historical; use desktop control for a live viewer.

API clients update a mount with `PATCH /api/v1/threads/{thread_id}/environments/{name}` and the thread's ETag. This body permits only Computer observation:

```json
{"permission_ceiling":{"operations":["environment.computer.describe","environment.computer.observe","environment.computer.zoom"]}}
```

The action list replaces the entire permission set. Include file and execution actions you want to retain; Console's presets preserve them. Computer permissions govern Computer tools, while shell and other tools keep their separate authority.

### Computer coordinate mode

If the model returns relative positions, add this agent configuration fragment:

```json
{"toolsets":{"computer":{"enabled":true,"config":{"coordinate_space":"normalized_1000"}}}}
```

Pass positions from 0 to 1000 directly. Tools convert them to screenshot pixels, including both drag endpoints. Models that return pixels use the default `pixels` mode. Scroll distances and keyboard input are unchanged. Verify the application result after each action.

### Desktop access prerequisites

Enable `desktop: true` and `desktop_assistance: true` in the recipe. This prepares human viewing and Computer control, not an agent tool. Service has no built-in desktop-assistance request tool; an application can supply its own request workflow. Configure the agent's Computer tools and mount permissions separately. Prepare a compatible desktop-access v2 endpoint using the [provider guide](../environments/providers.md).

For Docker, configure `desktop_viewer_url`, such as `wss://desktop.example/{container_id}/websockify`, routed to each container's port 6081. Engine and Envd access do not provide this browser route. Plain WS is accepted only on loopback. E2B needs an automatically prepared or compatible custom desktop template; a stock `desktop` template alone is insufficient. Other providers use their prepared supervisor and provider-specific routes. Opening the viewer does not start or extend the environment.

### Get and return desktop control

1. Select the **Desktop** icon beside **Browser** in an interactive conversation. The right-side panel automatically opens a read-only view of the selected environment mount.
2. Choose **Take control** to reserve Computer input. Service blocks subsequent Computer calls and connects input once authorized. Already admitted calls may finish, including sending input.
3. Use the desktop while files, Shell, processes, scripts, other desktops and the agent's model work continue.
4. Choose **Renew control** when more time is needed. The default lease is five minutes; operators configure the default and request bounds through `control.desktop_control_seconds`, `control.desktop_control_min_seconds` and `control.desktop_control_max_seconds`.
5. Choose **Return control** when finished. Service revokes input and confirms held-key/button cleanup before reopening the Computer channel.

Closing the viewer or page disconnects that viewer and leaves control unchanged until explicit handback or lease expiry. Short connection grants refresh separately from the human lease. One principal controls a native desktop at a time, including when multiple environment aliases or threads share it. Read-only viewers remain independent. Browser control has its own lifecycle.

Each normal model request includes the current Computer admission state for known native desktop mounts, separately from permissions and readiness. An agent that sees human control can call `computer_notify(alias="workspace")` to register for handback without attempting a blocked action. A Computer call blocked during takeover also registers the same notice automatically. Registrations deduplicate per thread and takeover; registration after handback returns `already_available` and does not subscribe to a future takeover. The agent can continue other work or finish its turn without polling. When that takeover ends, including cancellation before input was granted, the existing inbox delivers one notice to eligible callers. A later takeover does not erase that event. The notice asks the agent to check current Computer admission; it does not promise that the desktop is still available or register for a later takeover. The agent must observe the current desktop and make a new model decision before sending input; old gestures are never replayed. A settled child is not restarted. Pending questions and approvals still require their ordinary complete response batch.

Unconfirmed human-viewer revocation or held-input cleanup keeps Computer access blocked for reconciliation. Failed or abandoned agent Computer calls do not delay takeover. This control boundary covers managed Computer calls, including calls inside CodeAct. It does not freeze arbitrary guest processes, Shell-launched UI automation or independent clients.

### Upgrade desktop control

[Follow the coordinated desktop upgrade](upgrade-notes.md#desktop-control-cutover).

### Automatic Sprites desktops

1. Add a Sprites provider with its organization and a token permitting ownership labels, execution, deletion and URL settings.
2. Create a template with **Desktop environment** enabled.
3. Create an instance. Each new Sprite installs Envd, desktop dependencies and Chrome. Console reports progress; return later to inspect it or retry a confirmed failure.
4. When ready, choose **View desktop** for a snapshot. Use a conversation's desktop control for interactive access.

An existing Sprite retains files and installation across sleep; a new Sprite installs again. Failed installation retains the owned target for explicit retry or deletion. Sprites has no explicit stop. Browser access uses short-lived grants and VNC authentication; the cloud API token stays in Service.
