---
title: Toolset 暴露与发现
description: 为原生 Toolset 选择直接或渐进式 schema，同时保持唯一执行路径。
---

Toolset exposure 决定模型如何看到已选中的工具。`direct` 直接提供 schema；`progressive` 先提供简短目录，按需发现完整定义。来源使用原生 Toolset ID，最终工具名保持不变。选择、权限、凭据和执行仍由原有组件负责。

## 配置 Toolset

将不可变 `ToolExposure` 传给 `AgentDefinition.tool_exposure` 或 `HarnessBuilder.build(tool_exposure=...)`。映射键是来源的原生 `ToolDefinition.toolset_id`：

```python
from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.tools import ToolExposure, ToolsetExposure
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def customer_name(customer_id: int) -> str:
    """Look up the display name for a customer ID."""
    return {42: "Ada"}.get(customer_id, "Unknown")


source = FunctionToolset(
    [customer_name],
    id="crm",
    instructions="Use numeric customer IDs, not display names.",
)
agent = HarnessBuilder().build(
    AgentSpec(),
    output_type=str,
    model=model,
    capabilities=(Capability(toolsets=[source]),),
    tool_exposure=ToolExposure(
        toolsets={
            "crm": ToolsetExposure(
                exposure="progressive",
                description="Customer records and account operations",
            ),
        },
    ),
)
```

`ToolsetExposure` 包含三个字段：

- `exposure`：默认为 `direct`，也可选择 `progressive`。
- `tool_exposure`：按最终工具名精确匹配的覆盖值，优先于来源默认值。
- `description`：来源用途，不能为空，最多 512 字符。有效的渐进式来源必须有用途；Harness 为内置 Toolset 提供默认描述。

例如，`tool_exposure={"customer_name": "direct"}` 可在渐进式来源中直接暴露一个工具。没有原生 ID 的工具保持直接暴露。Exposure 不添加前缀或别名，已有名称冲突仍会失败。

## 发现、描述与执行

默认使用 Harness 发现。有可用渐进式工具时，模型获得五个固定控制工具：

| 控制工具                            | 用途                                  |
| ----------------------------------- | ------------------------------------- |
| `list_toolsets(offset=0)`           | 浏览原生来源 ID、用途、数量和名称预览 |
| `list_tools(toolset, offset=0)`     | 分页浏览一个来源的工具摘要            |
| `search_tools(query, toolset=None)` | 搜索当前名称、描述和参数描述          |
| `describe_tools(names)`             | 按精确名称读取完整定义和来源指令      |
| `execute_tool(name, arguments)`     | 通过原生 manager 调用启用的目标       |

搜索始终返回摘要，即使名称精确匹配。构造不熟悉的参数前应读取完整定义：

```python
found = await search_tools(query="customer name", toolset="crm")
definitions = await describe_tools(names=["customer_name"])
result = await execute_tool(name="customer_name", arguments={"customer_id": 42})
result
```

这是 CodeAct 程序示例；普通模型调用使用相同名称和参数对象。已知精确名称时可以直接执行，无须重复搜索。`execute_tool` 也支持直接暴露的可调用工具。

列表每页最多 20 项，使用 `next_offset` 继续。搜索要求非空查询，最多返回 20 个摘要，以及总数和部分结果标记；结果不完整时缩小查询或浏览来源。描述接受 1–20 个名称。响应限制为 32 KiB UTF-8 JSON。完整定义不会被截断，超限时明确要求减少定义数量或简化来源 schema。

精确名称匹配忽略大小写并优先排序，之后按关键词覆盖与相关性排序。关键词按标点和驼峰拆分。搜索读取当前 schema，不展开引用、不获取外部资源；默认值、示例和枚举值不参与匹配。这是确定性本地匹配，不是语义检索。

不含 schema 的来源目录最多 8 KiB，每个来源最多预览五个名称。目录变化时在一个逻辑 Run 中注入一次；保留的摘要值可阻止后续 Run 重复注入相同目录。渐进式来源指令随完整描述返回。禁用 Toolset 指令时也会隐藏这部分指导。

