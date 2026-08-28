# Plugins and Extensions

Agent Harness exposes focused extension points rather than one universal plugin interface. Choose the narrowest boundary that owns the behavior and lifetime you need.

| Extension point                | Use it for                                                                                                                       | Lifecycle                                                                | Model-visible                                    |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------ |
| Harness middleware plugin      | Transform semantic input, observe events, wrap errors, or replace a complete result candidate                                    | Agent-bound at build, then freshly run-bound around each logical run     | Only through an explicit Capability contribution |
| Pydantic Capability or Toolset | Instructions, request hooks, tools, Agent-loop state, and collaboration with other run Capabilities                              | Native Pydantic Agent/run lifecycle                                      | Yes                                              |
| `EnvironmentProviderBinding`   | Implement one already selected provider-neutral Environment operation revision                                                   | One binding scope inside one `EnvironmentRunBinding`                     | Only through explicit Environment tools/context  |
| `EnvironmentRunExtension`      | Hold a resource that needs the complete entered Environment aggregate; use `EnvironmentRunCallbacks` for simple paired callbacks | Entered after state restore; reverse-order exit before provider teardown | No                                               |

Installed entry-point metadata means code is available, not enabled or authorized. Importing `a13n_harness` scans no entry points and activates no extension.

## Harness Middleware

Harness plugins are trusted Python middleware around one complete process-local run. Use a Pydantic AI Capability for behavior inside the Agent loop. Use middleware when behavior must wrap semantic input, the canonical event stream, errors, or the complete result boundary.

A plugin can be supplied directly as an `AbstractHarnessPlugin` or created from a selected `HarnessPluginFactory` entry point.

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

Direct and configured plugins enter the same ordering, Agent binding, run binding, middleware, result validation, and cleanup path.

### Publish a Plugin Factory

Register one no-argument factory class:

```toml
[project.entry-points."a13n_harness.plugins"]
"acme.audit" = "acme_harness.plugin:AuditPluginFactory"
```

The entry-point name and `plugin_key()` must match:

```python
from a13n_harness import (
    AbstractHarnessPlugin,
    HarnessPluginFactory,
    HarnessPluginFactoryContext,
)


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

    def create_plugin(
        self,
        context: HarnessPluginFactoryContext,
    ) -> AbstractHarnessPlugin:
        # Validate context.configuration with a package-owned schema.
        return AuditPlugin(context.plugin_id)
```

Factory construction is synchronous and side-effect free. `create_plugin()` returns a fresh concrete plugin for each configured instance. Mutable run data belongs in the exact run-bound plugin returned by `for_run()`.

### Select Configured Plugins

The Harness plugin document is a data schema, not a required file format:

```python
from a13n_harness import HarnessBuildContext, HarnessBuilder

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

Configuration selects stable entry-point keys and never accepts a `module:object` target.

### Optional Environment Source

Configured plugins are disabled by default. A deployment can opt into an environment-selected document:

```bash
export A13N_HARNESS_PLUGIN_CONFIG_ENABLED=true
export A13N_HARNESS_PLUGIN_CONFIG_FILE=/etc/a13n/harness-plugins.yaml
```

```python
builder = HarnessBuilder()
```

When disabled, builder construction does not read the file or scan package metadata. When enabled, it imports only factory keys selected by enabled entries.

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

Publish into a fresh directory that is not yet searchable, validate the complete installation, atomically place it, activate that exact path, then build a replacement executable. Do not mutate an already imported release in place or append two releases that own the same plugin key.

A new builder discovers current distribution metadata. An existing builder retains its selected factory catalog; an existing executable retains its already constructed plugin graph. Keep old executables alive until their active runs finish.

The Host remains responsible for artifact trust, dependency compatibility, installation locks, directory ordering, and rollback. Harness includes no package installer or process-global mutable plugin registry.

## Plugin Lifecycle

A plugin has three distinct phases:

1. **factory selection and creation** during builder construction for configured plugins;
2. **Agent binding** once for each built root or child executable;
3. **run binding and middleware** freshly for every logical run.

Run middleware must preserve single-consumer streaming and yield exactly one structurally valid result candidate. Use `try/finally` for plugin-owned cleanup. Do not swallow cancellation or convert cleanup failure into clean completion.

Plugins can contribute native Capabilities at Agent binding. They should not implement a second tool dispatcher, message history, usage accumulator, or Environment lifecycle.

## Environment Provider Bindings

Trusted code can implement `EnvironmentProviderBinding` directly when it already owns one process-local resource revision and can expose provider-neutral file, shell, process, output, port, readiness, and portable-state operations.

That is a low-level runtime binding contract. Provider specification catalogs, `EnvironmentProvider` lifecycle operations, credential handling, and durable provider state are not Harness middleware and are not documented as a Harness plugin system.

An `EnvironmentProviderBinding` is fresh and single-use. Effectful allocation, authentication, session entry, maintenance tasks, and cleanup-producing work belong inside its async `bind()` scope or in the owning provider layer, never in import-time discovery or an inert factory constructor.

## Environment Run Extensions

Use an `EnvironmentRunExtension` when setup and teardown need the stable complete `Environment`, including zero, one, or several provider bindings.

### Callback Composition

For ordinary Host setup and cleanup, register an `EnvironmentRunCallbacks` adapter instead of defining an extension class:

```python
from a13n_harness import (
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

`on_enter` runs after portable Environment state restoration and before controller activation. It participates in making the aggregate active; it does not mean every operation family is globally ready. Call `context.environment.ensure_ready()` when setup depends on an exact family. `on_exit` runs during reverse-order Environment teardown while provider-neutral operations remain available. It also runs after failure, cancellation, or rollback following successful entry, so it is cleanup rather than a success notification.

Each adapter is one identified extension. Multiple adapters enter in registration order and exit in reverse order. Callback failures use the same authoritative failure semantics as custom extension setup and cleanup. Catch expected failures inside a callback only when the Host deliberately wants best-effort behavior.

### Custom Resource Scope

Use a custom async context manager when setup and cleanup share local state or need a richer resource scope:

```python
from contextlib import asynccontextmanager

from a13n_harness import EnvironmentRunExtensionContext


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

Register direct extension objects through the advanced aggregate route:

```python
from a13n_harness.environment.advanced import create_environment_run_binding


environment_binding = create_environment_run_binding(
    initial_topology=topology,
    topology_limits=topology_limits,
    state_limits=state_limits,
    extensions=(WorkspaceMarkerExtension("workspace-marker"),),
)
```

The same `extensions=` sequence accepts callback adapters and custom extension objects together:

```python
environment_binding = create_environment_run_binding(
    initial_topology=topology,
    topology_limits=topology_limits,
    state_limits=state_limits,
    extensions=(
        active_run_callbacks,
        WorkspaceMarkerExtension("custom-marker"),
    ),
)
```

Extensions enter after providers are available and portable Environment state is restored. They exit in reverse order while the Environment is still open and before provider scopes close.

### Explicit Extension Factories

A distribution can register a side-effect-free factory under:

```toml
[project.entry-points."a13n_harness.environment_run_extensions"]
"acme.workspace-marker" = "acme_environment.extension:WorkspaceMarkerFactory"
```

The Host explicitly selects keys with `build_environment_run_extension_factory_catalog()` or provides exact registrations. Harness owns no ambient configuration document for Environment run extensions.

## Runnable Example

The [plugin integration example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) contains wheel-ready middleware and Environment extension entry points, direct-code composition, YAML selection, per-run isolation, and offline tests.
