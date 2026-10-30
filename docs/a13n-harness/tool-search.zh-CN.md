---
title: ToolSearch 工具分组
description: 为工具集合分组，按需读取定义，再通过简短的模型接口调用选定工具。
---

Agent 的工具较多时，可以按领域将相关工具分组，让模型按需找到操作，无需在每次请求中接收所有 schema。ToolSearch 提供五个固定控制工具，负责发现和调用。选中的成员通过这些控制工具访问，无关工具仍直接提供给模型。

## 使用固定代理

模型按以下路径使用工具：

1. 用 `discover_tools` 搜索候选，或用 `list_tool_groups`、`list_tools` 浏览目录。
2. 尚未掌握当前 schema 时，用 `describe_tool` 读取候选工具的完整定义。
3. 用返回的分组、本地工具名和参数调用 `call_tool`。

发现工具不会把成员 schema 加入后续模型请求。调用仍通过当前 ToolManager 和 Harness 执行边界到达原来的 Toolset，保留参数校验、策略、钩子、凭据和结果处理。

`ToolSearchCapability` 和 `ToolSearchPlan` 默认使用 `ProxyToolSearchConfig()`，`ToolSearchConfig()` 是它的别名。固定的控制 schema 和指引不包含变化中的工具目录，有助于保持模型提示前缀稳定。分组工具也支持审批和[客户端工具反馈](client-tools.md)。

## 为 Toolset 分组

使用[快速开始](getting-started.md)中的环境，将以下完整程序保存为 `search_demo.py`。确定性模型会发现工具、读取选中工具的 schema，再调用本地函数，无需 provider 凭据。

