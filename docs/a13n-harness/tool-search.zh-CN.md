---
title: 分组工具搜索（ToolSearch）
description: 通过按需分组 ToolSearch 发现机制让 Python 智能体轻松扩展至数百个工具，显著降低提示词 Token 开销并完整保留参数 Schema。
---

当智能体接入数十甚至上百个工具时，可按业务领域将相关工具源隐藏在五个固定代理控制工具（`discover_tools`、`describe_tool`、`call_tool`、`list_tool_groups`、`list_tools`）之后，使模型仅按当前轮次需要加载对应 Schema。

## 为工具集分组

将领域名到 `ToolSearchGroup(source=..., description=...)` 的映射传给 `ToolSearchCapability`：

```python title="search_demo.py"
import asyncio
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import ProxyToolSearchConfig, ToolSearchCapability, ToolSearchGroup
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


source = FunctionToolset([customer_name], instructions="Use numeric customer IDs.")
proxy = ToolSearchCapability(
    groups={"crm": ToolSearchGroup(source=source, description="Customer records")},
    config=ProxyToolSearchConfig(max_results=10, max_search_bytes=32_768),
)
```

运行时模型遵循标准三步路径（或直接使用 `detail="full"` 一步获取完整定义）：

| 控制工具                          | 用途                                                                 |
| --------------------------------- | -------------------------------------------------------------------- |
| `discover_tools`                  | 关键词搜索（`detail="auto"`、`"summary"` 或 `"full"`；可选 `group`） |
| `describe_tool`                   | 读取候选工具的完整 JSON Schema 与源工具集指令                        |
| `call_tool`                       | 按 `(group, tool, arguments)` 调用目标工具并执行正常校验与权限检查   |
| `list_tool_groups` / `list_tools` | 浏览可用领域并按 `next_offset` 分页枚举成员工具                      |

分组仅改变 Schema 发现方式——调用仍完整经过参数校验、[受管工具权限](managed-tools.md#select-tool-permissions)、人工审批与[客户端工具反馈](client-tools.md)。如需在 `CodeAct` 中调用分组工具，请用 `CodeActPolicyToolset` 包装底层工具源（见 [CodeAct](delegation-and-codeact.md#codeact)）。

## 分组绑定运行的 MCP 能力

将本地 `MCP` 或 `ContextualMCP`（`native=False, local=True`，且不设 `defer_loading=True`）直接作为 `ToolSearchGroup(source=...)` 传入，即可在准备工具前保留按次请求头解析：

```python
from a13n_harness.capabilities import ToolSearchCapability, ToolSearchGroup
from a13n_harness.mcp import ContextualMCP

knowledge = ToolSearchGroup(
    source=ContextualMCP("https://mcp.example.com/mcp", id="knowledge", native=False, local=True),
    description="Search and read the tenant's knowledge base",
)
capabilities = (ToolSearchCapability(groups={"knowledge": knowledge}),)
```

## 宿主集成

若能力或插件已直接挂载在智能体定义上，可向 `HarnessBuilder().build(..., tool_search=...)`（或 `AgentDefinition.tool_search`）传入 `ToolSearchPlan`，无需重复包装实例：

```python
from a13n_harness.capabilities import ToolSearchPlan, ToolSearchSelection
from pydantic_ai.capabilities import Capability

customer_tools = Capability(id="customer-tools", tools=[customer_name])
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=model,
    capabilities=(customer_tools,),
    tool_search=ToolSearchPlan(
        groups={
            "crm": ToolSearchSelection(
                "Customer records",
                capabilities=(customer_tools,),  # 或 plugins=("customer-plugin",)
            )
        }
    ),
)
```

Harness UI 的声明式 YAML 配置方式见[工具搜索分组](../a13n-harness-ui/agents-and-subagents.md#tool-search-groups)。
