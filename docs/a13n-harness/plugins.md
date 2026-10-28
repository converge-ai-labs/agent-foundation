---
title: Plugins and extensions
description: Add Run middleware, package it for configuration, and manage Environment setup and cleanup.
---

Start with a direct plugin when you need to observe or change a complete Run. This page builds an offline audit plugin, packages the same implementation as a factory, and then demonstrates Environment setup and cleanup. Use the Python environment from [Getting started](getting-started.md); the two standalone programs need no Model credentials.

## Choose and Activate an Extension Point

Choose the extension that owns the behavior:

| Task                                                         | Extension                     | Selection                                            |
| ------------------------------------------------------------ | ----------------------------- | ---------------------------------------------------- |
| Wrap semantic input, execution, errors, or a complete result | Harness middleware            | `build(plugins=...)` or an enabled installed factory |
| Add tools, instructions, or Agent-loop hooks                 | [Capability](capabilities.md) | Definition or Run composition                        |
| Prepare a Host target on first use                           | `EnvironmentSource`           | `run(environment=...)` or `EnvironmentMount`         |
| Set up and clean up resources across mounts                  | `EnvironmentRunExtension`     | `create_environment_runtime(extensions=...)`         |
| Publish an Environment backend for Hosts                     | Provider plugin               | Selected entry-point name, then Provider type        |

Package installation makes an extension available. The Host still selects it. `AgentSpec` selects Capabilities; middleware and Environment extensions have their own composition APIs.

## Harness Middleware

Middleware surrounds the complete Run. The following plugin emits an observation before execution and another after receiving the inner result. Its configuration is immutable during Runs, so the same instance can serve concurrent invocations.

### Wrap Execution and Emit Observations

Save this as `plugin_demo.py` and run `python plugin_demo.py`. The assertions check both observations and the final output.

```python
import asyncio
from typing import Any

from pydantic_ai.models.function import FunctionModel

from a13n_harness import AbstractHarnessPlugin, AgentSpec, HarnessBuilder, HarnessRunResult
from a13n_harness.events import HarnessEvent, HarnessExtensionEvent
from a13n_harness.plugins import PluginRunExchange, PluginRunNext


class AuditPlugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id: str, label: str = "audit") -> None:
        self._plugin_id = plugin_id
        self._label = label

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    async def wrap_run(
        self, exchange: PluginRunExchange, call_next: PluginRunNext[Any]
    ) -> HarnessRunResult[Any]:
        await exchange.context.events.emit(
            HarnessExtensionEvent(
                kind="diagnostic", payload={"label": self._label, "phase": "before"},
            )
        )
        result = await call_next(exchange)
        await exchange.context.events.emit(
            HarnessExtensionEvent(
                kind="diagnostic", payload={"label": self._label, "phase": "after"},
            )
        )
        return result


async def respond(messages, info):
    yield "Plugin example completed."


async def main() -> None:
    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(), output_type=str,
        model=FunctionModel(stream_function=respond),
        plugins=(AuditPlugin("audit-primary"),),
    )
    phases = []
    async with executable.stream("Run the example.") as stream:
        async for item in stream:
            if isinstance(item, HarnessEvent) and isinstance(item.event, HarnessExtensionEvent):
                if item.event.kind == "diagnostic":
                    phases.append(item.event.payload["phase"])
    result = stream.result
    assert result is not None
    assert phases == ["before", "after"]
    assert result.output_or_raise() == "Plugin example completed."
    print(phases, result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

`wrap_run()` awaits `call_next(exchange)` at most once and returns a complete `HarnessRunResult`. To change input, pass `exchange.with_input(...)` to the continuation. To short-circuit, return a valid result without calling the continuation.

The Run delivers extension observations through its bounded event channel. Slow consumption applies backpressure. Middleware emits events through `context.events`; the caller consumes the stream. Here `after` means the inner call returned; use `finally` for resource cleanup on failures as well.

### Direct Composition

The example selects `AuditPlugin("audit-primary")` through `build(plugins=...)`. That is sufficient for an embedded application; factory discovery is optional. `configured_plugins_enabled=False` keeps this standalone example independent of deployment plugin settings.

Give every plugin a unique ID. Definition order is the default; `PluginOrdering` can add `wraps`, `wrapped_by`, or outermost/innermost constraints. `requires` checks presence without ordering the pair. Invalid references and cycles fail construction.

### Feature Capabilities and Current Provider Clients

A feature such as Web has two inputs: the definition enables its tools, and each Run receives current clients and policy. This helper reuses `AuditPlugin` from `plugin_demo.py`; the application supplies its Model and Host-owned `WebBinding`.

```python
from pydantic_ai.models import Model
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.capabilities import WebBinding, WebCapability, WebConfiguration
from plugin_demo import AuditPlugin