```python
import asyncio
import json

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


async def respond(messages, info):
    names = {tool.name for tool in info.function_tools}
    assert "customer_name" not in names and "crm__customer_name" not in names
    returns = [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    if not returns:
        name, args = "discover_tools", {"query": "customer name", "group": "crm"}
    elif returns[-1].tool_name == "discover_tools":
        assert returns[-1].content["detail"] == "summary"
        match = returns[-1].content["tools"][0]
        name, args = "describe_tool", {"group": match["group"], "tool": match["tool"]}
    elif returns[-1].tool_name == "describe_tool":
        definition = returns[-1].content["tool"]
        assert "customer_id" in definition["parameters_json_schema"]["properties"]
        name, args = "call_tool", {
            "group": "crm", "tool": "customer_name", "arguments": {"customer_id": 42}
        }
    else:
        assert returns[-1].content == "Ada"
        yield "Customer Ada"
        return
    yield {0: DeltaToolCall(name=name, json_args=json.dumps(args))}


async def main():
    source = FunctionToolset(
        [customer_name], instructions="Use numeric customer IDs, not display names."
    )
    proxy = ToolSearchCapability(
        groups={
            "crm": ToolSearchGroup(source=source, description="Customer records")
        }
    )
    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=respond),
        capabilities=(proxy,),
    )
    result = await agent.run("Find customer 42")
    assert result.output_or_raise() == "Customer Ada"
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

在仓库根目录运行：

```bash
uv run --no-sync python search_demo.py
```

预期输出：

```text
Customer Ada
```

`source` 提供 Python 函数及其指令，`ToolSearchGroup` 将来源划入 `crm` 领域，一个 `ToolSearchCapability` 安装分组接口。断言表明，模型始终没有收到可直接调用的 `customer_name` schema，但代理调用仍执行了该函数并返回 `Ada`。

`respond()` 中的三个工具输入展示了调用协议：

| 工具             | 输入                                     | 下一步使用的结果           |
| ---------------- | ---------------------------------------- | -------------------------- |
| `discover_tools` | 查询 `customer name`，分组 `crm`         | 候选的分组和本地名称       |
| `describe_tool`  | 分组 `crm`，工具 `customer_name`         | 完整参数 schema 和来源指令 |
| `call_tool`      | 同一工具身份，参数 `{"customer_id": 42}` | 原始函数结果 `Ada`         |

函数目标保留原来的参数校验器。外部目标保留外部工具语义，由客户端在执行前校验参数。示例目标在内部注册为 `crm__customer_name`，受管理工具的 ID 保持不变。

按 `crm`、`knowledge`、`billing` 等领域分组，每组放入多个相关工具。组名以字母开头，最多包含 32 个字母、数字、连字符或下划线，不能包含 `__`。描述必须非空，最多 512 个字符。不同组可以使用相同的本地工具名。同组内可通过 `CombinedToolset`、`Capability(toolsets=[...])` 或 `CombinedCapability` 组合来源，但成员名必须唯一。普通来源名称冲突时，先改名再分组；客户端声明使用其支持的分组路径。

注册名称先将组名中的每个下划线转为 `_u`，再拼接 `__` 和不变的本地工具名。因此，`my_tools/select` 变为 `my_utools__select`，`a_/b` 也与 `a/_b` 不同。向客户端投递时，使用[保存的路由元数据](client-tools.md#proxy-discovery)，不要拆分字符串推断名称。

## 指令与动态参数

来源 Toolset 指令随完整定义返回。摘要列表不读取这些指令，也不复制 schema。独立的 Capability 级指令保留原有行为。

控制工具不包含动态 `group` 枚举。完整发现、列表和详情查询读取当前准备后的成员 schema；`call_tool` 始终保持固定参数对象结构。

生成的指令告诉模型：

1. 选择领域并发现候选；检查 `detail`，若返回摘要且尚无完整有效定义，先读取所选工具详情；
2. 使用返回的本地名称和匹配完整 schema 的参数对象；
3. 复用已知且当前有效的 schema，不必每次调用前都搜索；
4. 分页读取，工具不可用时刷新发现结果；
5. 核查不确定副作用，不盲目重试修改。

空查询或纯空白查询浏览一个或所有组。显式 `group` 先限定候选范围。精确匹配本地名称或实际注册的完整名称时排在最前，忽略大小写和首尾空白，但保留分隔符差异。完整名称包含组名下划线的转义：组 `a_` 的工具 `b` 对应 `a_u__b`，组 `a_u` 的同名工具对应 `a_uu__b`。其他关键词候选仍然保留：即使存在 `payment`，搜索 `payment` 也能找到 `payment_lookup` 和 `refund_payment`。规范名称查询不会隐式限定组；使用返回的 group 和本地名称查询目标工具详情。

查询按空白、标点、下划线、连字符和 camelCase 边界拆分关键词，任一关键词不区分大小写的子串命中即可召回。检索字段包括本地名称、完整工具描述、组标识、输入属性名和输入 schema 描述。会扫描嵌套参数和本地 schema 定义，但不展开引用。默认值、示例、枚举值、返回 schema、来源指令和组描述不参与匹配。

结果先按精确名称匹配排序，其次按命中的不同关键词数量，再按相关性，最后按规范名称稳定排序。每个关键词只计最强的一次匹配：完整词优于子串；每一档内的字段优先级为本地名称、工具描述、组标识、参数名、参数描述。重复关键词或重复文本不增加权重。摘要默认每页 10 条，受配置条数和字节上限约束。使用简短关键词；非空纯标点查询必须精确命中工具名。camelCase 会影响拆词，因此不同大小写的查询可能拆分不同，但精确名称和关键词比较均忽略大小写。这是确定性本地搜索，不提供语义检索或中文语义分词。

每次发现可选择详情模式：

| `detail`       | 行为                                                                                                           |
| -------------- | -------------------------------------------------------------------------------------------------------------- |
| `auto`（默认） | 返回摘要；仅当整个候选集只有一个精确名称命中、offset 为 0、完整响应不超过 8 KiB 且在配置字节上限内时自动展开。 |
| `summary`      | 始终返回候选；描述压缩空白，最多 256 字符，包含截断时的省略号。                                                |
| `full`         | 返回完整定义，不静默降级为摘要。                                                                               |

唯一性在分页前按整个候选集判断。单个模糊结果、`limit=1` 或字节限制导致的一页单条结果不触发自动展开。响应标记实际表示，一页不混合摘要和完整定义。已知工具身份时，可直接调用 `describe_tool(group=..., tool=...)`，无需先搜索。发现和详情查询都不执行业务操作。

使用相同 query、group 和 detail 跟随 `next_offset`；字节预算可能让一页少于请求条数。offset 基于当前目录，不是快照或持久游标；目录变化可能使分页位置移动。关闭 Toolset 指令也会关闭生成的引导和完整定义中的来源指令。

## 获取完整列表

调用 `list_tool_groups` 获取可用分组及工具数量，再调用 `list_tools` 枚举工具。两者都返回 `total` 和 `next_offset`；沿用参数并持续请求下一页，直到 `next_offset` 为 null。

`list_tools` 可按 `group` 筛选，`detail` 为 `summary`（默认）或 `full`。完整模式返回全部描述、输入/返回 schema 和来源指令，不进行相关性过滤。列表受数量和字节限制；无法单独放入一页的完整定义会明确报错，而不是截断。分页针对当前目录，目录变化可能改变页边界。

## 配置名称与发现限制

在 `search_demo.py` 中，用以下代码替换 `main()` 内的 `proxy` 构造，并添加导入。保留同一函数中的 `source`。确定性模型的工具调用和名称比较也需要改用新的控制名称。

```python
from a13n_harness.capabilities import ToolSearchCapability, ProxyToolSearchConfig, ToolSearchGroup

