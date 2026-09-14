# Plugins and Extensions

Agent Harness exposes focused extension points rather than one universal plugin interface. Choose the narrowest boundary that owns the behavior and lifetime you need.

| Extension point              | Use it for                                                                                                                       | Lifecycle                                                                       | Model-visible                                    |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------ |
| Harness middleware plugin    | Transform semantic input, observe events, wrap errors, or replace a complete result candidate                                    | Agent-bound at build, then freshly run-bound around each logical run            | Only through an explicit Capability contribution |
| Pydantic Capability          | Own or compose Toolsets, instructions, request hooks, Agent-loop state, and collaboration with other run Capabilities            | Native Pydantic Agent/run lifecycle                                             | Yes                                              |
| `EnvironmentProviderBinding` | Implement one already selected provider-neutral Environment operation revision                                                   | One binding scope inside one `EnvironmentRuntime`                               | Only through explicit Environment tools/context  |
| `EnvironmentRunExtension`    | Hold a resource that needs the complete entered Environment aggregate; use `EnvironmentRunCallbacks` for simple paired callbacks | Entered with the current aggregate; reverse-order exit before provider teardown | No                                               |

Installed entry-point metadata means code is available, not enabled or authorized. Importing `a13n_harness` scans no entry points and activates no extension.

## Choose and Activate an Extension Point

Availability, selection, and activation are separate decisions:

| Extension point              | Makes an implementation available                                                                               | Selects and activates it                                                                                                 | Automatic behavior                                                                                                                     |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| Harness middleware plugin    | Install a package with an `a13n_harness.plugins` entry point, or import a concrete plugin                       | Enable one configured `plugin_key`/`plugin_id`, or pass the concrete plugin to `HarnessBuilder.build()`                  | Ambient configuration is disabled by default; after explicit opt-in, only entries with `enabled: true` are loaded                      |
| Pydantic Capability          | Import a concrete Capability, or let a trusted Host authorize an exact declarative type                         | Put the instance in definition/run composition, or put its serialized spec in `AgentSpec.capabilities`                   | Optional Capabilities are never inferred from package presence; see [Capabilities](capabilities.md#select-capabilities-with-agentspec) |
| `EnvironmentRunExtension`    | Install a package with an `a13n_harness.environment_run_extensions` entry point, or import a concrete extension | Select an exact factory key, create one identified instance, and pass it to `create_environment_runtime(extensions=...)` | There is no ambient configuration or automatic selection                                                                               |
| `EnvironmentProviderBinding` | Construct a fresh trusted binding from the owning Provider layer                                                | Put the binding in an `EnvironmentRuntimeMount`                                                                          | Provider availability never mounts or exposes model tools by itself                                                                    |

`AgentSpec` selects only Capabilities. It does not select Harness middleware, Environment run extensions, Providers, credentials, or live collaborators. A selected plugin may contribute ordinary Capabilities from trusted plugin code, but that contribution is owned by the plugin rather than reconstructed from `AgentSpec`.

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
from a13n_harness import AbstractHarnessPlugin
from a13n_harness.plugin_factories import (
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

Configuration selects stable entry-point keys and never accepts a `module:object` target. `HarnessBuildContext.extensions` is a bounded namespaced JSON value forwarded to selected plugin factories; despite its name, it is not a plugin or Environment-extension list and does not enable anything.

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

A replacement builder with configured plugins explicitly enabled resolves current distribution metadata only for factory keys selected by its enabled entries. A disabled builder still performs no metadata scan. An existing builder retains its selected factory catalog; an existing executable retains its already constructed plugin graph. Keep old executables alive until their active runs finish.

The Host remains responsible for artifact trust, dependency compatibility, installation locks, directory ordering, and rollback. Harness includes no package installer or process-global mutable plugin registry.

## Plugin Lifecycle

A plugin has three distinct phases:

1. **factory selection and creation** during builder construction for configured plugins;
2. **Agent binding** once for each built root or child executable;
3. **run binding and middleware** freshly for every logical run.

Run middleware must preserve single-consumer streaming and yield exactly one structurally valid result candidate. Use `try/finally` for plugin-owned cleanup. Do not swallow cancellation or convert cleanup failure into clean completion.

Plugins can contribute native Capabilities at Agent binding. Harness calls `get_capabilities()` on the instance returned by `for_agent()`; do not extract contributions before that binding. They should not implement a second tool dispatcher, message history, usage accumulator, or Environment lifecycle.

To support optional grouped presentation, a plugin can expose source factories or a presentation option for its contribution. A Host-owned composition layer can aggregate selected sources into one `ToolProxyCapability(groups=...)`, while retaining required middleware and leaving unrelated tools direct. This requires an explicit plugin integration interface, not generic lookup or interception of arbitrary plugins. See [ToolProxy plugin-contributed sources](tool-proxy.md#plugin-contributed-sources) for an example and duplicate-installation boundaries.

## Environment Inputs and Advanced Bindings

When a Provider has already constructed an `Environment`, pass it directly to `run(environment=...)` or wrap it in `EnvironmentMount` to select access and paths. Explicit runtimes and their dynamic `mount()` and `replace()` methods accept the same inputs. Harness owns entry and local cleanup; Host code does not need to implement a forwarding binding class:

```python
from a13n_harness.environment import EnvironmentAccess, EnvironmentMount
from a13n_harness.environment.advanced import create_environment_runtime

environment_runtime = create_environment_runtime(
    mounts={
        "workspace": EnvironmentMount(
            environment=environment,
            access=EnvironmentAccess.READ_WRITE,
        ),
    },
    default_mount="workspace",
)
```

`access` also accepts an `EnvironmentPermissionSet` with an exact action ceiling. This is useful for a setup extension that needs only selected file operations. Provider permissions always narrow the ceiling. Each underlying Environment transfers only once, even if it is wrapped in another `EnvironmentMount`; invalid initial routes do not transfer it, and a failed attempt to reuse it cannot close its existing scope.

### Advanced Provider Binding Scopes

Use `EnvironmentProviderBinding` with `EnvironmentRuntimeMount` when a Host needs to acquire an authenticated session or another resource inside a custom async `bind()` scope and expose provider-neutral file, shell, process, output, port, readiness, and portable-state operations. This advanced input remains accepted by explicit runtime construction and dynamic mount replacement. An existing Environment should use the direct inputs above.

That is a low-level runtime binding contract. Provider specification catalogs, `EnvironmentProvider` lifecycle operations, credential handling, and durable provider state are not Harness middleware and are not documented as a Harness plugin system.

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

`on_enter` runs after portable Environment state restoration and before runtime activation. It participates in making the runtime active; it does not mean every operation family is globally ready. Call `context.environment.ensure_ready()` when setup depends on an exact family. `on_exit` runs during reverse-order Environment teardown while provider-neutral operations remain available. It also runs after failure, cancellation, or rollback following successful entry, so it is cleanup rather than a success notification.

Each adapter is one identified extension. Multiple adapters enter in registration order and exit in reverse order. Callback failures use the same authoritative failure semantics as custom extension setup and cleanup. Catch expected failures inside a callback only when the Host deliberately wants best-effort behavior.

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

Give the runtime to the Harness through fresh run bindings. Harness binds, activates, and closes it:

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

Extensions enter after providers are available and portable Environment state is restored. They exit in reverse order while the Environment is still open and before provider scopes close.

### Explicit Extension Factories

A distribution can register a side-effect-free factory under:

```toml
[project.entry-points."a13n_harness.environment_run_extensions"]
"acme.workspace-marker" = "acme_environment.extension:WorkspaceMarkerFactory"
```

The Host explicitly selects keys with `build_environment_run_extension_factory_catalog()` or provides exact factories. Harness owns no ambient configuration document for Environment run extensions. A complete installed-factory path is:

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

`extension_key` chooses one installed factory; `extension_id` identifies one concrete aggregate instance and must be unique within that runtime. Catalog construction imports only explicitly selected keys. Passing a concrete extension directly skips metadata discovery entirely.

## Runnable Example

The [integration package example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) contains Host-authorized custom Capability selection, wheel-ready middleware and Environment extension entry points, direct-code composition, YAML selection, public Harness execution, per-run isolation, and offline tests.
