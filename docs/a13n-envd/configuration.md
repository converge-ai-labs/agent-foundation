---
title: Daemon configuration and transports
sidebarTitle: Configuration and transports
description: Configure Envd stdio, HTTP, and reverse WebSocket carriers, session defaults, command enablement, and system telemetry collection.
---

Configure `a13n-envd` at startup to choose how it connects to a Host, which commands it permits, and what OS account it runs under. For a prebuilt container image with sandbox settings, see [Sandbox image](sandbox.md).

## Connect to Service

Register a computer with a Service workspace using an API token with `write` permission (stored in a `0600` file):

```sh
a13n-envd connect-service https://control.example.com --host work \
  --workspace-id ws_example --api-token-file /private/path/api-token \
  --default-working-directory /path/to/project
```

The first run enrolls the computer and saves its dedicated device credential under `--state-dir` (default `~/.a13n-envd`). On later restarts, run `a13n-envd connect-service work` without the API token file.

If the saved credential is lost, rotate it via `POST /api/v1/client-environments/{id}/rotate-credential?workspace_id=…` (with `If-Match`), save the new token to a private file, and restart with `--credential-file /private/path/device-token`.

## Connect to Harness UI

Start an outbound connection to a Harness UI server:

```console
a13n-envd connect https://host.example.com --host work
```

1. Open the printed approval URL, sign in, and approve the matching verification code.
2. Keep Envd running. On later restarts, run `a13n-envd connect work`.

HTTPS is required except for loopback (`http://127.0.0.1:8765`). Use `--ca-file` for a private CA, `--name` for the display label, and `--default-working-directory` for the default path.

### Saved instances and Hosts

Envd stores its device identity and per-Host credentials under `--state-dir` (`A13N_ENVD_STATE_DIR`, default `~/.a13n-envd` on Unix or `%LOCALAPPDATA%/.a13n-envd` on Windows).

To connect one computer to two different Hosts, run a separate process with its own `--instance` and `--host`:

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

Preserve the state directory across restarts and protect it like an account credential.

## Enable commands

By default, Envd allows file operations only. Enable Full Control to expose `/bin/sh` (Unix) or PowerShell (Windows) under the execution account:

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

Alternatively, omit `full_control` and declare explicit `trusted_executable_roots` and `shell_profiles` in your daemon JSON:

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "trusted_executable_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
  "shell_profiles": [
    {
      "profile_id": "sh",
      "display_name": "POSIX shell",
      "native_executable": "/bin/sh",
      "fixed_arguments": ["-c"],
      "executable_search_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
      "max_script_bytes": 1048576
    }
  ]
}
```

## Configuration layers

<span id="minimal-standalone-configuration"></span>

Envd merges five layers from lowest to highest precedence (objects merge recursively; scalars and arrays replace):

1. Built-in defaults
2. `--config /path/to/a13n-envd.json`
3. `A13N_ENVD_CONFIG_JSON`
4. Scalar `A13N_ENVD_*` environment variables (e.g., `A13N_ENVD_FULL_CONTROL=true`, `A13N_ENVD_DEVICE_ID=...`, `A13N_ENVD_ALLOW_SUDO=false`)
5. Explicit CLI flags

## Carrier profiles

Set `A13N_ENVD_TRANSPORT` to match your deployment:

| Profile             | Direction               | Typical use                             |
| ------------------- | ----------------------- | --------------------------------------- |
| `stdio`             | Parent process pipes    | Host-owned local daemon (default)       |
| `http`              | Host dials Envd         | Direct HTTPS or loopback HTTP daemon    |
| `reverse_websocket` | Envd dials Host (`wss`) | Outbound connection behind NAT/firewall |

Example loopback HTTP launch:

```bash
mkdir -p "$HOME/.a13n-envd-runtime" && chmod 700 "$HOME/.a13n-envd-runtime"
export A13N_ENVD_RUNTIME_DIR="$HOME/.a13n-envd-runtime"
export A13N_ENVD_DEVICE_ID=device-http-example
export A13N_ENVD_TRANSPORT=http
export A13N_ENVD_HTTP_BIND=127.0.0.1:8787
export A13N_ENVD_HTTP_CREDENTIAL_FILE=/private/envd-token
export A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback
a13n-envd --default-working-directory /absolute/path/to/workspace
```

For HTTPS, unset `A13N_ENVD_HTTP_PLAINTEXT_SCOPE` and set `A13N_ENVD_HTTP_TLS_CERT_FILE` and `A13N_ENVD_HTTP_TLS_KEY_FILE`. For `reverse_websocket`, set `A13N_ENVD_REVERSE_WS_URL` and `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`.

## Daemon lifetime and restarts

<span id="lifecycle-and-ownership"></span>

| Deployment                                     | Command                         | Restart owner                          |
| ---------------------------------------------- | ------------------------------- | -------------------------------------- |
| External service manager or Host-owned `stdio` | `a13n-envd daemon [options]`    | Service manager or Host                |
| Standalone HTTP / reverse WebSocket            | `a13n-envd supervise [options]` | Built-in supervisor (up to 3 restarts) |

A restart preserves the Device ID, workspace files, and credentials, but starts a fresh generation—clients must open a new Session.

## Native identity and sudo

On Linux, Envd runs as the launching user by default. To drop from root to a provisioned non-root account, set both `execution.uid` and `execution.gid` (or `A13N_ENVD_EXECUTION_UID` and `A13N_ENVD_EXECUTION_GID`).

> [!IMPORTANT]
> `execution.allow_sudo` defaults to `true` (subject to image sudoers). To block privilege escalation via `no_new_privs` on Linux, pass `--allow-sudo false` or set `A13N_ENVD_ALLOW_SUDO=false`.

## Health probe and monitoring

### Optional health probe

Enable an unauthenticated HTTP liveness probe (`GET /healthz` or `/ping`, returns `200 ok`):

```bash
A13N_ENVD_HEALTH_ENABLED=true A13N_ENVD_HEALTH_BIND=127.0.0.1:8080 a13n-envd daemon
```

### System information and resource monitoring

Envd can sample host/container CPU, memory, and disk metrics and export them via Prometheus or OpenTelemetry. See [Monitoring and metrics](monitoring.md) for setup.

## Standalone field reference

| JSON field                                   | Default / purpose                                                               |
| -------------------------------------------- | ------------------------------------------------------------------------------- |
| `device_id`                                  | Stable device identity (defaults to installation state directory)               |
| `default_working_directory`                  | Initial Session cwd (defaults to daemon startup cwd)                            |
| `directory_discovery`                        | `true` — allows bounded directory browsing before Session open                  |
| `idle_timeout_ms` / `max_lifetime_ms`        | Default Session idle (`3600000` ms) and max lifetime                            |
| `vm_startup_timeout_ms`                      | `120000` ms — VM startup admission budget                                       |
| `execution.uid`, `execution.gid`             | Optional paired Linux UID/GID                                                   |
| `execution.allow_sudo`                       | `true` — set `false` on Linux to enforce `no_new_privs`                         |
| `sandbox`                                    | `{"mode":"disabled"}` or `restricted` with directory `grants`                   |
| `egress`                                     | `{"mode":"inherit"}`, `deny`, or `controlled` (see [Session egress](egress.md)) |
| `full_control`                               | `false` — enables native shell and inherited environment                        |
| `computer_use`                               | `false` — opt-in desktop control (see [Computer use](computer-use.md))          |
| `trusted_executable_roots`, `shell_profiles` | Explicit command allowlist when `full_control` is `false`                       |
| `limits`                                     | Device and per-Session concurrency, file size, and spool caps                   |