proxy = ToolSearchCapability(
    groups={"crm": ToolSearchGroup(source=source, description="Customer records")},
    config=ProxyToolSearchConfig(
        search_name="find_operations",
        describe_name="describe_operation",
        call_name="invoke_operation",
        list_name="list_operations",
        groups_name="list_domains",
        max_results=10,
        max_search_bytes=32_768,
    )
)
```

生成描述和指令使用配置名称。名称必须不同，不能与其他工具冲突。默认值避免与原生 `ToolSearch` 的 `search_tools` 冲突；原生搜索可为独立的未分组工具并存。

`max_results` 接受 1–100。`limit` 省略或 null 时，摘要默认 `min(10, max_results)`，完整定义默认 `min(5, max_results)`。UTF-8 JSON 响应预算为 1024–32,768 字节，约束整个发现或详情响应。放不下的条目移到下一页，schema 和指令不截断。单个完整条目也放不下时，完整发现或详情查询明确失败，但仍可展示摘要。应直接公开该工具或简化定义来读取完整契约。

迁移 `NativeToolSearchConfig` 时，改用 `ProxyToolSearchConfig`，删除 `strategy`，并将 `mode: native` 改为 `mode: proxy`。旧默认名称 `search_proxy_tools` 和 `call_proxy_tool` 不注册为别名。需更新模型指令和保存的程序；依赖搜索直接返回 schema 的程序应显式请求 `detail="full"`。

准备后没有剩余分组工具时，不公开代理控制工具或生成的代理指令。分组减少模型上下文，但来源仍正常初始化和准备工具。它不会消除 MCP 工具列表流量，也不会让构造一千个 Toolset 不产生开销。

## 为绑定 Run 的 MCP Capability 分组

Host 连接 HTTP MCP 服务并提供租户身份时，可以使用以下构造片段。将示例 URL 换成实际服务，通过 Host 管理的 headers 提供服务要求的认证，再将生成的 `capabilities` 元组和你的模型一起传给 builder。上面的离线 `respond()` 只认识 CRM 工具，接入此服务时需替换。

直接对 `ContextualMCP` Capability 分组。它会先解析本次 Run 的 headers，再创建 MCP toolset：

```python
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)

