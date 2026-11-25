---
title: Cloud Provider setup and limits
sidebarTitle: Cloud Providers
description: Configure E2B, Daytona, Modal, Vercel, Sprites, Runloop, and boxd cloud sandboxes with native or Envd execution paths and custom desktop images.
---

Choose a cloud backend in the [Environment overview](index.md#choose-a-backend), manage input ownership via [Configuration](configuration.md), and persist returned targets via [Lifecycle](lifecycle.md).

## Cloud Providers

Daytona and Modal run through Envd; E2B, Vercel, and Sprites support both Native Files & Shell and Envd; Runloop and boxd provide Native Files & Shell only. Store API keys in your credential store and keep recipes and saved `EnvironmentState` free of secrets:

| Provider       | Backend settings                                                                 | Credential fields          | Common recipe settings                                                                                              |
| -------------- | -------------------------------------------------------------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| E2B            | `domain`, optional `api_url`                                                     | `api_key`                  | `template`, `execution`, `preparation`, `desktop`, `user`, `root`, `allow_internet_access`, `timeout_seconds`       |
| Daytona        | `organization_id`, `target` (default `us`)                                       | `api_key`                  | `snapshot` (`"auto"` or snapshot name), `desktop`, `desktop_assistance`                                             |
| Modal          | optional `workspace`, `app_name` (`a13n-sandboxes`), `environment_name` (`main`) | `token_id`, `token_secret` | `image` (`"auto"` or image ID), `desktop`, `desktop_assistance`, `cpu`, `memory` (MiB), `timeout_seconds`           |
| Vercel Sandbox | `team_id`, `project_id`                                                          | `api_key` (access token)   | `execution`, `snapshot` (`"auto"` or ID), `desktop`, `runtime`, `vcpus`, `timeout_seconds`                          |
| Fly.io Sprites | `organization`                                                                   | `api_key` (Sprites token)  | `region`, `execution`, `desktop`                                                                                    |
| Runloop        | `organization`                                                                   | `api_key`                  | `blueprint_id`, `resource_size`, `idle_timeout_seconds`                                                             |
| boxd           | `region` (`fra` or `nyc`), optional `organization`                               | `api_key`                  | `size` (`1x4`–`8x32`), `volumes`, `from_snapshot`, `auto_suspend_timeout_seconds`, `auto_hibernate_timeout_seconds` |

### Reconnection and lifecycle

Create or start a target through management, persist the returned `EnvironmentState` before running Agents, attach via a fixed-target connector per Run, and stop or destroy through management when finished:

| Provider       | Stop / resume behavior                                     | Memory preserved | Expiry & keepalive notes                                                                 |
| -------------- | ---------------------------------------------------------- | ---------------- | ---------------------------------------------------------------------------------------- |
| E2B            | Native pause/resume preserves disk                         | Yes              | Renewable whole-sandbox TTL (`timeout_seconds`, default `3600`)                          |
| Daytona        | Native stop/start (including archived sandboxes)           | Not promised     | Requires disabled auto-delete and no org-enforced hard TTL                               |
| Modal          | Filesystem snapshot on stop; `start()` creates new sandbox | No               | Max 24h running TTL; snapshots have no expiry and are cleaned up on resume/destroy       |
| Vercel Sandbox | Named native stop/resume preserves disk                    | No               | Bounded session timeout renewed up to total limit; stop snapshots do not expire          |
| Fly.io Sprites | No explicit `stop()`; idle Sprites auto-sleep and wake     | Not promised     | Durable disk survives sleep; destroy explicitly when done                                |
| Runloop        | Native suspend/resume preserves disk                       | No               | Idle policy suspends Devbox; keepalive extends active intervals                          |
| boxd           | Native hibernate/wake preserves disk and RAM               | Yes              | No hard expiry; optional `auto_hibernate_timeout_seconds` stops until explicit `start()` |

### E2B runtime

Use `template: "base"` for Native Files & Shell (`/home/user`, `/usr/bin/python3`) without custom builds, or run `cd examples/environment-provider && uv run python -m a13n_environment_example.e2b` for a self-cleaning walkthrough:

```python
import os
from a13n_service.providers.environments.e2b.definition import E2B

recipe = {"template": "base", "timeout_seconds": 300}
async with await E2B.open_provider(
    configuration={"domain": "e2b.dev"}, credential={"api_key": os.environ["E2B_API_KEY"]}
) as provider:
    state = await provider.create(recipe, environment_id="env-example", operation_id="op-create")
    await state_store.publish(environment_key, state)
    connector = E2B.execution.execution_connector(
        E2B.execution_environment(recipe),
        credential={"api_key": os.environ["E2B_API_KEY"]},
        environment_id="env-example",
        state=state,
    )
result = await executable.run("Inspect the sandbox", environment=PreparedSource(connector))
```

Native E2B bounds observation buffers independently of sandbox TTL (`max_active_observations=128`, `max_observation_bytes=1 MiB`, `max_retained_output_bytes=128 MiB`, `request_timeout_seconds=30`). For headless Envd without Desktop, use a glibc 2.39+ template (Debian 13 / Ubuntu 24.04+) with `{"template": "your-base", "execution": "envd_http", "preparation": "headless", "timeout_seconds": 1200}` (`>= 960`s).

### E2B desktop preparation

Enable **Automatic desktop** in Service (`{"template": "auto", "execution": "envd_http", "desktop": true, "desktop_assistance": true}`) to build and reuse a private desktop template automatically, or build a custom template from a Linux `amd64` Envd binary:

```bash
cd examples/environment-provider && uv sync --locked
uv run python desktop_template.py my-envd-desktop --binary /absolute/path/to/linux-amd64/a13n-envd
```

File, Shell, and [Computer operations](../a13n-envd/computer-use.md) run over EIP at `/workspace`, while the desktop supervisor enforces revocable human viewing and takeover.

### Daytona

Set your organization's default region in Daytona, supply an API key with Sandbox and Snapshot permissions, and use `snapshot: "auto"` (2 vCPUs, 4 GiB RAM, 10 GiB disk) for automatic headless or desktop Envd preparation. To prebuild a custom snapshot for runtime-only keys:

```bash
uv run --with daytona==0.225.0 python examples/environment-provider/daytona_desktop_snapshot.py \
  my-envd --region us --binary /absolute/path/to/linux-amd64/a13n-envd
```

Agent operations use EIP on port `8888` at `/workspace`, and human desktop assistance uses signed short-lived routes on port `6081`.

### Modal

Supply `token_id` and `token_secret` and leave `image` unset or `"auto"` (`desktop=false` for headless, `desktop=true` for Computer + human assistance at 2 CPUs / 4096 MiB). Both Modal and Daytona automatic builds also accept an optional digest-pinned `base_image` (glibc >= 2.39, unused UID/GID 1000). To build a custom preinstalled image:

```sh
uv run --locked python examples/environment-provider/modal_desktop_image.py \
  --app a13n-sandbox-dev --binary /path/to/linux-amd64/a13n-envd
```

Reference the resulting `"image": "im-..."` with `"envd_management": "preinstalled"` and `"startup_command": ["a13n-sandbox-entrypoint", "a13n-sandbox-desktop", "a13n-envd", "supervise"]`. EIP runs at `/workspace` (UID/GID 1000) over TLS port `8888`, separate from viewer port `6081`.

### Vercel Sandbox

Use `execution: "native"` (`/vercel/sandbox`) for Native Files & Shell, or `{"execution": "envd_http", "snapshot": "auto", "desktop": false, "timeout_seconds": 2700}` (`desktop: true` for Computer + human access) to prepare Envd at `/workspace` (UID/GID 1001). You can also prebuild a custom snapshot with `uv run --locked python examples/environment-provider/vercel_desktop_snapshot.py --binary /path/to/linux-amd64/a13n-envd` and pass `"envd_management": "preinstalled"` plus the background `startup_command`.

### Fly.io Sprites

Sprites defaults to `/home/sprite` for Native execution, or installs headless Envd when `execution="envd_http"` (`desktop=false`). Enable Desktop in Service for [automatic Sprites desktops](../a13n-service/computer-use.md#automatic-sprites-desktops). Sprites sleep automatically when idle and wake on execution—do not call `stop()`.

### Runloop

Runloop Devboxes run Native Files & Shell at `/home/user` with guest `python3` and idle suspension managed via `idle_timeout_seconds`.

### boxd

Each Environment provisions an isolated [boxd](https://boxd.sh?ref=agent-foundation) VM at `/home/boxd` (`pip install a13n-harness[boxd]`, API key via `boxd auth keys create <name>`). `stop()` hibernates disk, memory, and processes; `start()` wakes the machine.

### Embedded configuration

Attach directly from an embedded Python Host using `select_builtin_environment_providers` and a persisted `EnvironmentState`:

```python
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers

(DAYTONA,) = select_builtin_environment_providers(("daytona",))
connector = DAYTONA.execution_connector(
    {"snapshot": "my-envd"},
    configuration={"organization_id": "your-organization"},
    credential={"api_key": api_key},
    environment_id="env-workspace",
    state=saved_state,
)
async with await connector.open() as execution:
    assert execution.operations.files is not None
    listing = await execution.operations.files.list("/", max_results=20)
```

### Cloud validation

Run offline adapter unit tests by default, and opt into live cloud checks with provider-scoped credentials (which allocate and clean up billable sandboxes):

```sh
A13N_TEST_CLOUD_PROVIDERS=daytona make test \
  PYTHON_TEST_DIRS=packages/a13n-service/tests/environment_providers/test_cloud_live.py
```

Vendor SDK docs: [E2B](https://e2b.dev/docs), [Daytona](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/), [Modal](https://modal.com/docs/guide/sandbox-snapshots), [Vercel Sandbox](https://github.com/vercel/sandbox), [Sprites](https://docs.sprites.dev/api/dev-latest/exec/), [Runloop](https://runloopai.github.io/api-client-python/sdk/async/devbox.html), and [boxd](https://docs.boxd.sh/reference/python-sdk?ref=agent-foundation).
