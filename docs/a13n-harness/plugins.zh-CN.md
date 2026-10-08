---
title: 插件与扩展
description: 选择范围最小的扩展点：Harness 中间件、Pydantic AI Capability、Environment Provider 绑定、Environment Run 扩展或 Provider 插件。
---

按要添加的行为选择扩展：

| 扩展点                       | 用途                                |
| ---------------------------- | ----------------------------------- |
| Harness 中间件插件           | 包装输入、执行、错误或最终 Run 结果 |
| Capability                   | 添加工具、指令或 Agent 循环行为     |
| `EnvironmentProviderBinding` | 暴露一个 Host 资源的操作            |
| `EnvironmentRunExtension`    | 为已进入的多个挂载执行初始化和清理  |
| Provider 插件                | 向 Host 提供 Environment Provider   |

安装包后显式选择扩展。仅安装不会启用。

## 选择并激活扩展点

通过各扩展对应的 API 选择：

| 扩展点           | 选择方式                                                           |
| ---------------- | ------------------------------------------------------------------ |
| 中间件           | 向 `build()` 传入具体插件，或启用配置中的 `plugin_key`/`plugin_id` |
| Capability       | 加入定义或 Run 组合，见[Capabilities](capabilities.md)             |
| Run 扩展         | 向 `create_environment_runtime(extensions=...)` 传入实例           |
| Provider binding | 向 `EnvironmentRuntimeMount` 加入新 binding                        |
| Provider 插件    | 在 Host 配置中选择入口点名称，再选择 Provider 类型                 |

`AgentSpec` 选择 Capabilities；中间件和 Environment 扩展另行配置。

## Harness 中间件

包装整个 Run 时使用中间件；Agent 循环内的行为使用[Capability](capabilities.md)。

可以直接传入 `AbstractHarnessPlugin` 实例，也可以通过选定的 `HarnessPluginFactory` 入口点创建插件。

### 包装执行并发出观察事件

实现异步 `wrap_run()` 并返回 `HarnessRunResult`。最多等待一次 `call_next(exchange)`，也可以直接返回完整结果以短路执行。通过 `exchange.with_input(...)` 替换语义输入。

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

中间件执行期间，Run 通过有界通道投递扩展事件。调用 continuation 之前、之后以及短路执行期间都可以发出事件。消费者较慢时会施加背压；中间件不拥有或迭代事件流。

**破坏性变更迁移：** 将 `PluginRunResponse` 和异步事件生成器改为 `async def wrap_run(...)`、`await call_next(...)` 及返回结果。通过 `context.events.emit()` 发出扩展观察事件。原生事件和 Run 生命周期事件仍由 Harness 管理。需要调整展示时，在 Host 共用的实时与持久化 fold 之前配置 Stream Protocol `DisplayFold` processor，恢复 fold 时使用相同的 processor。不要在中间件中改写原生事件。

### 直接组合

嵌入式应用最简单的方式是直接传入对象：

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

### 功能 Capability 与当前 Provider 客户端

在定义中选择功能 Capability，通过 Run bindings 提供当前客户端：

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

`WebCapability` 放在定义中，不通过插件贡献。Host 管理 provider 客户端及其生命周期；`HarnessState` 不保存客户端。

