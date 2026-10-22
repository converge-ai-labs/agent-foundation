---
title: ToolSearch 工具分组
description: 通过搜索工具和调用工具提供大型工具集合，让模型只需查看简短的工具分组列表。
---

ToolSearch 将选中的本地和 MCP 工具放到固定代理后面。默认普通工具保持直接可见；启用分组后，模型通过 `discover_tools`、`describe_tool`、`call_tool`、`list_tools` 和 `list_tool_groups` 发现、浏览和调用成员。发现不会把成员 schema 加入后续模型请求。

工具通过当前 `ToolManager`、原 Toolset 和 Harness 执行边界执行，保留参数验证、策略、凭据、钩子、结果和用量处理。

## 使用固定代理

`ToolSearchCapability` 和 `ToolSearchPlan` 默认使用 `ProxyToolSearchConfig()`，也可使用别名 `ToolSearchConfig()`。Host 配置可省略 `config.mode`，或设为 `proxy`。控制工具的 schema、描述和生成指令不包含动态目录，有利于模型提示缓存。MCP 来源使用 `native=False, local=True`。

迁移旧配置时，将 `NativeToolSearchConfig` 替换为 `ProxyToolSearchConfig`，删除 `strategy`，并把 `mode: native` 改为 `mode: proxy`。已发现工具始终通过 `call_tool` 调用。需要审批或客户端续传的工具保持直接暴露，使用其原有恢复协议。

