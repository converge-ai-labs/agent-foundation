---
title: Daemon configuration and transports
sidebarTitle: Configuration and transports
description: Connect Envd to a Host, choose execution settings, and collect resource metrics.
---

Configure Envd at startup to choose its connection, execution account, filesystem isolation and network mode. The Host owns deployment, credentials and daemon lifetime. Each EIP Session then selects a working directory and operations within those settings.

Use the JSON and environment variables on this page for the daemon. Harness UI YAML and `EnvironmentProviderSpec` configure the Host-side adapter, including required methods and optional Session egress policies with secret references. For a prepared container with account, shell and sudo settings, use the [sandbox image](sandbox.md).

## Configuration layers

Envd applies five layers, with later layers taking precedence:

1. Built-in defaults.
2. The daemon JSON file selected by `--config`.
3. `A13N_ENVD_CONFIG_JSON`, validated against the same schema.
4. Scalar `A13N_ENVD_*` environment variables.
5. Explicit CLI arguments.

Objects merge recursively; arrays and scalar values replace earlier values. Each JSON layer must validate before merging. A launcher can supply nested settings entirely through the environment:

```bash
export A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
export A13N_ENVD_DEVICE_ID=device-sandbox
export A13N_ENVD_FULL_CONTROL=true
export A13N_ENVD_CONFIG_JSON='{"default_working_directory":"/workspace","limits":{"max_file_bytes":104857600}}'
a13n-envd
```

Scalar settings cover identity, execution, discovery, metadata and lifetimes: `A13N_ENVD_ALLOW_SUDO`, `A13N_ENVD_EXECUTION_UID`, `A13N_ENVD_EXECUTION_GID`, `A13N_ENVD_EGRESS_MODE`, `A13N_ENVD_FULL_CONTROL`, `A13N_ENVD_COMPUTER_USE`, `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS`, `A13N_ENVD_DIRECTORY_DISCOVERY`, `A13N_ENVD_DEVICE_ID`, `A13N_ENVD_NAME`, `A13N_ENVD_DESCRIPTION`, `A13N_ENVD_DEFAULT_WORKING_DIRECTORY`, `A13N_ENVD_IDLE_TIMEOUT_MS` and `A13N_ENVD_DISCONNECT_GRACE_MS`. Boolean values accept `true`, `false`, `1` and `0`.

## Connect to Service

Use a regular Service API token belonging to the principal that will run the agent. Save it in a private file, then run:

```sh
a13n-envd connect-service https://control.example.com --host work \
  --workspace-id ws_example --api-token-file /private/path/api-token \
  --default-working-directory /path/to/project
```

The first connection registers the installation if it has no environment under that owner and workspace. Envd saves the environment ID and dedicated device credential locally. Restart with `a13n-envd connect-service work`; the API token file is no longer needed. Add `--environment-id env_example` on first use to require an existing environment. `--device-id` overrides the local identity file; keep using the same override on subsequent starts. Common daemon options, including `--computer-use`, remain available.

