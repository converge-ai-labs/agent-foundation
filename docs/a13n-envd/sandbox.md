---
title: Sandbox image
description: Run the official a13n-envd container images (standard, slim, desktop, and VM variants) with persistent workspace volumes and non-root execution.
---

`ghcr.io/converge-ai-labs/a13n-sandbox` packages `a13n-envd` in three container variants (`X.Y.Z` / `latest`, `-slim`, and `-desktop`; `dev*` for main builds):

| Variant    | Contents                                                                                  | Typical use                                                |
| ---------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `standard` | Envd, Python 3.13, uv, Node 24, pnpm, Git, C/C++ tools, Chromium, `agent-browser`, skills | Default coding & browser sandbox (`sandbox` 1000:1000)     |
| `desktop`  | Standard toolchain + supervised Xvfb & Openbox                                            | GUI Computer Use (`execution="envd_http"`, `desktop=true`) |
| `slim`     | Envd, minimal shell/utilities, CA certs, tini (no Python, Node, sudo, or browser)         | Lightweight file/command daemon or custom base image       |

Build and smoke-check locally from the repository root with `make image-sandbox` (`-slim`, `-desktop`) and `make image-check-sandbox`.

## Run standalone or with Harness UI

- **Host-owned stdio (keep `-i`, omit `-t`):**

```bash
docker volume create agent-workspace
docker run --rm -i \
  --mount type=volume,source=agent-workspace,target=/workspace \
  ghcr.io/converge-ai-labs/a13n-sandbox:latest
```

- **Outbound connection to Harness UI:**

```bash
docker volume create agent-envd-state
docker volume create agent-workspace
docker run --rm --name agent-sandbox \
  --mount type=volume,source=agent-envd-state,target=/var/lib/a13n-envd \
  --mount type=volume,source=agent-workspace,target=/workspace \
  ghcr.io/converge-ai-labs/a13n-sandbox:latest \
  a13n-envd connect https://host.example.com --host work
```

See [Connect to Harness UI](configuration.md#connect-to-harness-ui) for pairing, or [register an external HTTP target](../environments/remote-envd.md#connect-to-the-service) for Service.

## Managed Docker desktop in Service

Build `a13n-sandbox:local-desktop` or use a published `-desktop` tag with `execution="envd_http"` and `desktop=true` (see [Service Computer Use](../a13n-service/computer-use.md#create-a-docker-desktop)).

- The image writes a private bearer token at `/var/lib/a13n-envd/http-token` (`0600`) and supervises Xvfb, Openbox, and Envd on container port `8888`.
- For human live takeover (`desktop_assistance: true`), route a browser-reachable `desktop_viewer_url` to container port `6081` (see [desktop control](../a13n-service/computer-use.md#desktop-access-prerequisites)).

## Defaults, skills, and overrides

- **Identity & sudo:** Launches as root under `tini` and executes commands/files as `sandbox` (`1000:1000`, `/workspace`). Standard and desktop allow passwordless `sudo` inside the container; pass `-e A13N_ENVD_ALLOW_SUDO=false` to block privilege escalation (`no_new_privs`).
- **Workspace skills:** On startup, `a13n-sandbox-init` seeds missing bundled skills into `/workspace/.agents/skills` (for native Docker Provider recipes that replace the entrypoint, set `init_script: a13n-sandbox-init`).
- **Controlled egress:** Prepare a Linux runtime with the [required namespace facilities](egress.md), set `A13N_ENVD_EGRESS_MODE=controlled`, and supply an `egress` policy in `session.open`.
