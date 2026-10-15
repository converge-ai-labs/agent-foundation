---
title: Sandbox image
description: Choose a minimal Envd runtime or a development sandbox with browser automation and optional workspace skills.
---

The `a13n-sandbox` image has three variants. All start Envd as root and run Session commands and file operations as the provisioned `sandbox` account (`1000:1000`). The standard and desktop variants also grant that account passwordless sudo. The outer container or VM supplies isolation; these variants' default UID is not a barrier against intentional root access.

| Variant            | Contents                                                                           | Intended use                                                                               |
| ------------------ | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Standard (default) | Envd, the development toolchain, agent-browser, Chromium, fonts and bundled skills | Agent development through Envd or the native Docker Provider                               |
| Desktop            | Standard toolchain plus Xvfb, Openbox and supervised desktop startup               | Computer Use through an image-managed Envd                                                 |
| Slim               | Envd, Debian's basic shell/utilities, runtime libraries, CA certificates and tini  | Envd operation or a base for a custom image; no Python, Node.js, compiler, sudo or browser |

Slim does not meet the native Docker Provider's Python prerequisite. It does not include the optional controlled-egress network tools; use the standard variant or provision those tools in a derived image.

**Controlled egress is off by default.** Ordinary use requires no `--privileged`, extra capabilities, or disabled Docker security profiles. Envd still requires a Linux runtime supporting its native process operations; a custom runtime or reduced capability set can reject them.

## Build and check

From the repository root:

```bash
make image-sandbox
make image-check-sandbox
make image-sandbox-slim
make image-check-sandbox-slim
make image-sandbox-desktop
make image-check-sandbox-desktop
```

The local tags are `a13n-sandbox:local`, `a13n-sandbox:local-slim` and `a13n-sandbox:local-desktop`. Smoke checks verify daemon startup, identity and directory permissions for all variants. Standard checks also cover the toolchain, sudo, skill initialization and offline browser snapshots/screenshots. Container CI runs these checks on native AMD64 and ARM64 runners. Protocol and execution-isolation tests remain in the Envd test suite.

Published images use `ghcr.io/converge-ai-labs/a13n-sandbox`. Select the Envd release tag matching your EIP client. All variants contain the same daemon version.

| Channel           | Standard          | Slim                 | Desktop                    |
| ----------------- | ----------------- | -------------------- | -------------------------- |
| Stable release    | `X.Y.Z`, `latest` | `X.Y.Z-slim`, `slim` | `X.Y.Z-desktop`, `desktop` |
| Release candidate | `X.Y.Z-rc.N`      | `X.Y.Z-rc.N-slim`    | `X.Y.Z-rc.N-desktop`       |
| Main development  | `dev`             | `dev-slim`           | `dev-desktop`              |

RC and development publication never advance `latest`, `slim` or `desktop`. These instructions describe the source images in this checkout, not a promise that an older published tag already has these defaults.

## Managed Docker desktop in Service

1. Build `a13n-sandbox:local-desktop` on the selected Docker Engine, or select a published desktop tag containing this feature.
2. In Console, add a Docker connection. Configure the existing **Docker host** for Engine management, then enable **Envd HTTP connection** and enter the hostname or IP reachable by every Service/Worker. For a local process connecting to Docker Desktop, use `127.0.0.1` for both the reachable host and bind address. For a remote Engine, explicitly configure its reachable private address and port-binding interface, and opt into the trusted plaintext private link.
3. In the Console template editor, select **Desktop environment**. Service selects the desktop image and HTTP EIP execution together; these fields cannot be edited in this mode. **Standard environment** selects the standard image and native Docker execution. CPU, memory and mounts remain configurable.
4. For a local build or your own image, select **Custom image** to expose image, execution, desktop and advanced/JSON settings. Use `a13n-sandbox:local-desktop`, execution `envd_http`, **Desktop capability** enabled (`desktop: true`), and `pull_policy: never`. Do not disable networking. The image supplies desktop/envd startup; no custom initialization script is needed.
5. Create an Environment. Service publishes envd's port, discovers its Docker-assigned host port, verifies its device identity through authenticated EIP, and checks actual desktop readiness.
6. Enable Computer tools on the Agent and allow the required Computer operations on its environment mount. These permissions do not supply a missing desktop.

Example Docker connection configuration for a trusted private network:

```json
{
  "docker_host": "tcp://10.0.0.8:2376",
  "envd_http": {
    "host": "10.0.0.8",
    "bind_host": "10.0.0.8",
    "allow_plaintext_private_link": true
  }
}
```

The Docker API and envd HTTP are separate connections. Configure Docker API transport protection independently. The HTTP path publishes an ephemeral host port for container port 8888; routing/firewalls must allow it from all relevant Service/Worker roles. It provides no TLS termination and must not expose bearer-authenticated plaintext traffic on the public Internet. Deployment outbound policy still applies to the resolved endpoint. A cloud Worker cannot reach a local Mac behind NAT through this path without an existing inbound route; Service reverse WebSocket integration is separate work.

