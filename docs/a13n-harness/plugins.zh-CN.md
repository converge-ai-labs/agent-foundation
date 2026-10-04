---
title: 插件与扩展
description: 选择范围最小的扩展点：Harness 中间件、Pydantic AI Capability、Environment Provider 绑定、Environment Run 扩展或 Provider 插件。
---

Harness 提供各有职责的扩展点，而非一个通用插件接口。请选择能够承载所需行为和生命周期的最小边界。

| 扩展点                       | 用途                                                                                                  | 生命周期                                         | 是否对模型可见                        |
| ---------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------- |
| Harness 中间件插件           | 转换语义输入、观测事件、封装错误或替换完整的候选结果                                                  | 构建时绑定 Agent，每个逻辑 Run 再重新绑定 Run    | 仅通过显式提供的 Capability           |
| Pydantic AI Capability       | 负责或组合 Toolset、指令、请求钩子、Agent 循环状态，以及与同一 Run 中其他 Capability 的协作           | 原生 Pydantic Agent/Run 生命周期                 | 是                                    |
| `EnvironmentProviderBinding` | 在异步 `bind()` 作用域中获取 Host 专用的会话或资源，并为一个挂载暴露 Provider 中立的 Environment 操作 | 在一个 `EnvironmentRuntime` 内拥有一个绑定作用域 | 仅通过显式的 Environment 工具或上下文 |
| `EnvironmentRunExtension`    | 持有需要完整且已进入的 Environment 聚合体的资源；简单的配对回调可使用 `EnvironmentRunCallbacks`       | 随当前聚合体进入，在 Provider 清理前按逆序退出   | 否                                    |
| Provider 插件                | 添加可供 Host 选择的 Environment Provider                                                             | Host 启动时加载一次，不产生运行效果的定义        | 否                                    |

已安装的入口点元数据只表示代码可用，不代表已启用或获得授权。导入 `a13n_harness` 不会扫描入口点，也不会激活任何扩展。

## 选择并激活扩展点

可用、选用和激活是三个独立的决定：

