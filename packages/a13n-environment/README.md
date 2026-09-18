# a13n Environment

`a13n-environment` is the shared Host-facing Environment provider library for Agent Foundation. The repository directory is `packages/a13n-environment`, the Python distribution is `a13n-environment`, and the import package is `a13n_environment`.

The package owns:

- credential-free `EnvironmentProviderSpec` envelopes and exact versioned configuration validation;
- metadata-only installed Provider discovery and immutable built-in, extension, and explicit-object catalogs;
- inert `EnvironmentProvider` plugins that construct fresh adapters;
- single-use `Environment` adapters with creation or re-entry, readiness, provider-neutral operations, cached state, non-destructive close, and explicit destruction;
- portable provider-owned `EnvironmentState` soft references;
- typed Provider errors with bounded safe projections;
- Native Direct Local/Docker and six cloud providers (E2B, Daytona, Modal, Vercel Sandbox, Fly.io Sprites, and Runloop), plus Envd Local/HTTP/WebSocket built-ins;
- EIP session sources and reusable stdio-carrier values shared with managed sandbox Providers.

Embedding code resolves a trusted Provider, validates configuration, supplies current state and fresh runtime collaborators, and constructs one new adapter per independent Harness Run:

```python
configuration = provider.validate_configuration(
    schema_version=spec.schema_version,
    value=spec.configuration,
)
environment = provider.create_environment(
    configuration=configuration,
    environment_id="workspace",
    state=current_state,
    runtime=fresh_runtime,
)

try:
    result = await executable.run("Use the workspace", environment=environment)
finally:
    current_state = environment.dump_state()
```

Provider validation and adapter construction perform no external I/O. Harness enters and closes the adapter exactly once. `close()` releases process-local resources without deleting the target; Harness never calls `destroy()`. When Host retention policy selects removal, the Host constructs a separate fresh adapter from the exact current state and calls `destroy()` explicitly.

The package does not own durable storage, Host authorization or scheduling, Harness Runs, model-facing tools, mount names, access ceilings, or target retention policy. The Host persists authoritative `EnvironmentState`; Harness owns only Run-local aggregate routing and state mapping.

Direct Local exposes an existing Host directory and never deletes, tags, locks, or claims ownership of it. Local Envd launches one compatible Host-selected `a13n-envd` generation for each fresh adapter and removes only its private runtime on close. Docker creates or re-enters a native container, uses Engine exec for operations, and preserves the container and background commands on close. Explicit destruction removes its private filesystem and preserves external host mounts. The six cloud peers (E2B, Daytona, Modal, Vercel Sandbox, Fly.io Sprites, and Runloop) use native transports without envd. All support files, shell commands, and portable target state; process/output capabilities and stop/renewal guarantees are provider-specific. See the [cloud comparison](../../docs/a13n-environment/providers.md#cloud-providers). The catalog contains no placeholder or fallback selection.

## Choose a Provider

| Route  | Provider              | Use it for                               | Operation and ownership boundary                            |
| ------ | --------------------- | ---------------------------------------- | ----------------------------------------------------------- |
| Native | `direct-local`        | Trusted local automation                 | Host OS operations; existing directory, no sandbox claim    |
| Native | `e2b`                 | Native managed cloud sandbox             | E2B SDK; sandbox create/pause/resume/renew/destroy          |
| Native | `daytona`             | Cloud sandbox                            | Native stop/start and preserved files                       |
| Native | `modal`               | Cloud sandbox                            | Snapshot-backed stop/resume; fixed running lifetime         |
| Native | `vercel`              | Cloud sandbox                            | Named persistent sandbox with native sessions               |
| Native | `sprites`             | Cloud sandbox                            | Persistent disk and automatic sleep/wake                    |
| Native | `runloop`             | Cloud sandbox                            | Devbox suspend/resume and idle keepalive                    |
| Envd   | `a13n.local-envd`     | CLI and local Agents                     | Private stdio daemon; close preserves workspace             |
| Native | `docker`              | Small single-node self-hosted services   | Docker lifecycle and native exec; close preserves container |
| Envd   | `a13n.http-envd`      | Network-reachable external environments  | HTTP(S) EIP; connect-only                                   |
| Envd   | `a13n.websocket-envd` | Environments that connect back to a Host | Reverse WebSocket EIP; Host-integrated SDK, connect-only    |

See [Remote Envd](../../docs/a13n-environment/remote-envd.md) for one-command local demos, external HTTP connection and Host-owned reverse WebSocket integration. The SDK starts no listener; close preserves the external daemon and workspace.

## Docker development

The default configuration selects the Envd-free `ghcr.io/converge-ai-labs/a13n-docker-environment:dev` image. Each Environment has its own `/workspace`. Templates can add existing host-directory mounts; named volumes and bootstrap storage are not template options.

The Host supplies a Docker client through the runtime and owns its closure:

```python
import asyncio
from a13n_environment import DockerProviderRuntime, DockerSDKEngine

engine = await asyncio.to_thread(DockerSDKEngine.connect, "unix:///var/run/docker.sock")
runtime = DockerProviderRuntime(engine=engine)
```

Run unit tests with `make docker-provider-test`. Build the native image with `make image-docker-environment`, then run real lifecycle and operation checks:

```sh
make docker-provider-live-test
```

[Single-host Compose](../../deploy/compose/README.md) uses the host Docker Engine through its Unix socket. Configure external bind paths in that Engine's filesystem namespace.

## Local Envd development

Hosts select the daemon executable once when constructing `LocalEnvdProviderRuntime`. The convenience resolver uses this precedence:

1. an explicit path passed to `resolve_a13n_envd_executable()`;
2. `A13N_ENVD_EXECUTABLE`;
3. `a13n-envd` (or `a13n-envd.exe`) discovered through `PATH` with `shutil.which()`.

The resolver expands path values, resolves them against the caller's current directory, validates one executable regular file, and returns an absolute path. The package never reads `.env`, changes `PATH`, installs a daemon, or rediscovers the executable after runtime construction.

```python
from a13n_environment import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_a13n_envd_executable,
)

runtime = LocalEnvdProviderRuntime(
    executable=resolve_a13n_envd_executable(),
    allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
)
```

Repository developers can optionally copy the `A13N_ENVD_EXECUTABLE` entry from `.env.harness.example` into the root `.env`. The focused target builds the source-tree daemon, loads `.env` only inside the target shell, defaults the variable to `target/debug/a13n-envd`, and runs the real Local Envd tests:

```bash
make local-envd-test
```

See the [Environment guide](../../docs/a13n-environment/index.md) for Harness usage, state re-entry, explicit destruction, and third-party plugin development. The runnable [built-in Provider example](../../examples/environment-provider/README.md) exercises Direct Local, Local Envd, and Docker from Host code.

## Versioning

`a13n-environment`, `a13n-harness`, and `a13n-stream-protocol` form the Harness release group. A `release/a13n-harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`.

The accepted architecture and compatibility contract are defined in the [a13n Environment specification](../../spec/a13n-environment/README.md).
