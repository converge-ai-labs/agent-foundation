---
title: Sandbox image
description: Choose an image, connect it to a Host, and preserve your workspace between containers.
---

Choose the standard `a13n-sandbox` image for development and browser automation, the desktop image for Computer Use, or slim as a minimal Envd runtime.

| Variant            | Contents                                                                           | Intended use                                                                               |
| ------------------ | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Standard (default) | Envd, the development toolchain, agent-browser, Chromium, fonts and bundled skills | Agent development through Envd or the native Docker Provider                               |
| Desktop            | Standard toolchain plus Xvfb, Openbox and supervised desktop startup               | Computer Use through an image-managed Envd                                                 |
| Slim               | Envd, Debian's basic shell/utilities, runtime libraries, CA certificates and tini  | Envd operation or a base for a custom image; no Python, Node.js, compiler, sudo or browser |

All variants launch Envd as root and execute Session commands and file operations as `sandbox` (`1000:1000`). Standard and desktop grant that account passwordless sudo, so the outer container or VM supplies the isolation boundary. Slim needs additional Python for the native Docker Provider and additional network tools for controlled egress.

Start with the default `inherit` egress mode. Ordinary use needs no `--privileged`, extra capabilities or disabled Docker security profiles. The Linux runtime must support Envd's native process operations; custom runtimes and reduced capability sets can reject them. For destination filtering, follow [controlled egress](#opt-in-controlled-egress).

## Build and check

Run the build and smoke-check pair for your chosen variant from the repository root:

```bash
make image-sandbox
make image-check-sandbox
make image-sandbox-slim
make image-check-sandbox-slim
make image-sandbox-desktop
make image-check-sandbox-desktop
```

These produce `a13n-sandbox:local`, `a13n-sandbox:local-slim` and `a13n-sandbox:local-desktop`. Smoke checks cover daemon startup, identity and directory permissions. Standard checks also exercise the toolchain, sudo, skill initialization and offline browser snapshots/screenshots. Container CI runs on native AMD64 and ARM64; the Envd suite owns protocol and execution-isolation tests.

To use a published image, select a tag under `ghcr.io/converge-ai-labs/a13n-sandbox` matching your EIP client's Envd release. All three variants share the daemon version:

| Channel           | Standard          | Slim                 | Desktop                    |
| ----------------- | ----------------- | -------------------- | -------------------------- |
| Stable release    | `X.Y.Z`, `latest` | `X.Y.Z-slim`, `slim` | `X.Y.Z-desktop`, `desktop` |
| Release candidate | `X.Y.Z-rc.N`      | `X.Y.Z-rc.N-slim`    | `X.Y.Z-rc.N-desktop`       |
| Main development  | `dev`             | `dev-slim`           | `dev-desktop`              |

`latest`, `slim` and `desktop` track stable releases; RC and development publication leave them unchanged. The behavior below describes this checkout's images. When using an older tag, check that release's features and defaults.

## Managed Docker desktop in Service

Prepare the Docker connection, then create a desktop environment:

1. Build `a13n-sandbox:local-desktop` on the selected Docker Engine, or choose a compatible published desktop tag.
2. Add a Docker connection in Console. Configure Engine access under **Container management**. Enable **Environment operation channel** and set **Host access address** to a hostname or IP reachable by every Service/Worker.
3. For a local process using Docker Desktop, use `127.0.0.1` for the access and listen addresses. For a remote Engine, configure its reachable private address, listen interface and trusted plaintext private-link opt-in.
4. Select **Desktop environment** in the template editor. This preset fixes the desktop image and HTTP EIP execution together. **Standard environment** selects the standard image and native Docker execution. Both allow CPU, memory and mount settings.
5. For a local build or your own image, select **Custom image**. Set image `a13n-sandbox:local-desktop`, execution `envd_http`, **Desktop capability** (`desktop: true`) and `pull_policy: never` for the local build. Keep networking enabled. The image starts the desktop and Envd without an initialization script.
6. Create the environment. Service publishes Envd's port, discovers the Docker-assigned host port, authenticates the device and checks desktop readiness. Enable the agent's Computer tools and the mount's required Computer operations before starting the task.

For a trusted private network, the Docker connection can use:

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