[离线中间件与 Web 示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins#middleware-and-feature-with-host-bindings) 将记录插件、由定义负责的 Web 功能、新的类型化绑定和标准 `fetch` 工具组合起来，无需发起网络请求即可演示。

### 发布插件工厂

注册一个无需参数的工厂类：

```toml
[project.entry-points."a13n_harness.plugins"]
"acme.audit" = "acme_harness.plugin:AuditPluginFactory"
```

入口点名称必须与 `plugin_key()` 一致：

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

在 `validate_configuration()` 验证配置，再创建新插件。工厂构造不执行 I/O，可变 Run 数据放入 `for_run()`。

### 通过配置选择插件

Harness 插件文档定义的是数据结构，不要求使用特定文件格式：

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

同一结构可以由 YAML 或 JSON 提供：

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

选择入口点键，不使用 `module:object` 路径。`HarnessBuildContext.extensions` 向选定工厂传递命名空间 JSON。

### 可选的环境变量配置来源

配置插件默认关闭。部署可以显式启用由环境变量指定的配置文档：

```bash
export A13N_HARNESS_PLUGIN_CONFIG_ENABLED=true
export A13N_HARNESS_PLUGIN_CONFIG_FILE=/etc/a13n/harness-plugins.yaml
```

```python
builder = HarnessBuilder()
```

来源优先级为 JSON、指定文件、工作目录中的 `harness-plugins.yaml`。配置缺失或无效时构建失败，仅加载已启用条目。

### 运行时插件目录

长期运行的 Host 可以将完整安装的发行包发布到新的不可变目录，并在构造替代 builder 前将该目录加入 `sys.path`：

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

目录必须同时包含可导入的包，以及带有入口点的标准发行包元数据。只有一个独立的 `.py` 文件是不够的。

按以下步骤操作：

1. 发布到尚未纳入搜索路径的新目录。
2. 校验完整安装。
3. 以原子方式放置该目录。
4. 激活该路径。
5. 构建替代可执行对象。

不要原地修改已经导入的版本，也不要添加两个占用同一插件键的版本。

新 builder 解析已选的安装工厂。已有 executable 保留原插件图，待活跃 Run 结束后再释放。

Host 管理包安装、兼容性与回滚。

## 插件生命周期

插件有三个独立阶段：

1. 配置插件的**工厂选择**在构造 builder 时完成，**插件创建**在每次 `build()` 时为每个根定义或子定义执行；
2. **绑定 Agent**：每个构建的根可执行对象或子可执行对象各执行一次；
3. **绑定 Run 并执行中间件**：每个逻辑 Run 都重新执行。

从 `wrap_run()` 返回一个有效结果。在 `finally` 清理插件资源，继续传播取消和清理失败。Harness 验证每个成功返回的结果。如果 `finally` 抛出异常，导致替换结果未能返回，则保留最近一次通过验证的内层结果。

在 `for_agent()` 返回的 Agent 绑定实例中，通过 `get_capabilities()` 贡献 Capabilities。

工具分组见[ToolProxy 插件贡献来源](tool-proxy.md#plugin-contributed-sources)。

## Provider 插件

Provider 插件发布 Environment 定义供 Host 选择。Model、Web、Connector 和 Memory 定义通过代码中的 `ProviderCatalog` 组合。

声明 Provider 类型、显示名称、配置、凭据与 runtime factory：

```python
from a13n_harness.providers.authentication import Authentication, CredentialMode
from a13n_harness.providers.environment.definition import EnvironmentProviderDefinition

ACME_SANDBOX = EnvironmentProviderDefinition(
    type="acme_sandbox",
    display_name="Acme Sandbox",
    configuration_model=AcmeConnectionConfiguration,
    credential_model=AcmeCredential,
    environment_model=AcmeEnvironmentConfiguration,
    construct=_construct,
    describe_environment=_describe,
    runtime_factory=_runtime,
    authentication=Authentication(mode=CredentialMode.required),
    setup_url="https://acme.example/dashboard",
    setup_label="Acme dashboard",
    supports_stop=True,
    supports_destroy=True,
)
```

一个发行包为每个入口点导出一个 `ProviderManifest`：

```python
from a13n_harness.providers.plugins import ProviderManifest

manifest = ProviderManifest(api_version=1, environment=(ACME_SANDBOX,))
```

```toml
[project.entry-points."a13n_harness.providers.plugins"]
acme = "acme_providers:manifest"
```

Host 指定自己信任的入口点，并根据内置定义和选定定义构建一个 Environment 目录：

```python
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.plugins import load_provider_plugins

plugins = load_provider_plugins(("acme",))
environments = ProviderCatalog(
    item for plugin in plugins for item in plugin.manifest.environment
)
definition = environments.require("acme_sandbox")
```

显式选择入口点名称和 Provider 类型。重复类型使目录构建失败，`require()` 对不可用类型抛出 `ProviderNotSelected`。

可运行的[插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) 在同一个项目中发布一份清单和一个独立的 Harness 中间件插件。[已安装 Provider 插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/provider-plugin) 演示直接使用和 Harness UI 加载。

## Harness 可选依赖

Harness 自带内置 Provider 定义。使用对应后端时安装 extra：

| 可选依赖 | 添加内容             | 使用方                        |
| -------- | -------------------- | ----------------------------- |
| `docker` | Python 的 Docker SDK | `docker` Environment Provider |
| `e2b`    | 异步 E2B SDK         | `e2b` Environment Provider    |
| `modal`  | Modal SDK            | `modal` Environment Provider  |

```console
uv add "a13n-harness[docker,e2b]"
```

读取 Provider 元数据无需厂商 SDK；缺少 extra 时，Provider 打开失败。

## Environment 输入与高级绑定

将已构造的 `Environment` 传给 `run(environment=...)`。权限上限与路径通过 `EnvironmentMount` 设置；动态挂载使用显式 runtime：

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

`permission_ceiling` 接受明确的任意操作集合，适合只需要部分文件操作的初始化扩展。Provider 权限始终只能缩小权限上限。Runtime 对每个底层 Environment 只接管一次所有权，即使它又包了一层 `EnvironmentMount` 也一样。无效的初始路由不会接管所有权，失败的复用尝试也不能关闭该 Environment 已有的作用域。

### 高级 Provider 绑定作用域

如果 Host 必须在自定义异步 `bind()` 作用域中获取已认证的会话或其他资源，可将 `EnvironmentProviderBinding` 与 `EnvironmentRuntimeMount` 配合使用。随后由该绑定暴露 Provider 中立的文件、shell、进程、输出、端口、就绪状态和可移植状态操作。显式 runtime 构造和动态挂载替换仍接受这种高级输入。已有 Environment 应使用上文的直接输入。

这是底层 runtime 绑定契约。Provider 目录、Environment Provider 生命周期操作、凭据处理和持久 Provider 状态由 [Provider 插件](#provider-plugins)和 Host 负责，不属于 Harness 中间件。

`EnvironmentProviderBinding` 是新建且只能使用一次的绑定。会产生实际影响的分配、认证、会话进入、维护任务，以及需要后续清理的工作，必须放在其异步 `bind()` 作用域或负责的 Provider 层中，不能放在导入时的发现流程或不产生运行效果的工厂构造函数中。

## Environment Run 扩展

当初始化和清理需要稳定、完整的 `EnvironmentRuntime` 时，可使用 `EnvironmentRunExtension`；它适用于空 runtime、单挂载 runtime 和多挂载 runtime。

### 组合回调

对于普通的 Host 初始化和清理，注册 `EnvironmentRunCallbacks` 适配器即可，无需定义扩展类：

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

`on_enter` 在 Harness 进入 Host 已构造的 adapter 后、首次模型请求前运行。初始化需要特定操作族时，显式请求就绪：

```python
from a13n_harness.environment import EnvironmentReadinessRequirement

await context.environment.ensure_ready(
    EnvironmentReadinessRequirement(operations=frozenset({"files"}))
)
```

`on_exit` 在 adapter 关闭前运行；成功进入后发生失败或取消时也会运行。用它执行清理。

扩展按注册顺序进入，按逆序退出。

### 自定义资源作用域

如果初始化和清理共享本地状态，或需要更丰富的资源作用域，请使用自定义异步上下文管理器：

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

在显式 runtime 上注册直接扩展对象，同时使用普通的 Environment 输入：

```python
from a13n_harness.environment.advanced import create_environment_runtime


environment_runtime = create_environment_runtime(
    mounts={"workspace": environment},
    default_mount="workspace",
    extensions=(WorkspaceMarkerExtension("workspace-marker"),),
)
```

通过新的 Run 绑定将 runtime 交给 Harness，由 Harness 完成绑定、激活和关闭：

```python
from a13n_harness import RunBindings

bindings = RunBindings.embedded(environment=environment_runtime)
result = await executable.run("Use the prepared workspace", bindings=bindings)
```

同一个 `extensions=` 序列可以同时接受回调适配器和自定义扩展对象：

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

Host 在构造 adapter 时提供当前 Provider 状态。扩展在 adapter 进入后进入，在 adapter 关闭前按逆序退出。

### 显式扩展工厂

发行包可以在以下入口点组中注册无副作用的工厂：

```toml
[project.entry-points."a13n_harness.environment_run_extensions"]
"acme.workspace-marker" = "acme_environment.extension:WorkspaceMarkerFactory"
```

用 `build_environment_run_extension_factory_catalog()` 选择工厂键，再创建实例：

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

`extension_key` 选择工厂，`extension_id` 在 runtime 内必须唯一。直接传入对象不需要入口点发现。

## 可运行示例

[集成包示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins)演示安装式与直接组合，并提供离线测试。