Each attachment inspects the exact retained container's current port mapping, including after restart, and verifies its expected envd device ID. The common image entrypoint creates a random credential in `/var/lib/a13n-envd/http-token` with mode 0600, or the configured `credential_file`. The provider reads it through the authorized Docker Engine connection; it never publishes the token in template JSON, instance state or API responses. Agent operations go directly over existing HTTP EIP, with no Docker exec proxy. Custom images can use the same bootstrap contract without an official image label; `execution: envd_http` also supports headless envd.

Failed readiness retains the allocated container for inspection. Closing a Run leaves it running; stop/start retains files and credentials but starts a fresh graphical session. Stop/destroy revokes access; credential rotation requires stopping the container, replacing its private token file, closing existing sessions, and restarting. Experimental relay-based desktop templates from before this change need new instances with the explicit execution and connection settings; native Docker recipes retain their defaults and fingerprints.

The desktop launcher runs as root, with Xvfb/Openbox and Session operations under `sandbox`. It creates an Xauthority cookie, disables X11 TCP listening, supervises required processes, and fails the container if the desktop dies. The standard image's passwordless sudo behavior also applies. This feature provides Agent screenshots/input, not a live remote-desktop viewer or human takeover. X11 literal Unicode `type_text` remains unsupported; supported key events are available.

## Run with a Host

### Stdio

Have your EIP Host launch this command with piped stdin and stdout:

```bash
docker run --rm -i a13n-sandbox:local
```

Keep `-i`, and do not add `-t`: stdin/stdout carry framed EIP traffic, not an interactive terminal. The default command starts Envd under tini; the standard entrypoint first initializes workspace skills. It is not a shell, HTTP listener or idle container. It waits for an EIP client while stdin remains open and exits when stdin closes. Running it without `-i` and seeing a clean exit is expected.

The default working directory is `/workspace`. To preserve work in a Docker-managed volume:

```bash
docker volume create agent-workspace
docker run --rm -i \
  --mount type=volume,source=agent-workspace,target=/workspace \
  a13n-sandbox:local
```

A new empty volume inherits the image directory's ownership. Existing volumes and bind mounts retain their ownership; arrange write access for `1000:1000` yourself. The image does not recursively chown mounted files at startup. Closing a Session does not delete files in `/workspace`, but removing a container discards files not stored in a volume or bind mount.

### Connect to Harness UI

Use an outbound connection to Harness UI:

```bash
docker volume create agent-envd-state
docker volume create agent-workspace
docker run --rm --name agent-sandbox \
  --mount type=volume,source=agent-envd-state,target=/var/lib/a13n-envd \
  --mount type=volume,source=agent-workspace,target=/workspace \
  a13n-sandbox:local \
  a13n-envd connect https://host.example.com --host work
```