## 选择原生工具搜索

使用相同来源策略，切换到 provider 原生发现：

```python
policy = ToolExposure(
    progressive_search="native",
    progressive_search_strategy="bm25",
    toolsets={
        "crm": ToolsetExposure(
            exposure="progressive",
            description="Customer records and account operations",
        ),
    },
)
```

省略 `progressive_search_strategy` 使用 provider 默认值，也可选择 `bm25` 或 `regex`。策略字段仅适用于原生模式，由 provider 验证支持情况。不支持的模型在请求发出前以 `native_tool_search_unsupported` 失败，不自动回退。

原生模式使用上游延迟工具搜索，不提供上述五个控制工具。发现后按不变的工具名直接调用，沿用普通验证、用量和延迟反馈。这是对本地执行工具的原生发现，不是 provider 侧 MCP 执行。

## MCP 与插件来源

保留 MCP Capability 的普通贡献路径。使用 `native=False, local=True`、稳定原生 ID，并在 `ToolExposure.toolsets` 中引用该 ID。`ContextualMCP` 等绑定 Run 的来源应保留 Capability 本身，不提前提取 Toolset。请求头每个逻辑 Run 解析一次，并在并发 Run 之间隔离。见 [MCP 工具](mcp.md)。

插件保留普通的 `for_agent()`、`get_capabilities()`、中间件和 Run 绑定。Exposure 不重复构造来源。Host 插件 factory 可以通过 `describe_toolsets(context)` 提供原生 ID 和用途。保存稳定 ID 和策略，不保存 Python 对象或凭据；Host 验证拒绝空白或重复 ID。

渐进式暴露减少模型上下文，不减少来源初始化或 MCP 工具列举成本。来源仍通过原有选择控制启停。

## 与 CodeAct 配合

来源所有者必须声明类型化 CodeAct 资格。Harness 模式下，五个控制工具可在 runner 中调用，渐进式目标 schema 不进入初始 runner 目录。桥接层将 `execute_tool` 解析到实际目标，并检查目标自己的资格、策略和参数。控制工具的资格不会赋予目标资格。

原生模式先在 CodeAct 外发现工具。符合资格的已发现工具随后进入 runner 目录，使用普通 sandbox 名称调用。见 [CodeAct](delegation-and-codeact.md#codeact)。

程序中每个 Host 函数调用都需要 `await`。普通成功的 `execute_tool` 消耗两次成功调用：外层封装和目标。CodeAct 直接分派目标，不额外执行封装，因此一次 runner 加一个目标也消耗两次。搜索和描述各消耗一次；分派前检查限额。

## 审批、客户端工具与续接

`execute_tool` 支持本地函数、审批工具、外部客户端工具和结构化用户问题。验证、权限、钩子、凭据、超时、原生重试和结果边界保持有效。发现控制、输出工具、CodeAct runner 和 provider 原生工具不能作为目标；执行控制工具仍可调用。

原生审批和外部延迟会正常挂起。Pending metadata 保存验证后的实际目标，原生历史保留外层调用 ID。Host 使用 `project_tool_call` 展示审批、投递外部调用和解释答案，同时保留原始 pending 事实供恢复使用。审批参数覆盖由 Harness 转换一次，不新增 pending store 或恢复协议。

首次参数验证失败会在预算允许时返回当前完整定义。取消和意外错误不会回滚副作用；重试修改操作前应检查不确定结果。

交接和压缩保留已调用工具的有界定义工作集及当前目录。仅搜索或描述不会将工具加入工作集。重建发现使用当前定义，不授予新权限。

Host 配置示例见 [Harness UI 来源暴露](../a13n-harness-ui/agents-and-subagents.md#toolset-exposure)和 [Service 工具](../a13n-service/tools.md#progressive-tool-discovery)。