Allow every relevant Service/Worker to reach the ephemeral host port mapped to container port 8888. Protect Docker API transport separately. Envd HTTP carries a bearer credential over the trusted private route, with no TLS termination supplied by this path; keep it off the public Internet. Deployment outbound policy also checks the resolved endpoint. A Worker outside the host network needs an inbound route to that host; this connection does not provide a reverse WebSocket tunnel.

The image creates a private random token at `/var/lib/a13n-envd/http-token` (mode 0600), or the configured `credential_file`. The provider retrieves it through the authorized Engine connection. Each attachment rechecks the retained container's port mapping and expected device ID, then sends Agent operations directly over HTTP EIP. Tokens stay out of template JSON, public instance state and API responses. Custom images can implement the same startup contract, including headless `envd_http` use.

For lifecycle management:

- Inspect the retained container when readiness fails.
- Close a Run to detach while keeping the container running.
- Stop and start to preserve files and credentials while creating a fresh graphical session.
- Stop or destroy to revoke access. To rotate a token, stop the container, replace its private token file, close existing sessions and restart.

The root desktop launcher supervises Xvfb/Openbox running as `sandbox`, creates an Xauthority cookie and disables X11 TCP listening. A required desktop process exiting fails the container. X11 supports key events; literal Unicode `type_text` is unavailable.

