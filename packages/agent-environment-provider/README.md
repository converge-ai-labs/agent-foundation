# Agent Environment Provider

`a13n-environment-provider` is the shared Host-facing Environment provider library for Agent Foundation. The repository directory is `packages/agent-environment-provider`, the Python distribution is `a13n-environment-provider`, and the import package is `a13n_environment_provider`.

The package owns:

- credential-free `EnvironmentProviderSpec` envelopes and exact versioned provider configuration;
- explicitly selected built-in and extension factory catalogs;
- Host-facing `EnvironmentProvider` lifecycle, reconciliation, and `ephemeral()` contracts;
- sensitive provider-owned `EnvironmentProviderResourceState` envelopes;
- single-entry `EnvironmentResource` scopes and fresh runtime attachments;
- typed provider errors with bounded safe projections;
- the working `a13n.direct-local` built-in;
- EIP attachment and session-source values shared with managed sandbox providers.

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

Direct Local is a logical access scope over an existing Host directory. It never creates, deletes, tags, locks, or claims ownership of that directory. Local Envd and Docker are intentionally deferred until their working implementations; E2B is deferred to its planned provider phase. The catalog contains no placeholder factories or fallback provider selection.

See the [Environment Provider guide](../../docs/agent-environment-provider/index.md) for high-level Harness usage, Host orchestration, lifecycle reconciliation, and third-party plugin development.

## Versioning

Agent Environment Provider, `a13n-harness`, and `a13n-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`.

The accepted architecture and compatibility contract are defined in the [Agent Environment Provider specification](../../spec/agent-environment-provider/README.md).
