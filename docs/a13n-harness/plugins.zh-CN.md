---
title: 插件与扩展
description: 添加 Run 中间件，将其打包为可配置插件，并管理 Environment 的初始化与清理。
---

需要观察或改变完整 Run 时，先直接传入插件对象。本页从离线审计插件开始，将同一个实现封装成工厂，再演示 Environment 的初始化与清理。使用[快速开始](getting-started.md)中的 Python 环境；两个独立程序均无需 Model 凭据。

## 选择并激活扩展点

根据行为的职责选择扩展：

| 任务                               | 扩展                          | 选择方式                                     |
| ---------------------------------- | ----------------------------- | -------------------------------------------- |
| 包装语义输入、执行、错误或完整结果 | Harness 中间件                | `build(plugins=...)` 或启用已安装的工厂      |
| 添加工具、指令或 Agent 循环钩子    | [Capability](capabilities.md) | 定义或 Run 组合                              |
| 首次使用时准备 Host 目标           | `EnvironmentSource`           | `run(environment=...)` 或 `EnvironmentMount` |
| 为多个挂载初始化和清理资源         | `EnvironmentRunExtension`     | `create_environment_runtime(extensions=...)` |
| 向 Host 发布 Environment 后端      | Provider 插件                 | 先选择入口点名称，再选择 Provider 类型       |

安装包使扩展可用，Host 仍需显式选择。`AgentSpec` 选择 Capabilities；中间件和 Environment 扩展使用各自的组合 API。

## Harness 中间件

中间件包裹完整 Run。下面的插件在执行前发出一条观察事件，收到内层结果后再发出一条。Run 期间配置不变，因此同一个实例可以服务并发调用。

### 包装执行并发出观察事件

保存为 `plugin_demo.py`，运行 `python plugin_demo.py`。断言会检查两条观察事件与最终输出。

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

`wrap_run()` 最多等待一次 `call_next(exchange)`，并返回完整的 `HarnessRunResult`。需要改变输入时，将 `exchange.with_input(...)` 传给 continuation；需要短路时，不调用 continuation，直接返回有效结果。

Run 通过有界事件通道投递扩展观察事件，消费缓慢会产生背压。中间件通过 `context.events` 发出事件，由调用方消费流。这里的 `after` 表示内层调用已返回；需要在失败时也清理资源，应使用 `finally`。

### 直接组合

示例通过 `build(plugins=...)` 选择 `AuditPlugin("audit-primary")`。嵌入式应用可以直接使用这种方式，工厂发现是可选的。`configured_plugins_enabled=False` 让独立示例不受部署插件配置影响。

每个插件需要唯一 ID。默认按定义顺序排列；`PluginOrdering` 可以增加 `wraps`、`wrapped_by` 或最外层、最内层约束。`requires` 只检查存在性，不决定两者顺序。无效引用和循环依赖会使构建失败。

### 功能 Capability 与当前 Provider 客户端

Web 等功能有两类输入：定义启用工具，每个 Run 接收当前客户端和策略。下面的辅助函数复用 `plugin_demo.py` 中的 `AuditPlugin`；应用提供 Model 和由 Host 管理的 `WebBinding`。

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