Human access is a separate opt-in. Set `desktop_assistance: true` and provide a browser-reachable `desktop_viewer_url` routed to the container's port 6081. Service enables the image's `A13N_DESKTOP_ACCESS` supervisor; the operator supplies the viewer route and TLS. Follow [desktop assistance](../a13n-service/environments.md#agent-requested-desktop-assistance) for permissions and the handback workflow.

## Run with a Host

### Stdio

Configure your EIP Host to launch this command with piped stdin and stdout:

```bash
docker run --rm -i a13n-sandbox:local
```

Keep `-i` and omit `-t` so the pipes carry EIP frames without terminal processing. The entrypoint initializes workspace skills, then starts Envd under tini. Envd waits for the EIP client while stdin stays open and exits when stdin closes. Without `-i`, an immediate clean exit is expected.

Mount a Docker-managed volume at the default working directory to preserve files after container removal:

```bash
docker volume create agent-workspace
docker run --rm -i \
  --mount type=volume,source=agent-workspace,target=/workspace \
  a13n-sandbox:local
```

A new empty volume inherits the image directory's ownership. For an existing volume or bind mount, arrange write access for `1000:1000`; startup preserves existing ownership. Session closure keeps workspace files. Container removal discards files outside volumes and bind mounts.

### Connect to Harness UI

Use separate volumes for Device credentials and workspace files, then connect outward to Harness UI:

```bash
docker volume create agent-envd-state
docker volume create agent-workspace
docker run --rm --name agent-sandbox \
  --mount type=volume,source=agent-envd-state,target=/var/lib/a13n-envd \
  --mount type=volume,source=agent-workspace,target=/workspace \
  a13n-sandbox:local \
  a13n-envd connect https://host.example.com --host work
```

1. Replace the URL with an address reachable from inside the container. Container `localhost` refers to the container itself.
2. Approve the printed verification code at the Host and keep the container running. Outbound connection mode needs no Docker port publishing.
3. Reuse the state volume on subsequent starts to retain the Device identity and credential. Keep that credential volume exclusive to this sandbox.

See [registration and saved Hosts](configuration.md#connect-to-harness-ui). For Service, run an HTTP daemon and [register an external target](../environments/remote-envd.md#connect-to-the-service).

Installation state lives in `/var/lib/a13n-envd`. Standalone generation-private runtime data uses `/run/a13n-envd-state`; `connect` keeps Host-specific runtime data under the installation directory. Preserve work through the workspace volume, not these runtime directories.

## Workspace skills and browser automation

The standard image builds in a pinned agent-browser CLI, system Chromium and bundled skills. `AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium` selects Chromium on both architectures. Run browser commands as `sandbox`; startup uses the installed assets without downloading them.

At startup, `a13n-sandbox-init` copies missing skill directories from `/usr/local/share/a13n/skills` into `.agents/skills` under the container's initial cwd, normally `/workspace`. The entrypoint waits for initialization, then uses `exec` to start your command. Use Docker `--workdir` to seed another existing writable directory. Changing an Envd Session cwd later does not seed that directory.

Existing names, including files and symlinks, remain untouched. Image upgrades therefore preserve workspace skills; explicitly remove an old skill directory when you want it seeded again. Failed copies never appear as partial skills. An unwritable destination produces a stderr warning and startup continues. Keeping initialization output on stderr preserves the stdio protocol channel.

For another execution account, set `A13N_ENVD_EXECUTION_UID` and `A13N_ENVD_EXECUTION_GID` consistently. The root initializer switches to those IDs before writing; non-root callers keep their identity. The initializer reads these environment variables, not Envd JSON or CLI options, and preserves mounted-file ownership. Set `A13N_SANDBOX_SKILLS_DIR` to select another bundled source.

Replacing CMD preserves initialization. Replacing ENTRYPOINT bypasses it, so call the initializer explicitly in a custom launcher:

```bash
# Bypass the standard entrypoint and initialize explicitly as the workload user.
docker run --rm --user sandbox --entrypoint sh a13n-sandbox:local \
  -c 'a13n-sandbox-init && agent-browser skills get core'
```

The native Docker Provider replaces the entrypoint. Add this recipe setting to seed skills:

```yaml
init_script: a13n-sandbox-init
```

With the Skills capability enabled, Harness UI discovers `.agents/skills` in the selected Device working directory while preparing a Run. The startup initializer supplies those files; Envd and the Provider do not otherwise install them.

## Everyday development

The standard toolchain includes Python 3.13 with pip/venv, uv, Node.js 24, npm, pnpm, C/C++ build tools, Bash, Git, curl, SSH client, jq, ripgrep, patch, zip/unzip, process tools, sudo and CA certificates. `sandbox` can write `/workspace` and `/tmp/a13n`. The image also installs userspace namespace/network tools for controlled egress; the deployment supplies kernel permissions. Published images support `linux/amd64` and `linux/arm64`.

For native Docker execution, the Provider replaces the daemon entrypoint with a keepalive and uses Docker exec for files and shell. The image label `ai.a13n.environment.user=sandbox` selects the execution user unless the recipe sets `user`. This path runs without Envd or an EIP listener.

For EIP execution, commands start as `sandbox` with `HOME=/home/sandbox`. Install a system dependency with sudo and Python dependencies in a virtual environment:

```bash
id -u                         # 1000
sudo -n id -u                 # 0
sudo -n apt-get update
sudo -n apt-get install -y sqlite3
python3 -m venv .venv
.venv/bin/python -m pip install requests
```

System-package changes last for that container. Put repeatable dependencies in a derived image, and configure Git author identity and SSH credentials for your workload. File RPCs continue as the unprivileged account after sudo commands; adjust permissions or ownership on root-created files before editing them through RPCs.

To debug a running container as the workload account:

```bash
docker exec -it --user sandbox agent-sandbox bash
```

Direct Docker exec uses Docker's authority, outside Envd's identity, sudo and egress policies. Omitting `--user sandbox` selects the image's root launch account.

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

Pass environment overrides while keeping the default command. For example, block privilege gains by Envd workers and their descendants:

```bash
docker run --rm -i \
  -e A13N_ENVD_ALLOW_SUDO=false \
  a13n-sandbox:local
```

This disables setuid/file-capability privilege gains, including native sudo. The daemon still launches as root, and Docker administrators retain their authority. For file-only use, also set `A13N_ENVD_FULL_CONTROL=false`.

To use another account, provision it in a derived image, set both execution IDs and arrange its filesystem permissions. Changing only Docker `--user` can leave incompatible execution IDs or root-owned state paths.

Image environment values override daemon JSON. Change the corresponding environment variables or supply CLI options; see [configuration precedence and native identity](configuration.md).

### Opt-in controlled egress

Enable destination filtering at both deployment and Session levels:

1. Prepare a compatible Linux runtime with the [required namespace/kernel facilities](egress.md). Ordinary container root is insufficient.
2. Start Envd with `A13N_ENVD_EGRESS_MODE=controlled`.
3. Have the trusted Host include an `egress` policy in `session.open`.

Missing runtime facilities can fail startup or Session preparation. Envd rejects mismatched requests: `inherit` mode rejects a supplied policy, and `controlled` mode requires one. The image neither elevates itself nor falls back to unfiltered execution.
