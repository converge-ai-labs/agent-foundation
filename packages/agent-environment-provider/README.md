# Agent Environment Provider

`a13n-environment-provider` is the shared Host-facing Environment provider library for Agent Foundation. The repository directory is `packages/agent-environment-provider`, the Python distribution is `a13n-environment-provider`, and the import package is `a13n_environment_provider`.

The package owns:

- credential-free `EnvironmentProviderSpec` envelopes and exact versioned provider configuration;
- explicitly selected built-in and extension factory catalogs;
- Host-facing `EnvironmentProvider` lifecycle, reconciliation, and `ephemeral()` contracts;
- sensitive provider-owned `EnvironmentProviderResourceState` envelopes;
- single-entry `EnvironmentResource` scopes and fresh runtime attachments;
- typed provider errors with bounded safe projections;
- the working `a13n.direct-local` and `a13n.local-envd` built-ins;
- EIP attachment and reusable stdio-carrier values that publish only initialization- and readiness-confirmed Sessions and are shared with managed sandbox providers.

The source type passed to Agent Harness defines ownership:

```python
# Harness creates, enters, attaches, exits, and destroys one temporary Resource.
result = await executable.run("Use a temporary workspace", environment=provider)

# Host keeps an already entered Resource; Harness borrows one fresh attachment.
async with resource:
    first = await executable.run("Start", environment=resource)
    second = await executable.run("Continue", environment=resource, previous_state=first.state)
```

The package does not own durable storage, Host authorization or scheduling, Harness runs, model-facing tools, or provider-neutral Environment operations. A Host explicitly manages reusable resources and persists current provider state. The Harness owns a Provider input only through the bounded ephemeral lifecycle.

Direct Local is a logical access scope over an existing Host directory. It never creates, deletes, tags, locks, or claims ownership of that directory. Local Envd launches one compatible Host-selected `agent-envd` process per entered Resource, validates startup through a provider-owned short-lived EIP readiness Session, and then uses readiness-confirmed sequential EIP Sessions over its private reusable stdio carrier. It uses no filesystem readiness marker or separate health probe. Docker and E2B remain deferred to their planned provider phases. The catalog contains no placeholder factories or fallback provider selection.

## Local Envd development

Hosts select the daemon executable once when constructing `LocalEnvdProviderRuntime`. The convenience resolver uses this precedence:

1. an explicit path passed to `resolve_agent_envd_executable()`;
2. `A13N_AGENT_ENVD_EXECUTABLE`;
3. `agent-envd` (or `agent-envd.exe`) discovered through `PATH` with `shutil.which()`.

The resolver expands path values, resolves them against the caller's current directory, validates one executable regular file, and returns an absolute path. The package never reads `.env`, changes `PATH`, installs a daemon, or rediscovers the executable after Provider construction.

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

See the [Environment Provider guide](../../docs/agent-environment-provider/index.md) for high-level Harness usage, Host orchestration, lifecycle reconciliation, and third-party plugin development.

## Versioning

Agent Environment Provider, `a13n-harness`, and `a13n-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`.

The accepted architecture and compatibility contract are defined in the [Agent Environment Provider specification](../../spec/agent-environment-provider/README.md).