Service profiles live under `instances/<instance>/services/<name>` in `--state-dir` (default `~/.a13n-envd`, or the platform state directory on Windows). Use separate names for different accounts or workspaces. A disabled, deleted or revoked registration is not recreated automatically. An offline registration remains available for explicit [thread mounting](../a13n-service/environments.md#mount-environments-on-a-thread); connecting does not mount it.

If enrollment succeeded but its credential was lost, envd reports `client_credential_required` and the environment ID. Read the environment's current ETag and explicitly call `POST /api/v1/client-environments/{id}/rotate-credential?workspace_id=…` using the ordinary API token and `If-Match`. Save the returned `token` in a private file and restart with `a13n-envd connect-service work --credential-file /private/path/device-token`. Startup never rotates automatically. Keep token files private (mode `0600` on Unix); API tokens are not saved in profiles or used for WebSocket connections.

## Connect to Harness UI

Start an outbound connection from the computer that will run the work:

```console
a13n-envd connect https://host.example.com --host work
```

1. Open the printed approval address and sign in to the Host.
2. Approve the matching verification code.
3. Keep Envd running. On subsequent starts, use `a13n-envd connect work` to reuse the registration.

Envd generates the credential on the Device, so pairing needs only the verification code in the browser. Reconnecting with saved state reuses approval. For Service, use [first-connection enrollment](#connect-to-service).

Use a Host URL reachable from this computer. HTTPS is required except for loopback HTTP such as `http://127.0.0.1:8765`. Add `--ca-file /path/to/ca.pem` for a private CA; certificate verification stays enabled. Set `--name` for the approval display, `--default-working-directory` for an existing native directory, and `--config` for daemon JSON settings.

### Saved instances and Hosts

Preserve the state directory across restarts and protect it as an account credential. Its default is `~/.a13n-envd` on Unix and `%LOCALAPPDATA%/.a13n-envd` on Windows, falling back to `%USERPROFILE%/.a13n-envd`. Override it with `--state-dir` or `A13N_ENVD_STATE_DIR`.

An instance stores its Device ID at `instances/<instance>/device-id`. Each Host's configuration, credential and private runtime live under `instances/<instance>/hosts/<host>/`. The default instance is `default`; omitting `--host` derives a stable alias from the URL.

Run a separate process for each Host. For two Hosts on one computer:

```console
a13n-envd connect https://personal.example.com --instance personal --host personal
a13n-envd connect https://team.example.com --instance team --host team
```

Restart each process with its original instance and Host alias. Each has its own registration and Sessions. `connect` runs in the foreground; to start it automatically, configure your service manager with the same account, state directory and command environment.

If authentication is rejected, Envd stops and preserves its credential. Check the Host address, registration status and mounted state directory. Use a new instance and explicit approval for a genuinely new installation.

## Minimal standalone configuration

For a Host-owned stdio daemon:

1. Create and protect an absolute runtime parent directory.
2. Save the JSON below as `/absolute/path/to/a13n-envd.json`, replacing the workspace path with an existing directory.
3. Have the Host launch Envd with piped stdin and stdout:

```bash
export A13N_ENVD_RUNTIME_DIR=/absolute/path/to/private-runtime
export A13N_ENVD_DEVICE_ID=device-my-machine
a13n-envd --config /absolute/path/to/a13n-envd.json
```

Envd creates an unpredictable private child for this generation and holds an exclusive runtime lock. The pipes carry framed EIP traffic; diagnostics go to stderr.

```json
{
  "default_working_directory": "/absolute/path/to/workspace",
  "directory_discovery": true,
  "limits": {
    "max_file_bytes": 104857600
  }
}
```

The default working directory becomes each Session's initial cwd. If omitted, Envd captures its startup cwd. A nonexistent directory fails Session opening rather than daemon startup. File access follows the execution account, Sandbox grants and outer sandbox, not a cwd boundary. Directory discovery reads one bounded level before a Session exists.

Choose a stable Device ID with `--device-id`, JSON `device_id` or `A13N_ENVD_DEVICE_ID`, or let the installation state directory retain a generated ID. CLI metadata options override JSON. Directory discovery uses JSON or `A13N_ENVD_DIRECTORY_DISCOVERY`. On Windows, bootstrap paths use native spelling; EIP paths use `/C:/...` or `/UNC/server/share/...`.

## System information and resource monitoring

See [Device monitoring and metrics](monitoring.md#system-information-and-resource-monitoring) for configuration and interpretation.

## Prometheus and OpenTelemetry

See [Device monitoring and metrics](monitoring.md#prometheus-and-opentelemetry) for configuration and interpretation.

### Native OTLP push

See [Device monitoring and metrics](monitoring.md#native-otlp-push) for configuration and interpretation.

### Prometheus scrape configuration

See [Device monitoring and metrics](monitoring.md#prometheus-scrape-configuration) for configuration and interpretation.

### OpenTelemetry Collector

See [Device monitoring and metrics](monitoring.md#opentelemetry-collector) for configuration and interpretation.

### Metrics and freshness

See [Device monitoring and metrics](monitoring.md#metrics-and-freshness) for configuration and interpretation.

## Enable commands

Enable Full Control for a native shell running with the execution account's authority:

```bash
A13N_ENVD_FULL_CONTROL=1 a13n-envd connect https://host.example.com
```

JSON `"full_control": true` selects the same mode; the scalar environment setting overrides it. Full Control supplies `/bin/sh` on Unix or PowerShell on Windows and inherits the launcher's environment and `PATH` order. It excludes daemon bootstrap variables (`A13N_ENVD_*`, `EIP_*`) and OTLP metric settings. Shell aliases and unexported variables stay with the launching shell. Per-command overrides affect that command only.

Envd `full_control` enables execution, not isolation, and is separate from Harness UI's local mode. Choose either Full Control or manual executable roots/shell profiles; they cannot be combined. For explicit command configuration, use:

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
      "safe_base_environment": {},
      "executable_search_roots": ["/usr/local/bin", "/usr/bin", "/bin"],
      "max_script_bytes": 1048576,
      "allow_login_mode": false
    }
  ]
}
```

With Full Control off and both roots/profiles empty, Envd advertises no command methods. Roots and profiles select trusted executables; the Host's outer boundary and [execution isolation](isolation.md) govern access. [Controlled Session egress](egress.md) adds destination restrictions and credential injection.

Check the Device's advertised execution features for signal, limit and executable-bit support. Child environments combine supported inherited values with explicit command inputs; daemon control variables remain startup configuration.

## Native identity and sudo

> [!IMPORTANT]
> Native sudo is allowed by default. With Sandbox disabled, authorized package installation and system-file changes modify the original outer-sandbox filesystem and persist across Sessions.

On Linux, omitting execution UID/GID keeps the launcher's identity, including root. To switch from a root launcher, first provision the account and its home/workspace permissions, then set both `execution.uid`/`execution.gid`, both environment variables, or both CLI options. Explicit `0:0` selects root. For a provisioned `sandbox` account:

```bash
# Replace sandbox with the account provisioned by your image or deployment.
export A13N_ENVD_EXECUTION_UID="$(id -u sandbox)"
export A13N_ENVD_EXECUTION_GID="$(id -g sandbox)"
a13n-envd
```

Commands, file RPCs, working-directory access and directory discovery use the selected account's native permissions. A file RPC reports insufficient permission rather than invoking sudo. Sudo in a command affects that command and descendants, not later file RPCs. Credential loading and broker management remain daemon responsibilities.

Account switching initializes supplementary groups and account environment defaults. Keeping the launcher's identity retains native capability inheritance. A root-to-user switch removes Envd's retained supervisor signaling capability before command execution. Outer-platform and controlled-egress restrictions still apply; an unprivileged launcher cannot select another account.

Install sudo and configure sudoers in the image or deployment. `allow_sudo` permits native privilege gains subject to that authorization and outer `no_new_privs`/`nosuid` restrictions. Envd supplies no passwordless rule.

Disable additional privilege gains with one of these launch settings:

```bash
# Environment variable
A13N_ENVD_ALLOW_SUDO=false a13n-envd

# CLI, including the connect command
a13n-envd --allow-sudo false
a13n-envd connect work --allow-sudo false

# Same nested setting without a file
A13N_ENVD_CONFIG_JSON='{"execution":{"allow_sudo":false}}' a13n-envd
```

The equivalent JSON setting is:

```json
{
  "execution": {"allow_sudo": false}
}
```

On Linux this applies `no_new_privs` to workers and descendants, blocking gains through sudo, setuid binaries and file capabilities. Existing authority, including root, remains. Startup precedence still applies (`--allow-sudo true` overrides an environment `false`); a command cannot change this policy through its own environment. Platforms without this implementation reject `false`. UID/GID selection is Linux-only; other platforms keep the native launching identity.

## Carrier profiles

Choose the connection direction for your Host:

| Profile             | Bootstrap                                                                        | Use                                         |
| ------------------- | -------------------------------------------------------------------------------- | ------------------------------------------- |
| `stdio`             | Parent-supplied process pipes                                                    | Host-owned local daemon                     |
| `http`              | Bind address, protected credential file, TLS or explicit trusted plaintext scope | Host-dialed Device                          |
| `reverse_websocket` | Outbound URL and protected credential file                                       | Device dials an authenticated Host listener |

Set `A13N_ENVD_TRANSPORT` to the chosen profile. For a local HTTP client:

1. Prepare a protected token file and an existing workspace.
2. Replace their paths in the command below, which creates a private runtime parent and binds loopback HTTP.
3. Launch Envd:

```bash
mkdir -p "$HOME/.a13n-envd-runtime"
chmod 700 "$HOME/.a13n-envd-runtime"
export A13N_ENVD_RUNTIME_DIR="$HOME/.a13n-envd-runtime"
export A13N_ENVD_DEVICE_ID=device-http-example
export A13N_ENVD_TRANSPORT=http
export A13N_ENVD_HTTP_BIND=127.0.0.1:8787
export A13N_ENVD_HTTP_CREDENTIAL_FILE=/private/envd-token
export A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback
a13n-envd --default-working-directory /absolute/path/to/workspace
```

Connect the Python client to the base origin `http://127.0.0.1:8787` using the same credential and expected Device ID. The client selects the authenticated `/eip/control` and `/eip/transfer` routes.

For HTTPS, unset `A13N_ENVD_HTTP_PLAINTEXT_SCOPE`, set `A13N_ENVD_HTTP_TLS_CERT_FILE` and `A13N_ENVD_HTTP_TLS_KEY_FILE`, and configure client trust before connecting. A private provider route uses `provider_private_link` plus explicit client opt-in. Public endpoints require HTTPS. See [Python transport options](python-client.md#http-options) for proxy routing and TLS.

For outbound reverse WebSocket, set `A13N_ENVD_REVERSE_WS_URL` and `A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE`; add private trust with `A13N_ENVD_REVERSE_WS_CA_FILE`. The Host owns the WebSocket listener.

Store credentials in protected files. Keep them out of argv, URLs, descriptors, logs and portable Environment state. A credential grants Device access; Session isolation comes from execution boundaries.

## Lifecycle and ownership

Initialize a Device connection, then open a Session for each resource scope. One daemon generation serves multiple Sessions; each captures its cwd and owns its operations. `session.close` closes that scope. Keepalive renews only the calling owner, so discovery traffic does not preserve abandoned Sessions.

After a framed-carrier disconnect, the owner can explicitly reattach during the grace period within the same generation. Reattachment gives access to existing resources without replaying commands or resuming transfers. After expiry, open a fresh Session and discard old references.

On shutdown, the Host closes its connections and terminates only daemons whose lifecycle it owns. Session cleanup reclaims processes and private output/staging storage while preserving workspace files. Treat a reported cleanup failure as incomplete reclamation.

## Check before admitting work

Check the daemon release:

```bash
a13n-envd --version
```

1. Match the exact Python client release.
2. Remove Host-only `A13N_ENVD_EXECUTABLE` from the daemon child environment. Unknown `A13N_ENVD_*` variables and JSON fields fail configuration.
3. Initialize the Device to verify protocol and identity, then check Session readiness for the selected scope.
4. Test the outer execution boundary through the deployment's own launcher and checks. Envd has no `isolation probe` or per-command isolation mode.

## Standalone field reference

Use these fields in daemon JSON:

| Root field                                   | Default or meaning                                                                                                                         |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `device_id`                                  | Explicit stable identity, otherwise installation identity                                                                                  |
| `installation_state_directory`               | Persistent identity storage when no explicit Device ID is supplied                                                                         |
| `name`, `description`                        | Optional display metadata                                                                                                                  |
| `default_working_directory`                  | Startup cwd if unset                                                                                                                       |
| `directory_discovery`                        | `true`                                                                                                                                     |
| `idle_timeout_ms`                            | Default Session idle lifetime: `3600000` (one hour)                                                                                        |
| `max_lifetime_ms`                            | Default maximum Session lifetime; positive when set, otherwise unlimited                                                                   |
| `execution.uid`, `execution.gid`             | Optional paired Linux native IDs; omitted retains the launcher, including root                                                             |
| `execution.allow_sudo`                       | `true`; native sudoers still controls authorization                                                                                        |
| `sandbox`                                    | `{"mode":"disabled"}`; or `restricted` with directory `grants`                                                                             |
| `egress`                                     | `{"mode":"inherit"}`; or `deny` / `controlled`                                                                                             |
| `full_control`                               | `false`; automatic native shell and inherited command environment when enabled                                                             |
| `computer_use`                               | `false`; opt-in macOS/X11/Windows screenshot/input methods with disabled Sandbox and inherited egress; see [computer use](computer-use.md) |
| `computer_use_permission_timeout_ms`         | `120000`; positive startup deadline for macOS permissions or X11/Windows desktop readiness before any EIP transport starts                 |
| `trusted_executable_roots`, `shell_profiles` | Empty; command methods disabled unless Full Control is enabled                                                                             |
| `limits`                                     | Device aggregates and per-Session limits                                                                                                   |

`idle_timeout_ms` and `max_lifetime_ms` are defaults used when `session.open` omits `lifecycle`. An explicit Session lifecycle overrides these defaults and remains immutable for that Session.

Default capacity is 128 Sessions, 256 concurrent Device operations, 128 concurrent operations per Session, 4 GiB Device spool, 1 GiB Session spool, 256 MiB per output stream and 2 MiB previews. Keep Session capacities within Device aggregates, previews within stream capacity, and enough spool capacity reserved for both streams.

### Shell-profile fields

Supply `profile_id`, `display_name`, `native_executable`, `executable_search_roots` and a positive `max_script_bytes`. Use existing absolute native paths for executables and roots. Optional defaults are `fixed_arguments: []`, `safe_base_environment: {}` and `allow_login_mode: false`.

## Controlled Session egress

For per-Session destination restrictions and HTTPS credential injection on Linux, follow [Session egress](egress.md). Configure controlled mode at daemon startup, then submit policies and secrets through EIP.
