---
title: Extend the Provider catalog
sidebarTitle: Provider plugins
description: Select trusted Provider plugins or publish a fixed-target Environment execution adapter.
---

Use this guide when your Host assembles a Provider catalog or you are implementing a Provider. For built-in runtime setup, return to [Providers and runtime](providers.md).

## Provider catalog and plugins

Use `ProviderCatalog` to allow exactly the definitions your Host supports. The same immutable catalog works across Model, Web, Connector, Memory, and Environment domains.

After installing a plugin that publishes entry point `acme` and Environment type `acme_sandbox`, load it alongside selected built-ins:

```python
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_harness.providers.plugins import load_provider_plugins

plugins = load_provider_plugins(("acme",))
catalog = ProviderCatalog(
    (
        *select_builtin_environment_providers(("direct_local", "docker")),
        *(item for plugin in plugins for item in plugin.manifest.environment),
    )
)
definition = catalog.require("acme_sandbox")
```

The loader imports only named entry points; an empty selection scans no installed metadata. It reports distribution/version/import provenance and rejects duplicate, malformed, missing, or ambiguous names. The catalog also rejects duplicate Provider types, so plugins cannot shadow built-ins. Restart the Host after changing plugin code.

To publish your own plugin, add this entry point to the distribution's `pyproject.toml`:

```toml
[project.entry-points."a13n_harness.providers.plugins"]
acme = "acme_agent_environment:manifest"
```

In `acme_agent_environment`, export a manifest containing your previously defined `ACME_SANDBOX`:

```python
from a13n_harness.providers.plugins import ProviderManifest

manifest = ProviderManifest(api_version=1, environment=(ACME_SANDBOX,))
```

Build the Environment definition in this order:

1. Choose a stable `type`, display name, and optional HTTPS setup link.
2. Define separate models for account configuration, credentials, and target recipes.
3. Supply execution metadata. Compose a separate Service `EnvironmentManagementDefinition` for management.
4. Implement a pure `connector_factory` returning a fixed-target `EnvironmentConnector`. Accept the always-supplied `session_recovery` keyword, including `None`; Native implementations accept it without producing Session hints.
5. Make every `open()` return a fresh ready `EnvironmentExecution`, with its own `execution_id` and operations.
6. Validate target references without changing authoritative state. Leave create, start, replace, renew, and destroy to management, outside execution open/check/close.

Follow the runnable [Provider plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) and [shared plugin contract](../a13n-harness/plugins.md#provider-plugins). For complete built-in management, state re-entry, and destruction, return to [Provider examples](examples.md).
