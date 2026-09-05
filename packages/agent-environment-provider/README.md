# Agent Environment Provider

`a13n-environment-provider` is the shared Host-facing Environment provider library for Agent Foundation. The repository directory is `packages/agent-environment-provider`, the Python distribution is `a13n-environment-provider`, and the import package is `a13n_environment_provider`.

The package owns:

- credential-free `EnvironmentProviderSpec` envelopes and exact versioned configuration validation;
- metadata-only installed Provider discovery and immutable built-in, extension, and explicit-object catalogs;
- inert `EnvironmentProvider` plugins that construct fresh adapters;
- single-use `Environment` adapters with creation or re-entry, readiness, provider-neutral operations, cached state, non-destructive close, and explicit destruction;
- portable provider-owned `EnvironmentState` soft references;
- typed Provider errors with bounded safe projections;
- Native Direct Local/E2B and Envd Local/Docker/HTTP/WebSocket built-ins;
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

Direct Local exposes an existing Host directory and never deletes, tags, locks, or claims ownership of it. Local Envd launches one compatible Host-selected `agent-envd` generation for each fresh adapter and removes only its private runtime on close. Docker creates or re-enters one exact local container, exposes authenticated HTTP EIP operations, and preserves the container on close; explicit destruction removes the container and its Provider-owned bootstrap material. Docker never uses exec, archive, copy, or logs for Harness operations. E2B implements native SDK operations with bounded command-local byte capture, state re-entry, pause/resume, keepalive and explicit destruction; it requires no envd installation or custom template. The catalog contains no placeholder or fallback selection.

## Choose a Provider

| Route  | Provider                    | Use it for                               | Operation and ownership boundary                          |
| ------ | --------------------------- | ---------------------------------------- | --------------------------------------------------------- |
| Native | `a13n.direct-local`         | Trusted local automation                 | Host OS operations; existing directory, no sandbox claim  |
| Native | `a13n.e2b`                  | Native managed cloud sandbox             | E2B SDK; sandbox create/pause/resume/renew/destroy        |
| Envd   | `a13n.local-envd`           | CLI and local Agents                     | Private stdio daemon; close preserves workspace           |
| Envd   | `a13n.docker` (Docker Envd) | Small single-node self-hosted services   | Docker lifecycle plus HTTP EIP; close preserves container |
| Envd   | `a13n.http-envd`            | Network-reachable external environments  | HTTP(S) EIP; connect-only                                 |
| Envd   | `a13n.websocket-envd`       | Environments that connect back to a Host | Reverse WebSocket EIP; Host-integrated SDK, connect-only  |

See [Remote Envd](../../docs/agent-environment-provider/remote-envd.md) for one-command local demos, external HTTP connection and Host-owned reverse WebSocket integration. The SDK starts no listener; close preserves the external daemon and workspace.

## Docker development

The default Docker recipe is empty; the Host passes its instance ID separately to `create_environment()`. It uses `ghcr.io/converge-ai-labs/agent-foundation-sandbox:latest`, pulls when the image is missing, exposes the container-backed `/workspace` virtual mount, and enables Bash. Hosts supply a local Engine adapter and a bootstrap store rooted at a Host-selected directory:

```python
from pathlib import Path

from a13n_environment_provider import (
    DirectoryDockerBootstrapStore,
    DockerProviderRuntime,
    DockerSDKEngine,
)

runtime = DockerProviderRuntime(
    engine=DockerSDKEngine.from_env(),
    bootstrap_store=DirectoryDockerBootstrapStore(Path("/var/lib/my-host/docker-bootstrap")),
)
```

The Engine client uses ordinary Host Docker authentication, credential-helper, mirror, and proxy configuration. Bind mounts and existing named volumes are optional Host overrides; named volumes remain externally owned. Run the focused real-image lifecycle test with:

```bash
make docker-provider-test
```

## Local Envd development

Hosts select the daemon executable once when constructing `LocalEnvdProviderRuntime`. The convenience resolver uses this precedence:

1. an explicit path passed to `resolve_agent_envd_executable()`;
2. `A13N_AGENT_ENVD_EXECUTABLE`;
3. `agent-envd` (or `agent-envd.exe`) discovered through `PATH` with `shutil.which()`.

The resolver expands path values, resolves them against the caller's current directory, validates one executable regular file, and returns an absolute path. The package never reads `.env`, changes `PATH`, installs a daemon, or rediscovers the executable after runtime construction.

```python
from a13n_environment_provider import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_agent_envd_executable,
)

runtime = LocalEnvdProviderRuntime(
    executable=resolve_agent_envd_executable(),
    allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
)
```

Repository developers can optionally copy the `A13N_AGENT_ENVD_EXECUTABLE` entry from `.env.harness.example` into the root `.env`. The focused target builds the source-tree daemon, loads `.env` only inside the target shell, defaults the variable to `target/debug/agent-envd`, and runs the real Local Envd tests:

```bash
make local-envd-test
```

See the [Environment Provider guide](../../docs/agent-environment-provider/index.md) for Harness usage, state re-entry, explicit destruction, and third-party plugin development. The runnable [built-in Provider example](../../examples/environment-provider/README.md) exercises Direct Local, Local Envd, and Docker from Host code.

## Versioning

Agent Environment Provider, `a13n-harness`, and `a13n-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`.

The accepted architecture and compatibility contract are defined in the [Agent Environment Provider specification](../../spec/agent-environment-provider/README.md).