async def run_web(model: Model, web: WebBinding, url: str) -> str:
    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(), output_type=str, model=model,
        plugins=(AuditPlugin("audit-web"),),
        capabilities=(WebCapability(WebConfiguration()),),
    )
    result = await executable.run(
        f"Read this page: {url}", bindings=RunBindings.embedded(web=web),
    )
    return result.output_or_raise()
```

Keep `WebCapability` on the definition, not in plugin contributions. The Host owns client lifetime; `HarnessState` stores no live clients. For a complete fixture client and an actual offline `fetch` call, use the [middleware and Web example](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins#middleware-and-feature-with-host-bindings).

### Publish a Plugin Factory

Use a factory when a Host should select this plugin by configuration. Move `AuditPlugin` and its imports into your package's `acme_harness/plugin.py` and add the following classes in the same module. The `label` field is included in the diagnostic payload; it changes real behavior.

```python
from pydantic import BaseModel, ConfigDict, Field
from a13n_harness.plugin_factories import HarnessPluginFactory, HarnessPluginFactoryContext


class AuditConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(default="audit", min_length=1)


class AuditPluginFactory(HarnessPluginFactory):
    configuration_model = AuditConfiguration
    display_name = "Audit"
    description = "Emit labeled Run observations."

    @classmethod
    def plugin_key(cls) -> str:
        return "acme.audit"

    def create_plugin(self, context: HarnessPluginFactoryContext) -> AuditPlugin:
        configuration = AuditConfiguration.model_validate(dict(context.configuration))
        return AuditPlugin(context.plugin_id, label=configuration.label)
```

Register the no-argument factory class in that distribution’s `pyproject.toml`. The entry-point name must equal `plugin_key()`.

```toml
[project.entry-points."a13n_harness.plugins"]
"acme.audit" = "acme_harness.plugin:AuditPluginFactory"
```

Factory validation and construction should perform no I/O. Allocate mutable per-Run state through `for_run()`, rather than storing it in the factory.

### Describe Configuration for Editors

`configuration_model` serves both validation and editor schema generation through `model_json_schema(mode="validation")`. Put defaults, constraints, descriptions, and validators in that model. Static `display_name` and `description` provide credential-free editor labels.

A factory with a custom `validate_configuration()` and no model can use a JSON editor. Service also falls back to JSON editing if schema generation fails. Schemas are generated when listing plugins; backend validation remains authoritative for constraints that JSON Schema cannot express.

### Select Configured Plugins

Install the distribution into the Host's Python environment first. Then save this as `harness-plugins.yaml`:

```yaml
schema_version: "1"
plugins:
  - plugin_id: audit-primary
    plugin_key: acme.audit
    enabled: true
    configuration:
      label: production
```

```python
from a13n_harness import HarnessBuilder
from a13n_harness.plugin_configuration import HarnessBuildContext