knowledge = ToolSearchGroup(
    source=ContextualMCP(
        "https://mcp.example.com/mcp",
        id="knowledge-server",
        native=False,
        local=True,
        headers_factory=MCPContextHeaders(
            MCPContextHeadersConfig(
                headers={
                    "X-Run-ID": MCPContextHeaderBinding("context.run_id"),
                    "X-Tenant-ID": MCPContextHeaderBinding("identity.tenant_id"),
                }
            )
        ),
    ),
    description="Search and read the tenant's knowledge base",
)
capabilities = (ToolSearchCapability(groups={"knowledge": knowledge}),)
```

Host 提供 `tenant_id` 身份声明。每次逻辑 Run 解析一次 headers；模型恢复尝试复用该 Run 的上游 MCP，并发 Run 分别替换。分组保留原生 MCP 传输和生命周期。headers 和执行选择见 [MCP 工具](mcp.md)。

选择 `native=False, local=True`。提供方原生执行，包括自动原生回退，不是代理目标。不要将分组来源与 `defer_loading=True` 组合；代理发现和原生延迟加载是不同接口。

## 与 CodeAct 配合

要让受限 Python 编排同一个 CRM 工具，用以下代码替换 `main()` 中的 `source` 和 `proxy` 构造。加入导入，并将生成的 `capabilities` 元组传给 builder。使用能调用 `run_code` 并传入下面代码的模型；原来的确定性模型演示的是普通代理调用。

```python
from a13n_harness.capabilities import CodeActCapability, ToolSearchCapability, ToolSearchGroup
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.toolsets import FunctionToolset

source = CodeActPolicyToolset(
    wrapped=FunctionToolset([customer_name]),
    policy=CodeActToolPolicy(tools={"customer_name": True}),
    reject_unknown_tools=True,
)
proxy = ToolSearchCapability(
    groups={"crm": ToolSearchGroup(source=source, description="Customer records")},
)
capabilities = (proxy, CodeActCapability())
```

runner 目录包含代理控制工具，不包含每个分组工具声明。受限 Python 可在一次 `run_code` 中发现并调用工具：

```python
found = await discover_tools(query="customer name", group="crm", detail="full")
match = found["tools"][0]
name = await call_tool(
    group=match["group"],
    tool=match["tool"],
    arguments={"customer_id": 42},
)
name
```

可复用的 `lookup.codeact.py` 程序使用同一桥接：

```python
async def main(inputs):
    return await call_tool(
        group="crm",
        tool="customer_name",
        arguments={"customer_id": inputs["customer_id"]},
    )
```

模型调用 `run_program(path="lookup.codeact.py", inputs={"customer_id": 42})`；该文件必须可通过当前 Environment 访问。受限 Python 中的每次宿主函数调用都需 await。代理调用保守地形成顺序执行屏障，在 `asyncio.gather` 中也如此。

分组不授予 CodeAct 使用资格。桥接先解析目标并检查其自己的类型化策略，再派发。没有显式所有者策略的 MCP 来源可通过普通模型代理调用，但不自动允许 CodeAct 调用。策略和运行时边界见 [CodeAct](delegation-and-codeact.md#codeact)。

## Host 集成

根据 Host 已有的来源构造方式选择 API：

- 已经准备好需要分组的 Toolset 或 Capability 实例时，使用 `ToolSearchCapability`，如第一个示例。
- 来源已经在 Agent 定义中选定时，使用 `ToolSearchPlan`，包括插件提供的来源。方案选择原来的实例或确切插件 ID，不会重新构造来源。

要试用第二种方式，保留 `search_demo.py` 中的 `customer_name()`、`respond()` 和最后执行 Run 的部分。加入以下导入，替换 `main()` 中构造来源、proxy 和 builder 的代码：

```python
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from pydantic_ai.capabilities import Capability

