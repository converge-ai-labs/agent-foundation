---
title: Sandbox image
description: Build and run the official a13n-sandbox container image with Envd, non-root execution, optional desktop support, and Service/Harness UI integration.
---

`ghcr.io/converge-ai-labs/a13n-sandbox` is the prebuilt container image for running `a13n-envd` with Python, Node.js, Git, and a non-root `sandbox` user owning `/workspace`.

## Run locally

```sh
docker run --rm -it ghcr.io/converge-ai-labs/a13n-sandbox:dev
```

Or build from source:

```sh
make image-sandbox SANDBOX_IMAGE=a13n-sandbox:local
```

## Managed Docker desktop in Service

To run an observable desktop inside Docker, build or select the desktop image (`a13n-sandbox:local-desktop`) and configure your Service template with `execution="envd_http"`, `desktop=true`, and networking enabled.

- **Human desktop assistance:** Set `desktop_assistance: true` and route a browser-reachable `desktop_viewer_url` to container port `6081`. See [Service Computer Use](../a13n-service/computer-use.md#create-a-docker-desktop) for permissions and handback.
- **Standalone registration:** Run an HTTP daemon and [register an external target](../environments/remote-envd.md#connect-to-the-service), or connect to Harness UI via [outbound Envd](configuration.md#connect-to-harness-ui).
