---
title: Sandbox image
description: A ready-to-use Linux development container for agents, with Envd and a sudo-capable sandbox account.
---

In the `a13n-sandbox` image, Envd starts as root; Session commands and file operations run as the provisioned `sandbox` account (`1000:1000`). That account has passwordless sudo for installing packages and administering the disposable container. The outer container or VM supplies isolation; the default UID is not a barrier against intentional root access.

**Controlled egress is off by default.** Ordinary use requires no `--privileged`, extra capabilities, or disabled Docker security profiles. Envd still requires a Linux runtime supporting its native process operations; a custom runtime or reduced capability set can reject them.

## Build and check

From the repository root:

```bash
make image-sandbox
make image-check-sandbox
```

The local tag is `a13n-sandbox:local`. The image smoke check verifies startup defaults, the `sandbox` account's directory permissions and passwordless sudo, and daemon startup under Docker's default permissions. Protocol and execution-isolation tests remain in the Envd test suite.

Published images use `ghcr.io/converge-ai-labs/a13n-sandbox`. Select the Envd release tag matching your EIP client; `dev` tracks main, stable releases use `X.Y.Z`, and RCs use `X.Y.Z-rc.N` without advancing `latest`. These instructions describe the source image in this checkout, not a promise that an older published tag already has these defaults.

## Run with a Host

### Stdio

Have your EIP Host launch this command with piped stdin and stdout:

```bash
docker run --rm -i a13n-sandbox:local
```

Keep `-i`, and do not add `-t`: stdin/stdout carry framed EIP traffic, not an interactive terminal. The default command is `tini -- a13n-envd`; it is not a shell, HTTP listener or idle container. It waits for an EIP client while stdin remains open and exits when stdin closes. Running it without `-i` and seeing a clean exit is expected.

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

## Everyday development

The image includes Python 3.13 with pip/venv, uv, Node.js 24, npm, pnpm, C/C++ build tools, Bash, Git, curl, SSH client, jq, ripgrep, patch, zip/unzip, process tools, sudo and CA certificates. `/workspace` and `/tmp/a13n` are writable by `sandbox`. It also installs the userspace namespace/network tools needed by opt-in egress; installing them does not grant kernel privileges. Add project-specific dependencies with sudo or a derived image. Published sandbox images support `linux/amd64` and `linux/arm64`.

The same image supports the native Docker Environment Provider. That provider replaces the daemon entrypoint with its keepalive and uses Docker exec for file and shell operations. The image label `ai.a13n.environment.user=sandbox` selects the non-root container user unless a recipe explicitly overrides `user`; standalone Envd still starts as root and launches EIP operations as `sandbox`. Native Docker does not expose an EIP listener or start Envd.

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

| Setting            | Image behavior                                                                                     |
| ------------------ | -------------------------------------------------------------------------------------------------- |
| Launcher           | Root, supervised by tini                                                                           |
| Execution identity | `A13N_ENVD_EXECUTION_UID=1000`, `A13N_ENVD_EXECUTION_GID=1000`                                     |
| Commands           | `A13N_ENVD_FULL_CONTROL=true` enables the native shell                                             |
| Sudo               | Standalone daemon default allows privilege gains; image sudoers grants `sandbox` passwordless sudo |
| Egress             | Standalone daemon default is `inherit`; no destination filtering is implied                        |
| Working directory  | `/workspace`                                                                                       |
| Installation state | `A13N_ENVD_STATE_DIR=/var/lib/a13n-envd`                                                           |
| Standalone runtime | `A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state`                                                       |

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
