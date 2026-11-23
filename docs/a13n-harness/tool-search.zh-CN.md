---
title: 工具搜索
description: 在 Harness 中将大型工具目录延迟隐藏在可搜索的工具分组后，让模型按轮次仅加载所需 Schema。
---

当智能体接入数十甚至上百个工具（例如大型 MCP 服务器）时，每轮发送全部 Schema 既浪费上下文又降低工具选择准确率。`ToolSearchCapability` 只暴露一个紧凑的搜索工具，按需加载匹配的工具 Schema。

## 启用工具搜索

将一个或多个工具集包装进 `ToolSearchGroup` 并传给 `ToolSearchCapability`：

```python
from a13n_harness.capabilities.tool_search import ToolSearchCapability, ToolSearchGroup

tool_search = ToolSearchCapability(
    groups=(
        ToolSearchGroup(
            name="github",
            description="Issues, pull requests, and repository metadata.",
            sources=(github_toolset,),
        ),
    ),
)
```

运行时模型仅看到分组说明，按关键词搜索后即可为当前 Run 激活目标工具。分组只控制 Schema 呈现方式，绝不会绕过工具权限或审批策略。

## 分组绑定运行的 MCP 能力

直接将本地 `MCP` 或 `ContextualMCP` 能力作为 `ToolSearchGroup` 来源传入，可在工具准备前保留单次运行的请求头解析与绑定：

```python
ToolSearchGroup(
    name="docs_mcp",
    description="Internal documentation search and retrieval.",
    sources=(mcp_capability,),
)
```

## 宿主集成

宿主应在配置中保存稳定的来源 ID、分组名称与描述，在构建期通过可信工厂解析，并在同一 `ToolSearchCapability` 中聚合各分组（或由插件在 `get_capabilities()` 中自行返回 `ToolSearchCapability`）。