代理支持 [CodeAct 编排](#use-with-codeact)，可在一次 runner 调用中发现并调用工具；目标仍须声明类型化 CodeAct 策略。

## 为 Toolset 分组

添加一个 `ToolSearchCapability`，显式将领域名称映射到来源。每个 `ToolSearchGroup` 是被动配置值，不是独立安装的 Capability：

```python
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


source = FunctionToolset(
    [customer_name],
    instructions="Use numeric customer IDs, not display names.",
)
proxy = ToolSearchCapability(
    groups={
        "crm": ToolSearchGroup(
            source=source,
            description="Customer records and account operations",
        )
    },
)
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=model,  # Supply your configured Pydantic AI Model.
    capabilities=(proxy,),
)
```

模型的发现调用为：

```json
{"query": "customer name", "group": "crm"}
```

结果包含实际 `detail`（`summary` 或 `full`）、`tools`、`total` 和 `next_offset`。这个关键词查询返回摘要，包含 `group`、本地 `tool` 名、简短描述和 `codeact_eligible`。模型用 `describe_tool` 获取选中的工具定义：

```json
{"group": "crm", "tool": "customer_name"}
```

详情返回 `{"detail": "full", "tool": ...}`，包含完整描述、`parameters_json_schema`、`return_schema`、来源 `instructions` 和 `codeact_eligible`。读完调用契约后，模型调用 `call_tool`：

```json
{
  "group": "crm",
  "tool": "customer_name",
  "arguments": {"customer_id": 42}
}
```

原来准备好的验证器检查并转换 `arguments`。工具仍以组内规范名称 `crm__customer_name` 注册，但不直接向模型公开成员 schema。未分组工具仍直接可见。受管理工具 ID 不变。

按 `crm`、`knowledge`、`billing` 等领域分组，不要每个工具一组。组标识以字母开头，最多 32 个字母、数字、连字符或下划线，不得含 `__`。描述非空，最多 512 字符。不同组可含相同本地工具名。同组多个来源可用原生 `CombinedToolset`、`Capability(toolsets=[...])` 或 `CombinedCapability` 作为来源。成员本地名必须唯一；重复会使准备失败。名称可能冲突时，先为来源添加前缀，再分组。

## 指令与动态参数

来源 Toolset 指令随完整定义返回。摘要列表不读取这些指令，也不复制 schema。独立的 Capability 级指令保留原有行为。

控制工具不包含动态 `group` 枚举。完整发现、列表和详情查询读取当前准备后的成员 schema；`call_tool` 始终保持固定参数对象结构。

生成的指令告诉模型：

1. 选择领域并发现候选；检查 `detail`，若返回摘要且尚无完整有效定义，先读取所选工具详情；
2. 使用返回的本地名称和匹配完整 schema 的参数对象；
3. 复用已知且当前有效的 schema，不必每次调用前都搜索；
4. 分页读取，工具不可用时刷新发现结果；
5. 核查不确定副作用，不盲目重试修改。

空查询或纯空白查询浏览一个或所有组。显式 `group` 先限定候选范围。精确本地名称或 `group__tool` 名称排在最前，忽略大小写和首尾空白，但保留分隔符差异。其他关键词候选仍然保留：即使存在 `payment`，搜索 `payment` 也能找到 `payment_lookup` 和 `refund_payment`。规范名称查询不会隐式限定组；使用返回的 group 和本地名称查询目标工具详情。

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

旧默认名称 `search_proxy_tools` 和 `call_proxy_tool` 不注册为别名。需更新模型指令和保存的程序；依赖搜索直接返回 schema 的程序应显式请求 `detail="full"`。

准备后没有剩余分组工具时，不公开代理控制工具或生成的代理指令。分组减少模型上下文，但来源仍正常初始化和准备工具。它不会消除 MCP 工具列表流量，也不会让构造一千个 Toolset 不产生开销。

## 为绑定 Run 的 MCP Capability 分组

将 Capability 实例作为组 `source`，尤其是绑定 Run 时会替换自身的 Capability。不要在定义时提取 `ContextualMCP` Toolset：

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

以下示例使用代理模式。

分组前发布来源的类型化 CodeAct 策略：

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

Host 在构造 Agent 定义时决定展示方式。已构造来源使用 `ToolSearchCapability`。要将普通 Harness 插件提供的工具与选中 Capability 一起分组，将类型化 `ToolSearchPlan` 传给 `AgentDefinition.tool_search` 或 `HarnessBuilder.build(tool_search=...)`。Harness 在普通的一次性绑定中保留插件归属，无须插件扫描或特殊来源工厂 API。

```python
from a13n_harness import AgentDefinition, AgentSpec
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from pydantic_ai.capabilities import MCP

source = MCP("https://mcp.example.com/mcp", native=False, local=True)
definition = AgentDefinition(
    agent=AgentSpec(model="openai:gpt-5"),
    output_type=str,
    capabilities=(source,),
    tool_search=ToolSearchPlan(
        groups={
            "knowledge": ToolSearchSelection(
                description="Find project documentation",
                capabilities=(source,),
            )
        }
    ),
)
```

要包含插件提供的内容，在选择中添加 `plugins=("plugin-docs",)`，并通过定义 `plugins` 提供该确切实例。同一插件类的多个实例按不同 ID 选择。所选 Capability 必须已在定义中，所选插件必须在 builder 中可用。未列来源直接展示。不要既通过具体来源代理，又通过构建方案安装同一来源。

分组保留原生同级顺序、显式 ID、状态、钩子和自定义容器指令；不会将独立来源变成一个原子排序节点。方案不可变，在构造时使用。

提供 JSON、YAML、API 或配置 UI 的 Host 应负责：

| Host 输入          | Host 职责                                                                                                |
| ------------------ | -------------------------------------------------------------------------------------------------------- |
| 来源 ID 和专用选项 | 通过可信来源工厂解析 ID；每个执行对象只构造一次所选来源。                                                |
| 直接或分组展示     | 每个来源选择直接安装或组内安装，不能重复。无关工具直接展示。                                             |
| 组名和描述         | 将所选来源汇总到每个 Agent 的一个 `ToolSearchCapability(groups=...)`。多个来源属于同领域时使用原生组合。 |
| 可选发现设置       | 验证并构造 `ProxyToolSearchConfig`；无须自定义时使用默认值。                                             |

例如 Host 自有文档可包含：

```yaml
tool_sources:
  - source_id: customer-tools
    presentation: proxy
    group: crm
    description: Customer records and account operations
  - source_id: host-status
    presentation: direct
```

这是 **Host schema 示例**，不是内置 Harness 或 Harness UI 资源。保存稳定 ID 和经验证选项，不保存序列化 Python 对象、导入目标或字面凭据。来源工厂和凭据解析仍由可信 Host/插件代码负责。组描述指导发现，不授予授权或 CodeAct 使用资格。

来源构造一次，再选择放置位置。第三方 `AbstractCapability` 可直接用作 `ToolSearchGroup(source=capability, description=...)`，无须 ToolSearch 专用实现。原生组合保留 Agent/Run 绑定、钩子和 Capability 级指令。`ContextualMCP` 等绑定 Run 的来源应提供 Capability 本身，不提前调用 `get_toolset()`。

groups 映射在构造时使用。改变展示应构建替代执行对象；修改原映射不重新配置已有 Agent。空映射不公开代理控制工具，直接工具不变。

### 插件提供的来源

`AbstractHarnessPlugin.get_capabilities()` 是内容提供边界，不是通用来源查询 API。优先用 `ToolSearchPlan` 为已有插件分组，无须改动插件。Harness 对各插件的 `for_agent()` 和 `get_capabilities()` 恰好调用一次，先验证原来源再分组。中间件仍安装；分组不授权保留的 Capability。

插件也可用以下 `ToolSearchCapability` 自行管理展示。在一个组合点汇总分组，不要安装互相竞争的代理界面。

以下 Host 自有插件展示该边界。其 `source_factory` 可调用对象在 `for_agent()` 中为每个执行对象只构造一次来源 Capability；`get_capabilities()` 选择直接或分组展示。来源 Run 绑定仍是原生流程：

```python
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from a13n_harness import AbstractHarnessPlugin, AgentContext
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from pydantic_ai.capabilities import AbstractCapability


@dataclass
class HostToolsPlugin(AbstractHarnessPlugin):
    source_factory: Callable[[], Mapping[str, AbstractCapability[AgentContext]]]
    group_descriptions: Mapping[str, str]
    sources: Mapping[str, AbstractCapability[AgentContext]] = field(default_factory=dict)

    @property
    def plugin_id(self) -> str:
        return "host-tools"

    def for_agent(self):
        return replace(self, sources=dict(self.source_factory()))

    def get_capabilities(self) -> Sequence[AbstractCapability[AgentContext]]:
        direct = []
        groups = {}
        for name, source in self.sources.items():
            if name in self.group_descriptions:
                groups[name] = ToolSearchGroup(
                    source=source, description=self.group_descriptions[name]
                )
            else:
                direct.append(source)
        if groups:
            direct.append(ToolSearchCapability(groups=groups))
        return tuple(direct)
```

这里来源名也用作组名；Host 的来源 ID 和领域名称不同时，可先组合来源，再构造各描述对象。在 Host 配置层根据可用来源验证名称。

通过 `HarnessBuilder.build(plugins=(plugin,))` 传入 `HostToolsPlugin(source_factory=build_sources, group_descriptions={"crm": "Customer records"})`；`build_sources` 是返回具名 Capability 的可信工厂。空 `group_descriptions` 保持所有来源直接展示。不要再通过 `capabilities=` 传入这些来源。

对于已有第三方中间件插件，`ToolSearchPlan` 按确切插件 ID 选择其普通贡献。不要手动调用 `get_capabilities()`、再次实例化来源，或在仍需要中间件时移除插件。见[插件生命周期](plugins.md#plugin-lifecycle)和[托管](hosting.md#reconstruct-trusted-definitions)。Harness UI 通过 [Agent 工具代理分组](../a13n-harness-ui/agents-and-subagents.md#tool-search-groups)提供此构建方案。

## 执行、用量与限制

- **统一执行路径：** 目标验证、Capability 钩子、受管理授权、凭据、超时、结果限制和 Toolset 派发仍使用原生机制。搜索用于发现，不是授权。
- **原生统计：** 成功的普通代理调用计算 `call_tool` 调用和目标，共两次成功工具调用。CodeAct 解析 `call_tool` 调用时不执行额外代理层：runner 加一个目标也算两次；再搜索一次则算三次。用量限制在目标派发前检查。
- **重试：** 普通目标验证和 `ModelRetry` 保留目标原生重试状态。ToolSearch 不重放副作用，也不维护重试引擎。CodeAct 保留有上限的 runner 失败语义。
- **仅内联审批：** 原生处理器可在当前调用内批准或拒绝。未解决审批或外部延迟会使代理调用失败，不会挂起嵌套续接。需要跨轮次 Host 交互的工具应直接公开。
- **支持的目标：** 本地函数工具，包括本地执行 MCP 和内联审批控制工具。不支持将外部/客户端工具、提供方原生工具、控制/输出工具、CodeAct runner 和延迟加载工具作为分组目标。
- **不确定副作用：** 取消和意外失败不回滚工作。重试修改前检查提供方状态。
- **组合范围：** API 通过 `HarnessBuilder` 或 `AgentDefinition` 组合，不是内置 `AgentSpec` 序列化名称。Harness UI 定义自己的每 Agent YAML 选择 schema，不创建独立代理分组资源。