| 扩展点                       | 如何让实现可用                                                                | 如何选择并激活                                                                          | 自动行为                                                                                                     |
| ---------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Harness 中间件插件           | 安装带有 `a13n_harness.plugins` 入口点的包，或导入具体插件                    | 启用配置中的一个 `plugin_key`/`plugin_id`，或将具体插件传给 `HarnessBuilder.build()`    | 默认不读取环境配置；显式启用后，只加载 `enabled: true` 的条目                                                |
| Pydantic AI Capability       | 导入具体 Capability，或由受信任的 Host 授权一个明确的声明式类型               | 将实例纳入定义或 Run 的组合，或将序列化规格放入 `AgentSpec.capabilities`                | 不会根据已安装的包推断可选 Capability；参见 [Capability](capabilities.md#select-capabilities-with-agentspec) |
| `EnvironmentRunExtension`    | 安装带有 `a13n_harness.environment_run_extensions` 入口点的包，或导入具体扩展 | 选择明确的工厂键，创建带标识的实例，再传给 `create_environment_runtime(extensions=...)` | 不读取环境配置，也不自动选择                                                                                 |
| `EnvironmentProviderBinding` | 由负责的 Provider 层构造新的可信绑定                                          | 将绑定放入 `EnvironmentRuntimeMount`                                                    | Provider 可用本身不会触发挂载或向模型暴露工具                                                                |
| Provider 插件                | 安装带有 `a13n_harness.providers.plugins` 入口点的发行包                      | 在 Host 启用的插件列表中指定入口点，再按名称选择 Provider 类型                          | 安装不会激活任何功能；未选用的入口点不会被导入                                                               |

`AgentSpec` 只选择 Capability，不选择 Harness 中间件、Environment Run 扩展、Provider、凭据或实时协作对象。选定插件可以通过可信插件代码提供普通 Capability，但这些贡献由插件负责，不会从 `AgentSpec` 重建。

## Harness 中间件

Harness 插件是可信的 Python 中间件，包裹一次完整的进程内 Run。Agent 循环内部的行为应使用 Pydantic AI Capability；需要包裹语义输入、规范事件流、错误或完整结果边界时，则使用中间件。

可以直接传入 `AbstractHarnessPlugin` 实例，也可以通过选定的 `HarnessPluginFactory` 入口点创建插件。

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

直接传入和通过配置创建的插件，使用同一套排序、Agent 绑定、Run 绑定、中间件、结果校验和清理流程。

### 功能 Capability 与当前 Provider 客户端

中间件插件可以与内置功能 Capability 一起运行，无需自己提供或配置该 Capability。Host 在 Agent 定义中选择一个配置好的 `WebCapability`，另行提供当前客户端和策略。内置保留 Capability 的来源规则仍然适用：`WebCapability` 属于定义，不能由插件的 `get_capabilities()` 提供。

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

不要给插件添加配套的 Provider Capability，也不要把 Provider 对象放入插件 YAML。`RunBindings.capabilities` 仍用于提供调用策略和 MCP Capability，不用于提供被动的功能依赖。绑定不能启用缺少负责方的功能，Provider 客户端也不会进入 `HarnessState`。Host 负责这些客户端的生命周期；插件的 `for_run()` 仍是普通的中间件隔离钩子，不是另一套功能 API。

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

实现 `validate_configuration()`，用包自有的 schema 校验并规范化条目的 `configuration`；Harness 会在调用 `create_plugin()` 之前调用它。工厂构造同步执行，且不得产生副作用。`create_plugin()` 为每个配置实例返回新的具体插件。可变的 Run 数据应放在 `for_run()` 返回的对应 Run 绑定插件中。

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

配置按稳定的入口点键进行选择，不接受 `module:object` 目标。`HarnessBuildContext.extensions` 是带命名空间、受限的 JSON 值，会转交给选定的插件工厂；它虽然名为 extensions，但不是插件或 Environment 扩展列表，也不会启用任何功能。

### 可选的环境变量配置来源

配置插件默认关闭。部署可以显式启用由环境变量指定的配置文档：

```bash
export A13N_HARNESS_PLUGIN_CONFIG_ENABLED=true
export A13N_HARNESS_PLUGIN_CONFIG_FILE=/etc/a13n/harness-plugins.yaml
```

```python
builder = HarnessBuilder()
```

关闭时，构造 builder 不会读取该文件，也不会扫描包元数据。启用时，只读取一个来源：设置了 `A13N_HARNESS_PLUGIN_CONFIG_JSON` 时读取它，否则读取 `A13N_HARNESS_PLUGIN_CONFIG_FILE` 指定的文件，再否则读取当前工作目录中的 `harness-plugins.yaml`。来源缺失或无效时，构造失败。只导入已启用条目选定的工厂键。

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

显式启用配置插件的替代 builder，只会为其已启用条目选定的工厂键解析当前发行包元数据。关闭的 builder 仍不会扫描元数据。已有 builder 保留已选定的工厂目录，已有可执行对象保留已经构造的插件图。在旧可执行对象的活跃 Run 全部结束前，保持其可用。

Host 仍负责制品可信性、依赖兼容性、安装锁、目录顺序和回滚。Harness 不包含包安装器，也不提供进程全局的可变插件注册表。

## 插件生命周期

插件有三个独立阶段：

1. 配置插件的**工厂选择**在构造 builder 时完成，**插件创建**在每次 `build()` 时为每个根定义或子定义执行；
2. **绑定 Agent**：每个构建的根可执行对象或子可执行对象各执行一次；
3. **绑定 Run 并执行中间件**：每个逻辑 Run 都重新执行。

Run 中间件必须保持单消费者流式语义，并且恰好产出一个结构有效的候选结果。用 `try/finally` 清理插件持有的资源。不要吞掉取消信号，也不要把清理失败转成正常完成。

插件可以在绑定 Agent 时提供原生 Capability。Harness 会在 `for_agent()` 返回的实例上调用 `get_capabilities()`；不要在绑定前提取贡献。插件及其提供的 Capability 不应另行实现工具分发器、消息历史、用量累加器或 Environment 生命周期。

为支持可选的分组展示，插件可以为自身贡献暴露来源工厂或展示选项。Host 负责的组合层可以将选定来源聚合进一个 `ToolProxyCapability(groups=...)`，同时保留必需的中间件，让无关工具继续直接暴露。也可以用 `ToolProxyPlan` 按确切插件 ID 选择未经修改的插件的贡献；这不需要插件专用接口、通用查找或拦截任意插件。示例及重复安装的边界参见 [ToolProxy 的插件贡献来源](tool-proxy.md#plugin-contributed-sources)。

## Provider 插件

Provider 插件通过一个入口点组和一份不可变清单，添加一个或多个可供 Host 选择的 Environment Provider。Model、Web、Connector 和 Memory 定义使用同一套定义契约，但由 Host 在代码中组合，并通过自己的 `ProviderCatalog` 选择。

定义是冻结的值。它声明稳定的 `type`、`display_name`、输入所用的类型化配置模型和凭据模型、凭据要求，以及可选的设置帮助。导入定义不会执行 I/O，也不会创建客户端：

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

每一步都需要明确选择。安装发行包不会激活任何功能，未指定的入口点不会被导入，同一领域内存在重复定义类型时目录会拒绝它。对于部署未提供的类型，`require()` 抛出 `ProviderNotSelected`，让 Host 能够报告明确且安全的配置错误，避免意外失败。

可运行的[插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) 在同一个项目中发布一份清单和一个独立的 Harness 中间件插件。[已安装 Provider 插件示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/provider-plugin) 演示直接使用和 Harness UI 加载。

## Harness 可选依赖

基础 `a13n-harness` 安装包含所有内置 Provider 定义，因此元数据、schema 和 Host 配置表单（例如 Service 中的 Console 表单）无需可选依赖就能使用。厂商 SDK 作为独立的可选依赖提供：

| 可选依赖 | 添加内容             | 使用方                        |
| -------- | -------------------- | ----------------------------- |
| `docker` | Python 的 Docker SDK | `docker` Environment Provider |
| `e2b`    | 异步 E2B SDK         | `e2b` Environment Provider    |
| `modal`  | Modal SDK            | `modal` Environment Provider  |

```console
uv add "a13n-harness[docker,e2b]"
```

导入 Harness 或读取 Provider 元数据都不会导入这些 SDK。缺少可选依赖的 Provider 只会在真正打开时报告受限的配置错误，不会在导入时失败。

## Environment 输入与高级绑定

如果 Provider 已经构造了 `Environment`，可直接传给 `run(environment=...)`，也可以用 `EnvironmentMount` 包装，以选择权限上限和路径。显式 runtime 及其动态 `mount()`、`replace()` 方法接受相同输入。Harness 负责进入和本地清理，Host 代码无需实现转发绑定类：

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

`on_enter` 在可移植 Environment 状态恢复之后、runtime 激活之前运行。它参与使 runtime 进入活跃状态，但不代表所有操作族都已全局就绪。如果初始化依赖某个明确的操作族，请调用 `context.environment.ensure_ready()`。`on_exit` 在 Environment 按逆序清理时运行，此时 Provider 中立操作仍可用。成功进入之后发生失败、取消或回滚时，它也会运行，因此它用于清理，不是成功通知。

每个适配器都是带标识的独立扩展。多个适配器按注册顺序进入，按逆序退出。回调失败采用与自定义扩展初始化、清理相同的权威失败语义。只有 Host 明确希望尽力执行时，才应在回调内部捕获预期失败。

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

扩展在 Provider 可用、可移植 Environment 状态恢复后进入；在 Environment 仍打开且 Provider 作用域尚未关闭时，按逆序退出。

### 显式扩展工厂

发行包可以在以下入口点组中注册无副作用的工厂：

```toml
[project.entry-points."a13n_harness.environment_run_extensions"]
"acme.workspace-marker" = "acme_environment.extension:WorkspaceMarkerFactory"
```

Host 通过 `build_environment_run_extension_factory_catalog()` 明确选择键，也可以提供明确的工厂。Harness 不提供通过环境配置选择 Environment Run 扩展的文档。使用已安装工厂的完整流程如下：

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

`extension_key` 选择一个已安装的工厂；`extension_id` 标识一个具体的聚合实例，在该 runtime 内必须唯一。构建目录只会导入明确选定的键。直接传入具体扩展则完全跳过元数据发现。

## 可运行示例

[集成包示例](https://github.com/converge-ai-labs/agent-foundation/tree/main/examples/plugins) 包含 Host 授权的自定义 Capability 选择、可打包为 wheel 的中间件和 Environment 扩展入口点、直接代码组合、YAML 选择、公共 Harness 执行接口、逐 Run 隔离，以及离线测试。