Replace the Host URL with one reachable **from inside the container**. Container `localhost` is not the host computer. Approve the printed verification code at the Host, then leave the container running. This outbound mode needs no Docker port publishing. Subsequent starts reuse the saved Device identity and credential from the state volume. Treat that volume as a credential; do not mount it into another unrelated sandbox. See [registration and saved Hosts](configuration.md#connect-to-harness-ui). To use the sandbox with Service, run it as an HTTP daemon and [register it as an external target](../environments/remote-envd.md#connect-to-the-service).

The persistent installation directory is `/var/lib/a13n-envd`. Standalone generation-private runtime data uses `/run/a13n-envd-state`; `connect` manages its Host-specific runtime under the installation directory. Neither runtime directory is a working directory or a Session recovery checkpoint.

## Workspace skills and browser automation

The standard image installs a pinned agent-browser CLI and system Chromium at build time. `AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium` selects that browser on both architectures. Startup does not download a browser or skills. Run browser commands as `sandbox`, just like ordinary EIP commands.

The bundled skill source is `/usr/local/share/a13n/skills`. The default entrypoint runs `a13n-sandbox-init` synchronously, then uses `exec` to start the supplied command. The initializer copies each missing skill directory into `.agents/skills` under the container's startup cwd, normally `/workspace`. Set Docker `--workdir` to select another existing writable directory. Selecting another Envd Session cwd or overriding the daemon's default working directory does not initialize that directory.

Existing same-name directories, files and symlinks are preserved without merging or updating. On image upgrades, an existing workspace skill remains user-owned; remove that skill directory explicitly if you want it seeded again. Failed copies are not published as partial skills. Unwritable destinations produce stderr warnings and do not prevent the requested command from starting. No initialization messages are written to stdout, which remains available for stdio EIP.

When invoked as root, the initializer switches to `A13N_ENVD_EXECUTION_UID` and `A13N_ENVD_EXECUTION_GID` (image defaults `1000:1000`) before writing. Non-root callers retain their current identity. The script does not parse Envd JSON or CLI options and never recursively changes mounted-file ownership. Use the image's UID/GID environment settings consistently when selecting another provisioned account. `A13N_SANDBOX_SKILLS_DIR` can select another bundled skill source.

Replacing CMD still runs initialization. Replacing ENTRYPOINT opts out. Custom launchers and the native Docker Provider can call the initializer explicitly:

```bash
# Bypass the standard entrypoint and initialize explicitly as the workload user.
docker run --rm --user sandbox --entrypoint sh a13n-sandbox:local \
  -c 'a13n-sandbox-init && agent-browser skills get core'
```

The native Docker Provider replaces the image entrypoint. To opt in, set its existing recipe field:

```yaml
init_script: a13n-sandbox-init
```

Neither Envd nor the Provider installs skills automatically outside this launch convention. With the Skills capability enabled, Harness UI discovers skills under the selected Device working directory's `.agents/skills` when preparing a Run.

## Everyday development

The standard image includes Python 3.13 with pip/venv, uv, Node.js 24, npm, pnpm, C/C++ build tools, Bash, Git, curl, SSH client, jq, ripgrep, patch, zip/unzip, process tools, sudo and CA certificates. `/workspace` and `/tmp/a13n` are writable by `sandbox`. It also installs the userspace namespace/network tools needed by opt-in egress; installing them does not grant kernel privileges. Add project-specific dependencies with sudo or a derived image. Published sandbox images support `linux/amd64` and `linux/arm64`.

The standard image supports the native Docker Environment Provider. That provider replaces the daemon entrypoint with its keepalive and uses Docker exec for file and shell operations. The image label `ai.a13n.environment.user=sandbox` selects the non-root container user unless a recipe explicitly overrides `user`; standalone Envd still starts as root and launches EIP operations as `sandbox`. Native Docker does not expose an EIP listener or start Envd.

Commands executed through EIP start as `sandbox`, with `HOME=/home/sandbox`. For example:

```bash
id -u                         # 1000
sudo -n id -u                 # 0
sudo -n apt-get update
sudo -n apt-get install -y sqlite3
python3 -m venv .venv
.venv/bin/python -m pip install requests
```

System-package changes persist across Sessions in the same container, but not after that container is removed. Use a derived image for repeatable dependencies. Git author identity and SSH credentials are not supplied by the image; configure them for your own workload.

File RPCs remain unprivileged even after a command uses sudo. A root-owned file created by sudo may require an explicit permission/ownership change before a later file RPC can modify it.

For a human debugging an already running container, select the execution account explicitly:

```bash
docker exec -it --user sandbox agent-sandbox bash
```

Docker exec does not pass through Envd: omitting `--user sandbox` uses the image's root launch account, and Envd's identity, sudo and egress policies do not constrain these direct Docker operations.

## Defaults and overrides

| Setting            | Image behavior                                                              |
| ------------------ | --------------------------------------------------------------------------- |
| Launcher           | Root, supervised by tini                                                    |
| Execution identity | `A13N_ENVD_EXECUTION_UID=1000`, `A13N_ENVD_EXECUTION_GID=1000`              |
| Commands           | `A13N_ENVD_FULL_CONTROL=true` enables the native shell                      |
| Sudo               | Standard grants `sandbox` passwordless sudo; slim does not install sudo     |
| Egress             | Standalone daemon default is `inherit`; no destination filtering is implied |
| Working directory  | `/workspace`                                                                |
| Installation state | `A13N_ENVD_STATE_DIR=/var/lib/a13n-envd`                                    |
| Standalone runtime | `A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state`                                |

Environment overrides work without replacing the default command. For example, disable privilege gains for Envd workers and their descendants:

```bash
docker run --rm -i \
  -e A13N_ENVD_ALLOW_SUDO=false \
  a13n-sandbox:local
```

This blocks worker setuid/file-capability privilege gains, including native sudo; it does not change the daemon's root launch identity or restrict a Docker administrator. Set `A13N_ENVD_FULL_CONTROL=false` for file-only use. To run as another account, provision that account in a derived image, set both execution UID/GID values, and arrange its filesystem permissions. Do not use Docker `--user` to change only the daemon identity while leaving incompatible execution IDs or root-owned state paths.

Image environment values take precedence over daemon JSON. Override the corresponding environment variables or use command-line options when changing these defaults. See [configuration precedence and native identity](configuration.md).

### Opt-in controlled egress

Controlled egress requires **both** a prepared deployment and a per-Session request:

1. Select a compatible Linux runtime and grant the [required namespace/kernel facilities](egress.md). Root inside an ordinary Docker container is not sufficient.
2. Set `A13N_ENVD_EGRESS_MODE=controlled` at daemon startup.
3. Have the trusted Host include an `egress` policy in `session.open`.

The image does not grant itself Docker capabilities, auto-elevate or silently fall back. Missing facilities can fail daemon startup or Session preparation. A Session supplying an egress policy to the default inherit-mode daemon is rejected, not executed without its policy. A controlled-mode daemon also rejects Sessions that omit their policy. No general-purpose `--privileged` launch is required for the default inherit-mode image.
