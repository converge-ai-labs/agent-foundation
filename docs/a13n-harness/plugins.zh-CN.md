---
title: 插件与扩展
description: 通过运行中间件插件、环境提供方插件和生命周期钩子扩展 Harness，支持 Python Entry Points 与运行时目录发现。
---

按需定制的层次选择合适的扩展点：

| 目标                                     | 扩展点                    | 挂载方式                                     |
| ---------------------------------------- | ------------------------- | -------------------------------------------- |
| 包裹完整运行（审计、指标、输入输出钩子） | Harness 中间件插件        | `build(plugins=...)` 或已启用的插件工厂      |
| 添加工具、指令或智能体循环钩子           | [能力](capabilities.md)   | `AgentSpec` 或 `build(capabilities=...)`     |
| 在首次使用时准备环境目标                 | `EnvironmentSource`       | `run(environment=...)` 或 `EnvironmentMount` |
| 跨挂载点准备与清理资源                   | `EnvironmentRunExtension` | `create_environment_runtime(extensions=...)` |
| 封装自定义沙箱后端                       | 提供方插件                | Python Entry Point + 提供方类型              |

## Harness 中间件

继承 `AbstractHarnessPlugin` 并重写 `wrap_run()`，在运行前后观测或改写执行：

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

### 插件生命周期

插件以不可变配置构造，单个实例可安全服务于并发运行。插件还可以通过 `get_capabilities()` 贡献能力，Harness 会将其与 `AgentSpec` 和构建器能力统一组合。

## 发布插件工厂

若要让 Service 或 Harness UI 等宿主发现插件，在 `pyproject.toml` 的 `a13n_harness.plugins` 入口组中注册工厂函数：

```toml
[project.entry-points."a13n_harness.plugins"]
"acme.audit" = "acme_plugins:build_audit_plugin"
```

安装扩展包后，请在宿主配置中按 Key 显式启用对应工厂（例如 Service 的 `plugins.keys` 或 `AppDefinition.factory_keys`）。

## 提供方插件

自定义环境后端实现 `EnvironmentProvider` 契约并通过 Python Entry Point 发布，供宿主统一发现与配置。打包契约与示例见[提供方目录与插件](../environments/provider-plugins.md)。