context = HarnessBuildContext.from_file("harness-plugins.yaml")
builder = HarnessBuilder(build_context=context)
```

Build your Agent with this `builder`; it adds the configured plugin. Omit the direct `AuditPlugin("audit-primary")` from the earlier example to avoid duplicate IDs. The constructor resolves installed factories, and each `build()` validates configuration and creates plugins for root and child definitions.

The document also accepts JSON or `HarnessBuildContext.from_configuration(mapping)`. `plugin_key` selects an installed entry point; `plugin_id` identifies an instance. `HarnessBuildContext.extensions` passes namespaced JSON to factories. Keep credentials and live clients in Host-owned bindings.

### Optional Environment-Variable Source

To let deployment settings select the document, enable ambient configuration before constructing `HarnessBuilder()`:

```bash
export A13N_HARNESS_PLUGIN_CONFIG_ENABLED=true
export A13N_HARNESS_PLUGIN_CONFIG_FILE=/etc/a13n/harness-plugins.yaml
```

Ambient loading is off by default. When enabled, `A13N_HARNESS_PLUGIN_CONFIG_JSON` takes precedence over the explicit file, which takes precedence over `harness-plugins.yaml` in the working directory. Missing or invalid selected configuration fails construction. Only enabled entries load factories; an explicit build context bypasses ambient source lookup.

### Runtime Plugin Directories

For a newly available distribution in a long-lived Host:

1. Install the complete distribution into a fresh, immutable directory, including package files and entry-point metadata.
2. Validate it before publishing the directory.
3. Add that exact directory to `sys.path` and call `importlib.invalidate_caches()`.
4. Construct a replacement builder and executable.
5. Retain existing executables until their active Runs finish.

A loose Python file supplies no distribution entry point. Existing builders retain their factory catalog; existing executables retain their plugin graph. This workflow adds distributions, not hot replacement of already imported modules. The Host owns compatibility and rollback; do not publish two distributions with the same selected key.

## Plugin Lifecycle

Keep data at the lifetime that owns it:

| Phase              | Hook                                            | What to keep there             |
| ------------------ | ----------------------------------------------- | ------------------------------ |
| Builder/definition | Factory selection, then creation per definition | Validated configuration        |
| Executable         | `for_agent()`, then `get_capabilities()`        | Reentrant Agent-bound behavior |
| Logical Run        | `for_run()`, then `wrap_run()`                  | Fresh mutable Run state        |

Binding replacements preserve concrete type, ID, and ordering. The audit example needs no mutable Run state and can return itself from the default binding hooks. A Capability can resolve its Run-bound plugin through `ctx.deps.plugins.require(plugin_id, ExpectedType)` after binding; see [ToolSearch contributions](tool-search.md#plugin-contributed-sources).

Clean resources in `finally` and propagate cancellation and cleanup failures. Harness validates every returned result. If cleanup raises before a replacement returns, the last valid inner result remains the retained outcome; terminal delivery is withheld. Imported plugins run with the Python process's authority, so install trusted code.

## Provider Plugins

Publish a Provider plugin when other Hosts need to select your Environment backend. Start from the complete [workspace Provider implementation](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/src/a13n_plugin_examples/environment.py):

1. Define configuration and optional credential models.
2. Implement `describe_environment` without preparing the target.
3. Implement `connector_factory` to return an inert execution connector.
4. Export an `EnvironmentProviderDefinition` in `ProviderManifest(api_version=1)`.
5. Register the manifest under `a13n_harness.providers.plugins` and have the Host explicitly select that entry-point name and Provider type.

The Host prepares and destroys targets. Duplicate Provider types fail catalog construction; `ProviderCatalog.require()` raises `ProviderNotSelected` for an unavailable type. Model, Web, Connector, and Memory definitions are composed through `ProviderCatalog` in code; installed Provider manifests export Environment definitions.

Published plugins using this Environment API require `a13n-harness>=0.10.0,<0.11.0`. The Provider manifest version is separate from middleware configuration's `schema_version="1"`. Ordinary middleware loading retains the 0.9.3 contract; middleware importing old Environment APIs needs source migration. The [installed Provider tutorial](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/provider-plugin) includes direct use and Harness UI selection.

## Harness Extras

Install the backend extra before opening its Provider:

| Extra    | Dependency            | Provider |
| -------- | --------------------- | -------- |
| `docker` | Docker SDK for Python | `docker` |
| `e2b`    | Async E2B SDK         | `e2b`    |
| `modal`  | Modal SDK             | `modal`  |

For example, `uv add "a13n-harness[docker,e2b]"` installs two backend dependencies. Metadata inspection works without vendor SDKs; opening a backend requires its extra.

## Environment Inputs and Sources

Use `EnvironmentSource` to connect Host preparation with Run execution. Pass a source to `run(environment=...)`, or wrap it in `EnvironmentMount` to set paths and permissions. The callback example below includes a complete `PreparedSource` for an already prepared local directory; [Environments](environments.md#supply-a-source) explains the source on its own.

Static registration publishes mount metadata. First dependent use calls `ensure_ready()`, opens a fresh execution, and gives Harness ownership of execution cleanup. Mount `permission_ceiling` lists exact actions; Provider permissions can narrow it further. Reusing an execution owned by another mount is rejected without closing that owner's resources.

### Host Preparation Callbacks

Keep `provider_key`, `environment_id`, `descriptor`, and `state` as inert metadata. Put allocation, current-state publication, and readiness waiting in `ensure_ready()`; return a connector only when the target is usable. The Host handles management failures and durable state. Harness does not automatically retry failed activation on the same mount.

## Environment Run Extensions

Use a Run extension for resources that span the complete entered Environment, including empty or multi-mount runtimes. Extensions see the static mount set before the first Model request and leave before opened executions close.

### Callback Composition

Save this independent program as `environment_extension_demo.py`. It creates a marker in a temporary directory, reads it during input preparation, and checks that cleanup removed it after the Run.

```python
import asyncio
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic_ai.models.function import FunctionModel

from a13n_harness import AgentSpec, HarnessBuilder, RunBindings, RunPreparationContext
from a13n_harness.environment import (
    EnvironmentAction, EnvironmentMount, EnvironmentPermissionSet,
    EnvironmentRunCallbacks, EnvironmentRunExtensionContext,
)
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.execution import EnvironmentConnector
from a13n_harness.environment.models import EnvironmentDescriptor, EnvironmentState
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL


@dataclass(frozen=True)
class PreparedSource:
    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        return self.connector


async def prepare(context: EnvironmentRunExtensionContext) -> None:
    await context.environment.files.write_text(
        "/workspace/.active-run", context.run_id, mode="create",
    )


async def clean(context: EnvironmentRunExtensionContext) -> None:
    await context.environment.files.remove("/workspace/.active-run")


async def read_marker(context: RunPreparationContext) -> str:
    marker = await context.environment.files.read_text("/workspace/.active-run")
    assert marker.text == context.run_id
    return "The marker exists. Finish the example."


async def respond(messages, info):
    yield "Extension example completed."


async def main() -> None:
    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond),
    )
    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        connector = DIRECT_LOCAL.execution_connector(
            {"root": {"path": str(root)}}, environment_id="env-extension",
        )
        runtime = create_environment_runtime(
            mounts={"workspace": EnvironmentMount(
                source=PreparedSource(connector),
                permission_ceiling=EnvironmentPermissionSet(operations=frozenset({
                    EnvironmentAction.FILE_READ_TEXT,
                    EnvironmentAction.FILE_WRITE_TEXT,
                    EnvironmentAction.FILE_REMOVE,
                })),
            )},
            default_mount="workspace",
            extensions=(EnvironmentRunCallbacks(
                extension_id="workspace-marker", on_enter=prepare, on_exit=clean,
            ),),
        )
        result = await executable.run(
            input_factory=read_marker,
            bindings=RunBindings.embedded(environment=runtime),
        )
        assert not (root / ".active-run").exists()
        print(result.output_or_raise(), "Marker removed.")


if __name__ == "__main__":
    asyncio.run(main())
```

Run `python environment_extension_demo.py`; expect `Extension example completed. Marker removed.` The first file operation prepares the mount. Entering an extension alone does not prepare targets; setup that needs a family ready can explicitly call `context.environment.ensure_ready(EnvironmentReadinessRequirement(operations=frozenset({"files"})))` after importing that requirement from `a13n_harness.environment`.

Callbacks enter in registration order and exit in reverse. `on_exit` runs after successful entry, including later failure or cancellation. If `on_enter` itself fails partway through setup, that callback owns cleanup of its partial work.

### Custom Resource Scope

For setup and teardown that share local state, add this class above `main()` in the environment example:

```python
from contextlib import asynccontextmanager


class WorkspaceMarkerExtension:
    def __init__(self, extension_id: str, path: str) -> None:
        self._extension_id = extension_id
        self._path = path

    @property
    def extension_id(self) -> str:
        return self._extension_id

    @asynccontextmanager
    async def bind(self, *, context: EnvironmentRunExtensionContext):
        await context.environment.files.write_text(
            self._path, context.run_id, mode="create",
        )
        try:
            yield
        finally:
            await context.environment.files.remove(self._path)
```

Add `WorkspaceMarkerExtension("custom-marker", "/workspace/.custom-run")` to the runtime's `extensions` tuple alongside the callback adapter. Each extension needs its own ID and resource path. Give the runtime to the Run through fresh `RunBindings.embedded(environment=runtime)`; Harness owns entering and closing that runtime.

### Explicit Extension Factories

For configuration-driven selection, register a side-effect-free factory in the `a13n_harness.environment_run_extensions` entry-point group. `build_environment_run_extension_factory_catalog(extension_keys=(... ,))` loads selected installed keys; `explicit_factories=(factory,)` accepts local factory objects without installation.

Call `catalog.create_extension(EnvironmentRunExtensionFactoryContext(extension_key=..., extension_id=..., configuration=...))`, then pass the resulting object in the same `extensions` tuple used above. The key selects the factory; the ID identifies one unique runtime instance. The [complete factory implementation](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/src/a13n_plugin_examples/environment_extension.py) and [end-to-end driver](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/src/a13n_plugin_examples/demo_environment_extension.py) supply the configuration models, installed selection, explicit selection, and marker assertions.

## Runnable Example

Use the [integration package](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) when you are ready to test packaging. Follow its installation and run commands to exercise metadata discovery as well as direct composition. The two programs on this page test middleware and Environment lifetimes locally; the package adds the distribution entry-point path.

## Migrate middleware

Replace legacy `PluginRunResponse` and async event generators with `async def wrap_run(...)`, one awaited continuation, and a returned result. Emit observations through `context.events.emit()`.

Native and Run lifecycle events remain Harness-owned. For presentation changes, configure the Host's Stream Protocol `DisplayFold` processor before the shared live-and-persisted fold, and use the same processor when restoring it.