将 `WebCapability` 放在定义中，而不是通过插件贡献。Host 管理客户端生命周期，`HarnessState` 不保存活跃客户端。完整的测试客户端和实际离线 `fetch` 调用见[中间件与 Web 示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins#middleware-and-feature-with-host-bindings)。

### 发布插件工厂

Host 需要通过配置选择插件时，再添加工厂。将 `AuditPlugin` 及其 import 语句移入包内的 `acme_harness/plugin.py`，并在同一模块添加以下类。`label` 字段会进入诊断事件的 payload，实际影响输出。

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

在发行包的 `pyproject.toml` 中注册无参工厂类。入口点名称必须等于 `plugin_key()`。

```toml
[project.entry-points."a13n_harness.plugins"]
"acme.audit" = "acme_harness.plugin:AuditPluginFactory"
```

工厂校验和构造不应执行 I/O。通过 `for_run()` 分配每个 Run 的可变状态，不要将其存放在工厂中。

### 为配置编辑器提供描述

`configuration_model` 同时用于校验和编辑器 Schema 生成，后者调用 `model_json_schema(mode="validation")`。默认值、约束、说明和校验器均放在这个模型中；静态 `display_name` 与 `description` 提供不含凭据的编辑器标签。

工厂只实现自定义 `validate_configuration()` 而未声明模型时，可以使用 JSON 编辑器。Schema 生成失败时，Service 也会退回 JSON 编辑。Schema 在查询插件目录时生成；JSON Schema 无法表达的约束仍由后端校验。

### 通过配置选择插件

先将发行包安装到 Host 使用的 Python 环境，再将以下内容保存为 `harness-plugins.yaml`：

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

用这个 `builder` 构建 Agent，它会添加配置中的插件。移除前例直接传入的 `AuditPlugin("audit-primary")`，避免 ID 重复。构造 builder 时解析已安装工厂；每次 `build()` 都为根定义和子定义校验配置、创建插件。

同一文档也支持 JSON，或通过 `HarnessBuildContext.from_configuration(mapping)` 传入。`plugin_key` 选择已安装入口点，`plugin_id` 标识实例。`HarnessBuildContext.extensions` 向工厂传递命名空间 JSON；凭据和活跃客户端保留在 Host 管理的绑定中。

### 可选的环境变量配置来源

需要由部署设置选择文档时，在构造 `HarnessBuilder()` 前启用环境配置：

```bash
export A13N_HARNESS_PLUGIN_CONFIG_ENABLED=true
export A13N_HARNESS_PLUGIN_CONFIG_FILE=/etc/a13n/harness-plugins.yaml
```

环境配置默认关闭。启用后，`A13N_HARNESS_PLUGIN_CONFIG_JSON` 优先于指定文件，指定文件优先于工作目录中的 `harness-plugins.yaml`。选定配置缺失或无效会使构造失败。只有启用的条目加载工厂；显式 build context 跳过环境配置来源查询。

### 运行时插件目录

长期运行的 Host 要加载新发行包时：

1. 将完整发行包安装到新的不可变目录，包括包文件和入口点元数据。
2. 校验后再发布目录。
3. 将该目录加入 `sys.path`，并调用 `importlib.invalidate_caches()`。
4. 构造替代 builder 和可执行对象。
5. 保留旧可执行对象，直到其中的活跃 Run 结束。

单个 Python 文件不提供发行包入口点。已有 builder 保留工厂目录，已有可执行对象保留插件图。此流程用于新增发行包，不是热替换已导入模块。Host 负责兼容性和回滚；不要发布两个占用同一选定键的发行包。

## 插件生命周期

将数据放在负责其生命周期的位置：

| 阶段         | 钩子                                     | 保存的内容            |
| ------------ | ---------------------------------------- | --------------------- |
| Builder/定义 | 选择工厂，再为每个定义创建插件           | 已校验配置            |
| 可执行对象   | `for_agent()`，然后 `get_capabilities()` | 可重入的 Agent 级行为 |
| 逻辑 Run     | `for_run()`，然后 `wrap_run()`           | 新的可变 Run 状态     |

绑定替代实例须保持具体类型、ID 和排序。审计示例没有可变 Run 状态，可沿用默认绑定钩子返回自身。Capability 可在绑定完成后通过 `ctx.deps.plugins.require(plugin_id, ExpectedType)` 查找 Run 绑定插件，参阅 [ToolSearch 贡献](tool-search.md#plugin-contributed-sources)。

在 `finally` 中清理资源，并继续传播取消和清理失败。Harness 校验每个返回的结果。如果替代结果返回前清理抛出异常，则保留最近的有效内层结果，但不投递终态。导入的插件拥有 Python 进程权限，因此应安装可信代码。

## Provider 插件

其他 Host 需要选择你的 Environment 后端时，发布 Provider 插件。从完整的[工作区 Provider 实现](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/src/a13n_plugin_examples/environment.py)开始：

1. 定义配置模型，以及可选的凭据模型。
2. 实现 `describe_environment`，不在其中准备目标。
3. 实现 `connector_factory`，返回构造时无副作用的执行 connector。
4. 将 `EnvironmentProviderDefinition` 放入 `ProviderManifest(api_version=1)` 导出。
5. 在 `a13n_harness.providers.plugins` 注册 manifest，由 Host 显式选择入口点名称和 Provider 类型。

Host 准备和销毁目标。重复 Provider 类型会使目录构造失败；类型不可用时，`ProviderCatalog.require()` 抛出 `ProviderNotSelected`。Model、Web、Connector 和 Memory 定义通过代码中的 `ProviderCatalog` 组合；安装式 Provider manifest 导出 Environment 定义。

使用这一 Environment API 发布的插件要求 `a13n-harness>=0.10.0,<0.11.0`。Provider manifest 版本与中间件配置的 `schema_version="1"` 分开。普通中间件加载沿用 0.9.3 契约；导入旧 Environment API 的中间件需要迁移源码。[已安装 Provider 教程](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/provider-plugin)包含直接使用与 Harness UI 选择步骤。

## Harness 可选依赖

打开 Provider 前，安装对应后端的可选依赖：

| 可选依赖 | 依赖内容          | Provider |
| -------- | ----------------- | -------- |
| `docker` | Python Docker SDK | `docker` |
| `e2b`    | 异步 E2B SDK      | `e2b`    |
| `modal`  | Modal SDK         | `modal`  |

例如，`uv add "a13n-harness[docker,e2b]"` 安装两种后端依赖。查看元数据不需要厂商 SDK，打开后端则需要对应可选依赖。

## 环境输入与来源

使用 `EnvironmentSource` 将 Host 的准备工作接入 Run。可将 source 传给 `run(environment=...)`，也可用 `EnvironmentMount` 设置路径和权限。下面的回调示例为已准备好的本地目录提供完整 `PreparedSource`；[环境](environments.md#supply-a-source)页单独解释这一接口。

静态注册发布挂载元数据。首次依赖该挂载的操作调用 `ensure_ready()`，打开新的 execution，并由 Harness 负责 execution 清理。挂载的 `permission_ceiling` 列出精确操作，Provider 权限可进一步收窄。复用其他挂载拥有的 execution 会被拒绝，原有者的资源不会被关闭。

### Host 准备回调

将 `provider_key`、`environment_id`、`descriptor` 和 `state` 保持为无 I/O 元数据。在 `ensure_ready()` 中分配资源、发布当前状态并等待就绪；目标可用后再返回 connector。Host 处理管理失败与持久状态，Harness 不自动重试同一挂载的激活失败。

## Environment Run 扩展

资源需要跨越整个已进入 Environment 的生命周期时，使用 Run 扩展；空 runtime 和多挂载 runtime 也适用。扩展在首次 Model 请求前看到静态挂载集合，并在已打开的 execution 关闭前退出。

### 组合回调

将这个独立程序保存为 `environment_extension_demo.py`。它在临时目录创建标记，在输入准备阶段读取标记，并在 Run 结束后检查清理是否删除了标记。

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

运行 `python environment_extension_demo.py`，预期输出 `Extension example completed. Marker removed.`。首次文件操作准备挂载。单纯进入扩展不会准备目标；初始化需要某操作族就绪时，可从 `a13n_harness.environment` 导入 `EnvironmentReadinessRequirement`，再显式调用 `context.environment.ensure_ready(EnvironmentReadinessRequirement(operations=frozenset({"files"})))`。

回调按注册顺序进入、逆序退出。成功进入后，即使后续失败或取消，`on_exit` 也会执行。如果 `on_enter` 自身在初始化中途失败，该回调负责清理已完成的部分。

### 自定义资源作用域

初始化和清理需要共享局部状态时，在环境示例的 `main()` 前添加这个类：

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

在 runtime 的 `extensions` 元组中，将 `WorkspaceMarkerExtension("custom-marker", "/workspace/.custom-run")` 与回调适配器一起传入。每个扩展需要独立 ID 和资源路径。通过新的 `RunBindings.embedded(environment=runtime)` 将 runtime 交给 Run，由 Harness 负责进入和关闭。

### 显式扩展工厂

需要通过配置选择扩展时，在 `a13n_harness.environment_run_extensions` 入口点组注册无副作用工厂。`build_environment_run_extension_factory_catalog(extension_keys=(... ,))` 加载指定的已安装键；`explicit_factories=(factory,)` 接收本地工厂对象，无需安装。

调用 `catalog.create_extension(EnvironmentRunExtensionFactoryContext(extension_key=..., extension_id=..., configuration=...))`，将返回对象放入前例的 `extensions` 元组。键选择工厂，ID 标识 runtime 中的唯一实例。[完整工厂实现](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/src/a13n_plugin_examples/environment_extension.py)和[端到端驱动程序](https://github.com/converge-ai-labs/agent-foundation/blob/main/examples/plugins/src/a13n_plugin_examples/demo_environment_extension.py)提供配置模型、安装式选择、显式选择和标记断言。

## 可运行示例

准备验证打包时，使用[集成包](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)。按其中的安装和运行命令验证元数据发现与直接组合。本页两个程序在本地验证中间件和 Environment 生命周期，集成包进一步覆盖发行包入口点路径。

## 迁移中间件

将旧 `PluginRunResponse` 和异步事件生成器改为 `async def wrap_run(...)`、一次等待 continuation，以及返回结果。通过 `context.events.emit()` 发出观察事件。

原生事件和 Run 生命周期事件由 Harness 管理。需要改变展示时，在 Host 共用的实时与持久化 fold 前配置 Stream Protocol `DisplayFold` processor，恢复时使用同一个 processor。
