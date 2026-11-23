---
title: Plugins and extensions
description: Extend Harness with run middleware plugins, packaged environment providers, and lifecycle hooks discovered via Python entry points or runtime directories.
---

Choose the extension point that matches the layer you want to customize:

| Goal                                                     | Extension point               | How to attach                                     |
| -------------------------------------------------------- | ----------------------------- | ------------------------------------------------- |
| Wrap a complete Run (audit, metrics, input/output hooks) | Harness middleware plugin     | `build(plugins=...)` or an enabled plugin factory |
| Add tools, instructions, or Agent-loop hooks             | [Capability](capabilities.md) | `AgentSpec` or `build(capabilities=...)`          |
| Prepare an Environment target on first use               | `EnvironmentSource`           | `run(environment=...)` or `EnvironmentMount`      |
| Set up and tear down resources across mounts             | `EnvironmentRunExtension`     | `create_environment_runtime(extensions=...)`      |
| Package a custom sandbox backend                         | Provider plugin               | Python entry point + Provider type                |

## Harness middleware

Subclass `AbstractHarnessPlugin` and override `wrap_run()` to observe or transform execution around a Run:

```python title="plugin_demo.py"
import asyncio
from typing import Any

from pydantic_ai.models.function import FunctionModel

from a13n_harness import AbstractHarnessPlugin, AgentSpec, HarnessBuilder, HarnessRunResult
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


async def respond(messages, info):
    del messages, info
    yield "Plugin example completed."


async def main() -> None:
    executable = HarnessBuilder(configured_plugins_enabled=False).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        plugins=(AuditPlugin("audit-primary"),),
    )
    result = await executable.run("Run the example.")
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

### Plugin lifecycle

Plugins are constructed with immutable configuration so one instance can safely serve concurrent Runs. Plugins can also contribute Capabilities via `get_capabilities()`, which Harness composes alongside `AgentSpec` and builder Capabilities.

## Publish a plugin factory

To make a plugin discoverable by Hosts (such as Service or Harness UI), register a factory function under the `a13n_harness.plugins` entry-point group in `pyproject.toml`:

```toml
[project.entry-points."a13n_harness.plugins"]
"acme.audit" = "acme_plugins:build_audit_plugin"
```

Hosts explicitly enable factories by key (e.g., `plugins.keys` in Service or `AppDefinition.factory_keys`); installing a package alone does not activate it.

## Provider plugins

Custom Environment backends implement the `EnvironmentProvider` contract and publish via Python entry points so Hosts can discover and configure them uniformly. See [Provider catalog and plugins](../environments/provider-plugins.md) for the packaging contract and examples.
