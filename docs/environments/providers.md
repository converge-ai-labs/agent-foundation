---
title: Providers and runtime configuration
sidebarTitle: Providers and runtime
description: Prepare a backend, connect executions to it, and keep target management separate from runtime cleanup.
---

Configure a Provider in three parts: a recipe describes the target, account settings and credentials reach the backend, and a runtime holds shared clients. Start with [Choose a backend](index.md#choose-a-backend), then follow the matching setup below. Use [Provider plugins](provider-plugins.md) to add your own adapter.

## Built-in Providers

Choose a backend and Native or Envd execution in the [backend selection table](index.md#choose-a-backend). Creation, stop, resume, and deletion belong to target management. This page covers Host-owned runtime clients; use [Cloud Providers](cloud-providers.md) for cloud accounts, desktop preparation, and backend limits, or [Provider plugins](provider-plugins.md) for catalog extensions.

Keep shared Device runtimes open until their borrowers finish. Each Envd execution opens an independent fixed-cwd Session. Sessions own execution resources; the Host still supplies tenant authorization and container allocation. Before deploying Sandbox or controlled egress, check the [current prerequisites and verify the image](../a13n-envd/egress.md#verify-a-sandbox-image).

## Local Envd runtime

Install a compatible `a13n-envd` binary, then construct a Host-owned runtime:

```python
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_a13n_envd_executable,
)

runtime = LocalEnvdProviderRuntime(
    executable=resolve_a13n_envd_executable(),
    allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
)
```

The resolver checks an explicit path first, then `A13N_ENVD_EXECUTABLE`, then the platform's `a13n-envd` executable on `PATH`. Set the environment variable in the Host process; the library does not load `.env`, install the binary, or fall back to Direct Local.

Pass `runtime` to the Local Envd connector. First acquisition launches one shared Device and checks exact daemon/client compatibility. Each opened execution gets an independent Session. Close executions after use and call `await runtime.close()` at Host shutdown.

Select launch Sandbox and egress settings before acquisition. Envd enforces those settings within the outer boundary supplied by the Host account, container, or VM; runtime construction performs no native-isolation probe. Follow the [Local Envd example](examples.md#local-envd), or run the real Provider path with `make local-envd-test`.

## Docker runtime

For a managed Docker target:

1. Set `DockerConnectionConfiguration.docker_host` to the Engine the Host can reach. Reconnecting later requires access to the same Engine. Service's [Compose deployment](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/docker/compose) shows socket access for its non-root process.
2. Choose an image and execution route. Service's `DockerRecipe` defaults to `execution="native"` and `ghcr.io/converge-ai-labs/a13n-sandbox:dev`. Native execution uses Docker exec; `execution="envd_http"` uses the image's Envd daemon.
3. Add resource limits and optional bind mounts. Mount `source` paths must already exist on the Engine's machine; `read_only` defaults to true. Keep `/workspace` and private command metadata under the container's own filesystem. Named volumes are not a recipe option.
4. Create the target, save its state, and open a connector. Pulling or rebuilding an image tag does not replace an existing container.

The default `pull_policy="if_missing"` pulls only absent images. `"never"` reports `environment_image_missing` when the image is absent. Use an immutable digest when you need exact image identity. Native user selection uses an explicit recipe `user`, then `ai.a13n.environment.user`, then the image USER. The standard sandbox image selects UID/GID 1000.

Custom native images need Linux, Python 3.10+, the configured shell, and writable `/workspace` and `/tmp/a13n`. Git is needed for git-ignore queries. `init_script` runs on new containers; failed initialization is not implicitly replayed. Its helper request timeout is separate from ordinary shell duration. Put ordinary variables in `environment`, and keep secrets out of recipe fields. `memory_gb` uses decimal GB, while `cpus` measures cores. `disable_network=true` selects Docker's `none` network. Destruction preserves external bind-mounted data.

For HTTP EIP, configure the connection's `envd_http` with a Worker-reachable host and bind address. Service preserves the image entrypoint and publishes container port 8888 to a Docker-assigned host port. Use a compatible Envd image and keep networking enabled. The image owns execution identities, so this route rejects recipe `user` overrides. Add `desktop: true` to include observable, input-ready desktop readiness. Plain HTTP requires loopback or an explicitly trusted private link. Follow [Create a Docker desktop](../a13n-service/computer-use.md#create-a-docker-desktop) for the Console steps.

For headless Envd, select an official `-slim` sandbox tag matching your deployment and `execution="envd_http"` with `desktop=false`. Management bootstrap and credential reads need POSIX shell/core utilities, not guest Python or jq. Native Docker file operations still require Python.

To use a custom base, build an explicitly compatible wrapper image: install a compatible Envd binary and the common `a13n-sandbox-entrypoint`, provision its Session user and writable workspace, and select the wrapper plus the daemon command in your Dockerfile. Service preserves that entrypoint/command and starts it as root. A plain base image is not an automatic installation request. Match the binary's architecture and userspace requirements; adding a shell script cannot make a glibc binary run on musl. Desktop wrappers additionally supply the desktop launcher and its dependencies.

An embedding Host can borrow its own Engine client:

```python
import docker
from a13n_harness.providers.environment.docker.runtime import DockerProviderRuntime, DockerSDKEngine

engine = DockerSDKEngine(docker.from_env())
runtime = DockerProviderRuntime(engine=engine)
# Construct and use Environment instances with this runtime, then:
# await engine.close()
```

Keep the borrowed Engine open until its users finish, then close it at Host shutdown. Registry authentication, credential helpers, mirrors, and proxies use Docker client configuration.

Native execution overrides the image entrypoint, enables Docker init, and keeps the container alive between Runs. Relative tool paths start in `/workspace`; absolute paths address the container, with Harness adding its aggregate mount prefix. Execution close releases local observations while preserving the container and background processes. Re-entry uses the saved container; confirmed absence requires an explicit management decision. A transport failure alone does not establish absence.

Native Docker provides files, shell, process control, and output observation. Ports and Computer require `envd_http`; changing an existing Native recipe does not install a daemon. With HTTP EIP, Session close preserves the desktop, while stop/start preserves files and recreates GUI state. See the [managed desktop image contract](../a13n-envd/sandbox.md#managed-docker-desktop-in-service) for startup and permissions.

## Cloud Providers

Continue to [cloud Provider setup and limits](cloud-providers.md#cloud-providers).

### Reconnection and lifecycle

Continue to [cloud Provider setup and limits](cloud-providers.md#reconnection-and-lifecycle).

### E2B runtime

Continue to [cloud Provider setup and limits](cloud-providers.md#e2b-runtime).

### E2B desktop preparation

Continue to [cloud Provider setup and limits](cloud-providers.md#e2b-desktop-preparation).

### Daytona

Continue to [cloud Provider setup and limits](cloud-providers.md#daytona).

### Modal

Continue to [cloud Provider setup and limits](cloud-providers.md#modal).

### Vercel Sandbox

Continue to [cloud Provider setup and limits](cloud-providers.md#vercel-sandbox).

### Fly.io Sprites

Continue to [cloud Provider setup and limits](cloud-providers.md#flyio-sprites).

### Runloop

Continue to [cloud Provider setup and limits](cloud-providers.md#runloop).

### boxd

Continue to [cloud Provider setup and limits](cloud-providers.md#boxd).

### Embedded configuration

Continue to [cloud Provider setup and limits](cloud-providers.md#embedded-configuration).

### Cloud validation

Continue to [cloud Provider setup and limits](cloud-providers.md#cloud-validation).

## Provider catalog and plugins

Follow [Provider catalog and plugins](provider-plugins.md#provider-catalog-and-plugins) to select or publish a trusted plugin.
