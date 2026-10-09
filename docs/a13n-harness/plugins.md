---
title: Plugins and extensions
description: 'Choose the narrowest extension point: Harness middleware, Pydantic AI Capabilities, Environment provider bindings, Environment Run Extensions, or Provider plugins.'
---

Choose an extension by the behavior you want to add:

| Extension point              | Use it for                                             |
| ---------------------------- | ------------------------------------------------------ |
| Harness middleware plugin    | Wrap input, execution, errors, or the final Run result |
| Capability                   | Add tools, instructions, or Agent-loop behavior        |
| `EnvironmentProviderBinding` | Expose operations from one Host resource               |
| `EnvironmentRunExtension`    | Set up and clean up resources across entered mounts    |
| Provider plugin              | Make an Environment Provider available to a Host       |

Install the package, then explicitly select its extension. Installation alone activates nothing.

## Choose and Activate an Extension Point

Select each extension through its owning API:

| Extension point  | Selection                                                                             |
| ---------------- | ------------------------------------------------------------------------------------- |
| Middleware       | Pass a concrete plugin to `build()` or enable its configured `plugin_key`/`plugin_id` |
| Capability       | Add it to definition or Run composition; see [Capabilities](capabilities.md)          |
| Run extension    | Pass an instance to `create_environment_runtime(extensions=...)`                      |
| Provider binding | Add a fresh binding to `EnvironmentRuntimeMount`                                      |
| Provider plugin  | Select its entry-point name, then its Provider type in Host configuration             |

`AgentSpec` selects Capabilities. Configure middleware and Environment extensions separately.

## Harness Middleware

Use middleware to wrap a whole Run; use a [Capability](capabilities.md) for behavior inside the Agent loop.

A plugin can be supplied directly as an `AbstractHarnessPlugin` or created from a selected `HarnessPluginFactory` entry point.

### Wrap Execution and Emit Observations

Implement an async `wrap_run()` that returns a `HarnessRunResult`. Await `call_next(exchange)` at most once, or return a complete result to short-circuit execution. Use `exchange.with_input(...)` to replace semantic input.

```python
from typing import Any

from a13n_harness import AbstractHarnessPlugin, HarnessRunResult
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.plugins import PluginRunExchange, PluginRunNext


class AuditPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    async def wrap_run(
        self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]
    ) -> HarnessRunResult[Any]:
        await exchange.context.events.emit(
            HarnessExtensionEvent(kind="diagnostic", payload={"phase": "before"})
        )
        result = await call_next(exchange)
        await exchange.context.events.emit(
            HarnessExtensionEvent(kind="diagnostic", payload={"phase": "after"})
        )
        return result
```

The Run delivers extension events through a bounded channel while middleware executes. Emission works before the continuation, after it, and during short-circuit execution. A slow consumer applies backpressure; middleware does not own or iterate the event stream.

**Breaking migration:** replace `PluginRunResponse` and async event generators with `async def wrap_run(...)`, `await call_next(...)`, and a returned result. Emit extension observations through `context.events.emit()`. Native events and Run lifecycle events remain Harness-owned. For presentation changes, configure the Host's Stream Protocol `DisplayFold` processor before its shared live-and-persisted fold; use the same processor when restoring the fold. Do not rewrite native events in middleware.

### Direct Composition

Direct objects are the simplest choice for an embedded application:

```python
from a13n_harness import HarnessBuilder

plugin = AuditPlugin("audit-primary")
executable = HarnessBuilder().build(
    agent_spec,
    output_type=str,
    model=model,
    plugins=(plugin,),
)
```

### Feature Capabilities and Current Provider Clients

Select the feature Capability on the definition and supply current clients through Run bindings:

```python
from a13n_harness import RunBindings
from a13n_harness.capabilities import WebBinding

result = await executable.run(
    "Read the page",
    bindings=RunBindings.embedded(
        web=WebBinding(client=web_client, policy=web_policy),
    ),
)
```

Keep `WebCapability` on the definition, not in plugin contributions. The Host owns provider clients and their lifetime; clients are not saved in `HarnessState`.

