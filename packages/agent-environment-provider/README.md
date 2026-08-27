# Agent Environment Provider

`a13n-environment-provider` is the shared Host-facing Environment provider library for Agent Foundation agents. The repository directory is `packages/agent-environment-provider`, the Python distribution is `a13n-environment-provider`, and the import package is `a13n_environment_provider`.

The package owns:

- credential-free `EnvironmentProviderSpec` envelopes and exact versioned provider configuration;
- explicitly selected built-in and extension factory catalogs;
- Host-facing `EnvironmentManager` lifecycle and reconciliation contracts;
- sensitive provider-owned resource-state envelopes;
- single-entry managed-resource scopes and fresh runtime attachments;
- typed provider errors with bounded safe projections;
- the working `a13n.direct-local` built-in;
- EIP attachment and session-source values shared with future managed sandbox providers.

The package does not own durable storage, Host authorization or scheduling, Harness runs, model-facing tools, or provider-neutral Environment operations. A Host selects and manages a provider resource, keeps any provider state, and transfers a fresh attachment to `a13n-harness`. The Harness adapts that attachment into one single-use Environment binding.

Direct Local is a logical access scope over an existing Host directory. It never creates, deletes, tags, locks, or claims ownership of that directory. Local Envd and Docker are intentionally deferred until their working Block 9 implementations; E2B is deferred until Block 10. The catalog contains no placeholder factories or fallback provider selection.

See the [Environment Provider guide](../../docs/agent-environment-provider/index.md) for Host orchestration and third-party plugin development.

## Versioning

Agent Environment Provider, `a13n-harness`, and `a13n-stream-protocol` form the Harness release group. A `release/harness-v<version>` tag publishes all three distributions at exactly the same version, where `<version>` is stable `X.Y.Z` or RC `X.Y.Z-rc.N`. Python package metadata represents the RC as `X.Y.ZrcN`.

The accepted architecture and compatibility contract are defined in the [Agent Environment Provider specification](../../spec/agent-environment-provider/README.md).
