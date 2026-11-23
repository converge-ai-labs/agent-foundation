---
title: Browsers
description: Provision managed Chromium browsers in Docker, Kubernetes, or cloud providers for Service agents, with live VNC inspection and persistent profiles.
---

Give an agent the `browser` toolset when a task requires JavaScript rendering, interactive web forms, or authenticated browser sessions.

## Quick start

1. Add a Browser provider under **Workspace settings → Providers → Browser** (Docker, Kubernetes, or a cloud browser provider).
2. Create a Browser template and optionally attach it as the agent's default browser.
3. On the agent revision, enable the `browser` toolset and set its [tool permissions](agent-configuration.md#tool-permissions).
4. Start a conversation and ask the agent to open a URL and capture a screenshot or extract page content.

## Kubernetes

To run managed Chromium browsers as Kubernetes Pods:

1. [Enable Kubernetes Browsers in Helm](configuration.md#kubernetes-browsers) (`kubernetesBrowsers.enabled=true`).
2. In Console, open **Providers → Browser providers**, create a Kubernetes browser provider using the cluster profile and namespace printed by Helm, and verify the connection.
3. Keep CDP and VNC endpoints private inside the cluster and restrict browser egress via Kubernetes `NetworkPolicy`.

## Profiles and live view

- **Persistent profiles:** Bind a storage claim or profile volume to preserve cookies and site state across sessions.
- **Live view:** Open the browser panel in Console to watch or take over an active browser session.