# Inside main(), replace the source/proxy/builder setup:
customer_tools = Capability(id="customer-tools", tools=[customer_name])
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    capabilities=(customer_tools,),
    tool_search=ToolSearchPlan(
        groups={
            "crm": ToolSearchSelection(
                "Customer records", capabilities=(customer_tools,)
            )
        }
    ),
)
```

程序仍输出 `Customer Ada`。选中的每个 Capability 也必须出现在定义中，未列出的来源继续直接展示。每个来源只提供一次，选择具体来源代理或构建方案中的一种。`AgentDefinition.tool_search` 也接受同样的方案。

Host 配置应保存稳定的来源 ID、组名、描述和经过校验的发现选项。通过可信来源工厂解析 ID，只构造一次来源，再选择展示方式。分组是展示选择，不授予授权或 CodeAct 使用资格。Harness UI 提供自己的 [Agent 工具代理分组](../a13n-harness-ui/agents-and-subagents.md#tool-search-groups)配置。

分组保留原生来源排序、显式 ID、状态、钩子和容器指令。对 `ContextualMCP` 这类依赖 Run 的 Capability，传入实例本身，让它在准备工具前完成正常绑定。方案和分组在构造时使用。需要改变展示方式时，重新构建执行对象；修改原映射不会重新配置现有 Agent。空分组映射保留直接工具，不公开代理控制工具。

### 插件提供的来源

用插件 `plugin_id` 返回的确切 ID 选择插件。以下替换代码使用同一个客户函数演示此路径。将导入和类放在模块顶层，再按注释替换 `main()` 内的构造部分：

```python
from a13n_harness import AbstractHarnessPlugin
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from pydantic_ai.capabilities import Capability


class CustomerPlugin(AbstractHarnessPlugin):
    @property
    def plugin_id(self):
        return "customer-plugin"

    def get_capabilities(self):
        return (Capability(id="customer-tools", tools=[customer_name]),)


# Inside main(), replace the source/proxy/builder setup:
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=FunctionModel(stream_function=respond),
    plugins=(CustomerPlugin(),),
    tool_search=ToolSearchPlan(
        groups={
            "crm": ToolSearchSelection(
                "Customer records", plugins=("customer-plugin",)
            )
        }
    ),
)
```

再次运行文件，仍会输出 `Customer Ada`。Harness 管理插件正常的 `for_agent()` 和 `get_capabilities()` 调用，各调用一次。插件同时提供中间件时，中间件仍会安装。同一插件类的多个实例需要不同 ID，方便方案分别选择。

插件也可以从 `get_capabilities()` 返回 `ToolSearchCapability`，自行管理分组。应在一个组合点汇总这种展示配置。对已有插件，使用方案即可分组，无需修改插件或手动调用其内容提供方法。见[插件生命周期](plugins.md#plugin-lifecycle)和 [Host 重建](hosting.md#reconstruct-trusted-definitions)。

## 执行、用量与限制

分组改变工具的发现方式，目标仍遵循自己的执行约定：

| 项目       | 行为                                                                                                                                                |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| 校验与策略 | 保留原生校验、钩子、受管理授权、凭据、超时和结果限制。发现工具不代表授权执行操作。                                                                  |
| 用量       | 成功的普通代理调用计算 `call_tool` 和目标，共两次工具调用。CodeAct 计算 runner 和目标，也是两次；每次发现或详情查询再加一次。限制在目标派发前检查。 |
| 重试       | 目标校验和 `ModelRetry` 保留原生重试状态。ToolSearch 不重放副作用。CodeAct 保留有上限的 runner 失败行为。                                           |
| 延迟反馈   | 未完成的审批、运行时 `CallDeferred` 或客户端调用会挂起普通代理调用。保留完整 Harness 请求，按目标调用 ID 提供结果后恢复。                           |

处理延迟操作时，Host 展示实际目标，历史仍保留原来的代理调用。原生内联处理器只接收该目标一次。恢复流程见[结构化挂起](state-and-resume.md#structured-suspension)；CodeAct 无法跨 Run 恢复解释器。修改操作的结果不确定时，应先检查 provider 状态再重试。取消不会回滚操作。

支持分组的目标包括本地函数工具、本地执行的 MCP、需要审批的工具，以及外部/客户端工具。提供方原生工具、控制/输出工具、CodeAct runner 和延迟加载工具应留在组外。分组 MCP 使用 `native=False, local=True`，不设置 `defer_loading=True`。

这些 API 通过 `HarnessBuilder` 或 `AgentDefinition` 组合，不是内置 `AgentSpec` 序列化名称，也不会创建独立的代理分组资源。
