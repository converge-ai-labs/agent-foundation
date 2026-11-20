---
title: 委派与 CodeAct
description: 通过子智能体编排复杂的多 Agent 协作流程，或借助 CodeAct 让 Agent 编写 Python 代码并发调用与编排外部工具。
---

复杂的智能体业务系统往往需要两类高阶编排能力：**委派（Delegation）**（将独立任务分派给具备专属提示词和特定模型的子智能体）以及 **CodeAct**（让智能体编写并执行 Python 代码，在单轮交互内通过循环、分支与并发高效编排多个工具）。

## 如何选择

根据具体的系统架构与交互延迟要求选择合适的编排策略：

| 业务场景 | 推荐方案 | 核心优势 |
| :--- | :--- | :--- |
| 单次直接的工具操作 | 直接工具调用 | 零额外开销；标准模型函数调用机制 |
| 边界明确的审查、专家分工或角色解耦 | [内联委派](#inline-delegation) | 拥有独立的 Prompt 上下文与专属模型选型，结果干净汇聚 |
| 涉及多轮遍历、数组过滤或计算聚合 | [CodeAct](#codeact) | 将中间结果在 Python 内过滤，避免巨量数据冲垮上下文与往返延迟 |
| 无需串行等待的大规模外部工具并发调用 | [CodeAct 并发调用](#combine-tool-calls) | 通过 `asyncio.gather` 秒级并发调度任意已注册工具 |
| 执行周期长、需跨越父级会话轮次的后台任务 | [异步子级](#asynchronous-children) | 任务生命周期与父会话解耦，由宿主调度器独立托管 |
| 需要调用系统原生命令、安装第三方依赖 | 业务代码或[执行环境 Environments](environments.md) | 在受限沙箱之外获得完整的操作系统原生能力 |

## 内联委派

内联委派允许父级 Agent 调度一个专业的子级 Agent，并同步等待其交付最终成果。Harness 负责在当前执行轮次内透明地管理子智能体的生命周期。

### 声明子级

使用完整的 `AgentDefinition` 定义子级 Agent，并通过 `SubagentCapability` 与 `SubagentDefinition` 在父级构建器中完成注册：

```python title="delegation_demo.py"
import asyncio
import os

from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder, SubagentDefinition
from a13n_harness.capabilities import SubagentCapability

# 1. 定义专属领域的子级智能体
reviewer = AgentDefinition(
    agent=AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="你是一位资深的系统架构安全专家。请深入审查传入的方案，精准识别关键架构风险与遗漏步骤。",
    ),
    output_type=str,
    definition_id="plan-reviewer-v1",
)

# 2. 将子级智能体挂载至父级协调智能体中
parent = HarnessBuilder().build(
    AgentDefinition(
        agent=AgentSpec(
            model=os.environ["HARNESS_MODEL"],
            instructions="负责全局任务的统筹推进。将架构方案的深度审查委派给 'reviewer' 子智能体，随后汇总输出最终建议。",
        ),
        output_type=str,
        definition_id="coordinator-v1",
        capabilities=(SubagentCapability(),),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="负责对架构与部署方案进行深度安全及风险审查的专家智能体。",
                agent=reviewer,
            ),
        ),
    )
)


async def main() -> None:
    result = await parent.run(
        "请审查这份数据库变更方案：直接在生产库执行 DDL，随后逐步启动数据迁移脚本。"
    )
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

当运行该脚本时，父级模型会自动发起 `delegate(subagent="reviewer", prompt=...)` 调用。Harness 将初始化并执行 reviewer 智能体，直至其完成计算并将结构化结果交付给父级模型。

### 继续子级对话

子智能体返回结果时，Harness 会生成一个唯一的 `execution_id`。如果父级模型在后续思考中希望继续与该子智能体深挖细节，只需调用 `resume_subagent(execution_id=..., prompt=...)` 即可承接上一轮对话；若再次调用 `delegate`，则会启动一个全新的独立子线程。

子智能体的完整对话线索已无缝序列化在父级的 `HarnessState` 中。跨进程重启保存与加载父级状态时，所有已委派的子级会话上下文均得到完整维系。

## CodeAct

传统的大模型工具调用（Tool Calling）采用“单步往返”机制：模型输出一条调用指令，宿主执行并返回结果，模型阅读后再决定下一步。如果遇到遍历数组、分页拉取或数据聚合等任务，这种往返会产生大量网络延迟并消耗成倍的 Token。

**CodeAct** 彻底改变了这一模式：它为模型赋予了一个受保护的异步 Python 运行环境。Agent 可以自主编写 Python 代码，利用循环、条件与并发直接调度注册的工具，仅将最终处理后的核心结果回传至 Prompt 上下文。

### 为工具启用 CodeAct

注册 `CodeActCapability()`，并使用 `CodeActPolicyToolset` 为需要暴露给 Python 代码的工具授予调用权限：

```python title="codeact_demo.py"
import asyncio
import os

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.toolsets import CodeActPolicyToolset, CodeActToolPolicy
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    """返回传入整数值的两倍。"""
    return value * 2


# 为原生工具配置 CodeAct 策略，允许被 Python 代码直接调度
math_tools = Capability(
    id="math-tools",
    toolsets=[
        CodeActPolicyToolset(
            wrapped=FunctionToolset([double], id="math-functions"),
            policy=CodeActToolPolicy(tools={"double": True}),
            reject_unknown_tools=True,
        )
    ],
)

calculator = HarnessBuilder().build(
    AgentSpec(
        model=os.environ["HARNESS_MODEL"],
        instructions="遇到多项计算时，优先通过 run_code 编写 Python 代码高效执行计算。",
    ),
    output_type=str,
    capabilities=(math_tools, CodeActCapability()),
)


async def main() -> None:
    result = await calculator.run(
        "请使用 run_code 对 [2, 4, 6, 8, 10] 中的每个数计算两倍并求和，返回最终总数。"
    )
    print(result.output_or_raise())


if __name__ == "__main__":
    asyncio.run(main())
```

### 组合工具调用

在 `run_code` 中，模型编写的是标准的异步 Python 代码，所有授权工具均以顶级异步函数形式直接可用：

```python
# 串行遍历多个工具调用
values = []
for item in [2, 4, 6]:
    doubled = await double(value=item)
    values.append(doubled)

sum(values)
```

对于无依赖关系的独立 I/O 操作，模型可以使用 `asyncio.gather` 进行高吞吐并发调用：

```python
import asyncio

# 并发工具调用，大幅缩短等待延迟
results = await asyncio.gather(
    double(value=10),
    double(value=20),
    double(value=30),
)
sum(results)
```

中间大量的调试输出与原始响应均留在沙箱内，仅最后一个表达式的值或显式返回对象会注入至模型上下文，极大节省了窗口开销。

### 组合浏览器工具

将 `CodeActCapability()` 与 `BrowserUseCapability()` 组合，可以实现极其高效的网页自动化抓取与交互逻辑：

```python
# 观测页面、批量抓取卡片文本并读取可访问性无障碍树
await browser_observe()
cards = await browser_js(
    expression="[...document.querySelectorAll('.pricing-card')].map(el => el.innerText)"
)
ax_tree = await browser_cdp(method="Accessibility.getFullAXTree", params={})

{"cards": cards, "total_ax_nodes": len(ax_tree.get("nodes", []))}
```

`browser_js` 直接在网页主页面上下文中求值并返回解析后的 JSON 对象；`browser_cdp` 支持直接调用 Chrome DevTools Protocol 协议域实现底层审计。

### 复用程序

对于结构固定、需反复执行的操作流，可以将代码保存为环境挂载目录下的 Python 文件，无需每轮由模型重新动态生成：

```python title="calculate_metrics.codeact.py"
async def main(inputs: dict) -> dict:
    raw_val = inputs["value"]
    computed = await double(value=raw_val)
    await store(key="last_computed_metric", value=computed)
    return {"input": raw_val, "result": computed}
```

Agent 在后续任务中只需通过 `run_program` 传入入参即可调用：

```json
{
  "path": "/workspace/calculate_metrics.codeact.py",
  "inputs": {"value": 42}
}
```

Harness 会通过绑定的执行环境加载该文件，启动独立的沙箱解释器运行 `main()` 函数，并将强类型的字典结果安全回传。

### 跨调用保留数据

Harness 为 CodeAct 的状态管理提供了三层生命周期维度：

| 作用域 | 生命周期 | 适用机制 |
| :--- | :--- | :--- |
| 临时局部变量 | 当前单次逻辑 Run 内 | 在 `run_code` 中定义的临时变量与辅助函数 |
| 脚本内部变量 | 单次脚本文件执行过程 | `run_program` 运行时的模块局部空间 |
| 持久化 KV 状态 | 跨会话轮次及服务重启持久保留 | `await store(key, value)` 与 `await load(key)` |

借助内置存储函数，可以在多轮任务间无缝共享结构化计算结果，而不会占用珍贵的 Prompt 空间：

```python
# 存储中间计算成果
await store(key="report.data", value={"summary": "第四季度财报", "score": 98})

# 在后续交互轮次中提取
saved = await load(key="report.data")

# 清理不再需要的数据
await forget(key="report.data")
```

所有通过 `store` 保存的键值对均被自动打包至 `HarnessState` 检查点中，即使应用进程重启也能完整恢复。

### 处理失败并设置限制

为确保宿主系统的健壮性，可以通过 `CodeActConfig` 设置精细的执行超时、并发上限与内存配额：

```python
from a13n_harness.capabilities import CodeActCapability, CodeActConfig

codeact_capability = CodeActCapability(
    CodeActConfig(
        max_tool_calls=32,          # 单次代码执行允许调用的工具上限
        max_concurrency=8,           # asyncio.gather 最大并发任务数
        timeout_seconds=60,          # 单次执行墙钟超时时间（秒）
        max_state_entries=128,       # 持久化存储条目上限
        max_state_bytes=2 * 1024 * 1024,  # 持久化状态内存上限（2 MiB）
    )
)
```

当代码抛出未捕获的异常或超出配额边界时，Harness 会精准定位代码行号与调用栈信息，以结构化错误形式反馈给模型，协助其自我反思并修正代码。

## 异步子级

当智能体需要启动长期后台运行、甚至执行生命周期超出当前轮次的子任务时，应采用异步子级架构。在该模式下，宿主平台需要提供一个 `SubagentOperator` 实现，负责物理任务调度、状态快照记录与结果转发：

```python
from a13n_harness.capabilities import SubagentCapability

# 启用由宿主系统调度的异步委派机制
capability = SubagentCapability(async_enabled=True, operator=host_operator)
```

开启后，模型将获得全套异步任务管理原语：

| 工具名称 | 作用 |
| :--- | :--- |
| `delegate(subagent_name, prompt)` | 提交子级任务并立即返回认领凭证（Receipt） |
| `subagent_info(execution_id)` | 查询子任务的实时运行进度、状态及性能指标 |
| `wait_subagent(execution_ids)` | 阻塞等待一个或多个子任务执行完毕 |
| `steer_subagent(execution_id, message)` | 向正在运行中的子任务发送实时引导指令 |
| `cancel_subagent(execution_id)` | 请求温和取消指定的子任务 |
| `resume_subagent(execution_id, prompt)` | 向已完结的子任务提交后续追加指令 |

### Host 管理的反馈

对于执行过程中需要人工介入或二次授权的长期运行子智能体，由宿主系统统筹其挂起与恢复流程：

1. 当子级遇到延迟决策工具或提问时，Harness 会将状态置为 `suspended`，并落盘持久化其 `HarnessState`。
2. 宿主系统通过界面通知用户或外系统收集确认信息。
3. 反馈就绪后，宿主系统发起 `DeferredToolResume`，将决策结果注入子智能体，使其从精准检查点处无缝接续。