The [offline middleware and Web example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins#middleware-and-feature-with-host-bindings) exercises a recorder plugin alongside a definition-owned Web feature, fresh typed bindings, and the standard `fetch` tool without a network request.

### Publish a Plugin Factory

Register one no-argument factory class:

```toml
[project.entry-points."a13n_harness.plugins"]
"acme.audit" = "acme_harness.plugin:AuditPluginFactory"
```

The entry-point name and `plugin_key()` must match:

```python
from collections.abc import Mapping

from a13n_harness import AbstractHarnessPlugin
from a13n_harness.plugin_factories import (
    HarnessPluginFactory,
    HarnessPluginFactoryContext,
)
from pydantic import BaseModel, JsonValue


class AuditConfiguration(BaseModel):
    mode: str = "metadata"


class AuditPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str) -> None:
        self._plugin_id = plugin_id

    @property
    def plugin_id(self) -> str:
        return self._plugin_id


class AuditPluginFactory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls) -> str:
        return "acme.audit"

    def validate_configuration(
        self,
        configuration: Mapping[str, JsonValue],
    ) -> BaseModel:
        return AuditConfiguration.model_validate(dict(configuration))

    def create_plugin(
        self,
        context: HarnessPluginFactoryContext,
    ) -> AbstractHarnessPlugin:
        # context.configuration holds the validated, normalized configuration.
        return AuditPlugin(context.plugin_id)
```

Validate configuration in `validate_configuration()` before creating a fresh plugin. Keep factory construction free of I/O and mutable Run data in `for_run()`.

### Select Configured Plugins

The Harness plugin document is a data schema, not a required file format:

```python
from a13n_harness import HarnessBuilder
from a13n_harness.plugin_configuration import HarnessBuildContext

configuration = {
    "schema_version": "1",
    "plugins": [
        {
            "plugin_id": "audit-primary",
            "plugin_key": "acme.audit",
            "enabled": True,
            "configuration": {"mode": "metadata"},
        }
    ],
}

context = HarnessBuildContext.from_configuration(configuration)
builder = HarnessBuilder(build_context=context)
```

The same schema can come from YAML or JSON:

```yaml
schema_version: "1"
plugins:
  - plugin_id: audit-primary
    plugin_key: acme.audit
    enabled: true
    configuration:
      mode: metadata
```

```python
context = HarnessBuildContext.from_file("harness-plugins.yaml")
builder = HarnessBuilder(build_context=context)
```

Select entry-point keys, not `module:object` paths. `HarnessBuildContext.extensions` passes namespaced JSON to the selected factories.

### Optional Environment-Variable Source

Configured plugins are disabled by default. A deployment can opt into a document selected by environment variables:

```bash
export A13N_HARNESS_PLUGIN_CONFIG_ENABLED=true
export A13N_HARNESS_PLUGIN_CONFIG_FILE=/etc/a13n/harness-plugins.yaml
```

```python
builder = HarnessBuilder()
```

Source precedence is JSON, explicit file, then `harness-plugins.yaml` in the working directory. Missing or invalid configuration fails construction. Only enabled entries are loaded.

### Runtime Plugin Directories

A long-lived Host can publish a complete installed distribution in a new immutable directory and add that directory to `sys.path` before constructing a replacement builder:

```python
import importlib
import sys
from pathlib import Path


def activate_plugin_directory(path: str | Path) -> Path:
    plugin_directory = Path(path).resolve()
    normalized = str(plugin_directory)
    if normalized not in sys.path:
        sys.path.append(normalized)
    importlib.invalidate_caches()
    return plugin_directory
```

The directory must contain both the import package and standard distribution metadata with the entry point. A loose `.py` file is not sufficient.

Follow these steps:

1. Publish into a fresh directory that is not yet searchable.
2. Validate the complete installation.
3. Atomically place the directory.
4. Activate that exact path.
5. Build a replacement executable.

Do not mutate an already imported release in place or append two releases that own the same plugin key.

A new builder resolves the selected installed factories. Existing executables keep their plugin graph; retain them until active Runs finish.

The Host owns package installation, compatibility, and rollback.

## Plugin Lifecycle

A plugin has three distinct phases:

1. **factory selection** during builder construction, then **plugin creation** during each `build()` for every root or child definition, for configured plugins;
2. **Agent binding** once for each built root or child executable;
3. **Run binding and middleware** freshly for every logical Run.

Return one valid result from `wrap_run()`. Clean plugin resources in `finally` and propagate cancellation and cleanup failures. The Harness validates each successfully returned result. A replacement that never returns because `finally` raises does not replace the last validated inner result.

Contribute Capabilities through `get_capabilities()` on the Agent-bound instance returned by `for_agent()`.

For grouped tools, see [ToolProxy plugin-contributed sources](tool-proxy.md#plugin-contributed-sources).

## Provider Plugins

Provider plugins publish Environment definitions for Host selection. Compose Model, Web, Connector, and Memory definitions in code through `ProviderCatalog`.

Declare the Provider type, configuration, credentials, and separate management and connector factories:

```python
from a13n_environment.authentication import Authentication, CredentialMode
from a13n_environment.definition import EnvironmentProviderDefinition

ACME_SANDBOX = EnvironmentProviderDefinition(
    type="acme_sandbox",
    display_name="Acme Sandbox",
    configuration_model=AcmeConnectionConfiguration,
    credential_model=AcmeCredential,
    environment_model=AcmeEnvironmentConfiguration,
    provider_factory=_open_provider,
    connector_factory=_connector,
    describe_environment=_describe,
    authentication=Authentication(mode=CredentialMode.required),
    setup_url="https://acme.example/dashboard",
    setup_label="Acme dashboard",
    supports_stop=True,
    supports_destroy=True,
)
```

One distribution exports one `ProviderManifest` per entry point:

```python
from a13n_harness.providers.plugins import ProviderManifest

manifest = ProviderManifest(api_version=2, environment=(ACME_SANDBOX,))
```

```toml
[project.entry-points."a13n_harness.providers.plugins"]
acme = "acme_providers:manifest"
```

A Host names the entry points it trusts and builds one Environment catalog from its built-in and selected definitions:

```python
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.plugins import load_provider_plugins

plugins = load_provider_plugins(("acme",))
environments = ProviderCatalog(
    item for plugin in plugins for item in plugin.manifest.environment
)
definition = environments.require("acme_sandbox")
```

Select the entry-point name and Provider type explicitly. Duplicate types fail catalog construction; `require()` raises `ProviderNotSelected` for an unavailable type.

The runnable [plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) publishes one manifest and a separate Harness middleware plugin from the same project. The [installed Provider plugin example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/provider-plugin) shows direct use and Harness UI loading.

## Harness Extras

Environment definitions ship in the independent `a13n-environment` package. Install its extra when opening a backend:

| Extra    | Adds                      | Needed by                         |
| -------- | ------------------------- | --------------------------------- |
| `docker` | The Docker SDK for Python | The `docker` Environment Provider |
| `e2b`    | The asynchronous E2B SDK  | The `e2b` Environment Provider    |
| `modal`  | The Modal SDK             | The `modal` Environment Provider  |

```console
uv add a13n-harness "a13n-environment[docker,e2b]"
```

Reading Provider metadata needs no vendor SDK. A missing extra fails when the Provider opens.

## Environment Inputs and Advanced Bindings

Pass an inert `EnvironmentConnector` to `run(environment=...)`. Use `EnvironmentMount` for permission ceilings and paths, or an explicit runtime for dynamic mounts:

```python
from a13n_harness.environment import (
    FILE_ACTIONS,
    EnvironmentMount,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import create_environment_runtime

environment_runtime = create_environment_runtime(
    mounts={
        "workspace": EnvironmentMount(
            environment=environment,
            permission_ceiling=EnvironmentPermissionSet(operations=FILE_ACTIONS),
        ),
    },
    default_mount="workspace",
)
```

`permission_ceiling` accepts any exact action set, which is useful for a setup extension that needs only selected file operations. Provider permissions always narrow the ceiling. A runtime takes ownership of each underlying Environment only once, even if it is wrapped in another `EnvironmentMount`. Invalid initial routes do not take ownership, and a failed attempt to reuse the Environment cannot close its existing scope.

### Advanced Provider Binding Scopes

Use `EnvironmentProviderBinding` with `EnvironmentRuntimeMount` when a Host must acquire an authenticated session or another resource inside a custom async `bind()` scope. The binding then exposes provider-neutral file, shell, process, output, port, readiness, and portable-state operations. This advanced input remains accepted by explicit runtime construction and dynamic mount replacement. An existing Environment should use the direct inputs above.

That is a low-level runtime binding contract. Provider catalogs, Environment Provider lifecycle operations, credential handling, and durable provider state belong to [Provider plugins](#provider-plugins) and the Host, not to Harness middleware.

An `EnvironmentProviderBinding` is fresh and single-use. Effectful allocation, authentication, session entry, maintenance tasks, and cleanup-producing work belong inside its async `bind()` scope or in the owning provider layer, never in import-time discovery or an inert factory constructor.

## Environment Run Extensions

Use an `EnvironmentRunExtension` when setup and teardown need the stable complete `EnvironmentRuntime`, including an empty, single-mount, or multi-mount runtime.

### Callback Composition

For ordinary Host setup and cleanup, register an `EnvironmentRunCallbacks` adapter instead of defining an extension class:

```python
from a13n_harness.environment import (
    EnvironmentRunCallbacks,
    EnvironmentRunExtensionContext,
)


async def prepare_environment(
    context: EnvironmentRunExtensionContext,
) -> None:
    await context.environment.files.write_text(
        "/workspace/.active-run",
        f"{context.run_id}\n",
        mode="create",
    )


async def clean_environment(
    context: EnvironmentRunExtensionContext,
) -> None:
    await context.environment.files.remove(
        "/workspace/.active-run",
    )


active_run_callbacks = EnvironmentRunCallbacks(
    extension_id="workspace-marker",
    on_enter=prepare_environment,
    on_exit=clean_environment,
)
```

`on_enter` runs after Harness enters the adapters constructed by the Host, before the first model request. If setup needs a specific operation family, request readiness explicitly:

```python
from a13n_harness.environment import EnvironmentReadinessRequirement

await context.environment.ensure_ready(
    EnvironmentReadinessRequirement(operations=frozenset({"files"}))
)
```

`on_exit` runs before adapters close, including on failure or cancellation after successful entry. Use it for cleanup.

Extensions enter in registration order and exit in reverse order.

### Custom Resource Scope

Use a custom async context manager when setup and cleanup share local state or need a richer resource scope:

```python
from contextlib import asynccontextmanager

from a13n_harness.environment import EnvironmentRunExtensionContext


class WorkspaceMarkerExtension:
    def __init__(self, extension_id: str) -> None:
        self._extension_id = extension_id

    @property
    def extension_id(self) -> str:
        return self._extension_id

    @asynccontextmanager
    async def bind(self, *, context: EnvironmentRunExtensionContext):
        path = "/workspace/.active-run"
        await context.environment.files.write_text(
            path,
            f"{context.run_id}\n",
            mode="create",
        )
        try:
            yield
        finally:
            await context.environment.files.remove(path)
```

Register direct extension objects on an explicit runtime with ordinary Environment inputs:

```python
from a13n_harness.environment.advanced import create_environment_runtime


environment_runtime = create_environment_runtime(
    mounts={"workspace": environment},
    default_mount="workspace",
    extensions=(WorkspaceMarkerExtension("workspace-marker"),),
)
```

Give the runtime to the Harness through fresh Run bindings. Harness binds, activates, and closes it:

```python
from a13n_harness import RunBindings

bindings = RunBindings.embedded(environment=environment_runtime)
result = await executable.run("Use the prepared workspace", bindings=bindings)
```

The same `extensions=` sequence accepts callback adapters and custom extension objects together:

```python
environment_runtime = create_environment_runtime(
    mounts=mounts,
    default_mount="workspace",
    extensions=(
        active_run_callbacks,
        WorkspaceMarkerExtension("custom-marker"),
    ),
)
```

The Host supplies current Provider state when constructing adapters. Extensions enter after the adapters and exit in reverse order before those adapters close.

### Explicit Extension Factories

A distribution can register a side-effect-free factory under:

```toml
[project.entry-points."a13n_harness.environment_run_extensions"]
"acme.workspace-marker" = "acme_environment.extension:WorkspaceMarkerFactory"
```

Select factory keys with `build_environment_run_extension_factory_catalog()`, then create the instances:

```python
from a13n_harness import RunBindings
from a13n_harness.environment import (
    EnvironmentRunExtensionFactoryContext,
    build_environment_run_extension_factory_catalog,
)
from a13n_harness.environment.advanced import create_environment_runtime

catalog = build_environment_run_extension_factory_catalog(
    extension_keys=("acme.workspace-marker",),
)
extension = catalog.create_extension(
    EnvironmentRunExtensionFactoryContext(
        extension_key="acme.workspace-marker",
        extension_id="workspace-marker-primary",
        configuration={"marker_path": "/workspace/.active-run"},
    )
)
environment_runtime = create_environment_runtime(
    mounts=mounts,
    default_mount="workspace",
    extensions=(extension,),
)
bindings = RunBindings.embedded(environment=environment_runtime)
result = await executable.run("Use the prepared workspace", bindings=bindings)
```

`extension_key` selects the factory; `extension_id` must be unique in the runtime. Direct objects skip entry-point discovery.

## Runnable Example

Run the [integration package example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) for installed and direct composition with offline tests.
