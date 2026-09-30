---
title: 委派与 CodeAct
description: 内联或异步运行已声明 subagent，让 CodeAct 通过符合条件的工具执行受限 Python。
---

Agent Harness 提供两种高级协调功能，无须引入工作流引擎：

- subagent 执行在内联或异步 operator 中运行确切的已声明子级；
- CodeAct 在活动工具中明确允许使用的子集上运行受限 Python。

两者均通过统一的 Harness 和 Pydantic AI 执行边界执行，使策略、事件、用量、取消、结果验证和清理保持一致。

## 内联委派

### 声明子级结构

父定义拥有有限的直接子定义集合：

```python
from a13n_harness import (
    AgentDefinition,
    HarnessBuilder,
    SubagentDefinition,
)
from a13n_harness.capabilities import SubagentCapability
from pydantic_ai.agent.spec import AgentSpec

child = AgentDefinition(
    agent=AgentSpec(),
    output_type=str,
    model=reviewer_model,
)

parent = HarnessBuilder().build(
    AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        model=coordinator_model,
        capabilities=(SubagentCapability(),),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Review one bounded change.",
                agent=child,
            ),
        ),
    )
)
```

构建递归验证有限无环图和同级名称唯一性。每个子级成为不可变、可复用的 `ExecutableAgent` 构建结果，包含在根执行对象中。

### 内联执行与续接

默认 Capability 不需要 Host 调度器或子级绑定回调。其内部内联执行器会：

1. 选择一个已声明子级；
2. 派生新的子实例血缘，并取编写的用量上限交集；
3. 应用委派边的上下文策略；
4. 借用父级已经进入的 Environment 映射，不重新进入或关闭 adapter；
5. 通过规范 `ExecutableAgent.stream()` 路径运行子级；
6. 将已验证的子级观测转发到父级流；
7. 保存完整嵌套子级续接，再返回有上限的结果。

`delegate(subagent, prompt)` 创建新的内联续接并返回 `execution_id`。`resume_subagent(execution_id, prompt)` 只推进该确切、兼容的嵌套 `HarnessState`。内联子状态绝不独立发布借用的 Environment 状态；子级不能挂载、替换、卸载或改变默认 Environment。

### 异步子级

可能在父 Run 结束后继续运行的子级，需要 Host 管理的 `SubagentOperator`：

```python
from a13n_harness.capabilities import SubagentCapability

operator = ApplicationSubagentOperator(thread_service, environment_service)
capability = SubagentCapability(
    async_enabled=True,
    operator=operator,
)
```

operator 实现完整的 `delegate`、`info`、`wait`、`steer`、`cancel` 和 `resume`。受理前，Harness 解析确切子级、派生身份、应用上下文、用量限制交集和独立的父级关联。可用时还分别提供原工具调用 ID 和组装名称。operator 可据此派生自己的幂等身份和重放范围；Harness 不生成这些值。之后 operator 负责子 Thread 创建、新 `RunBindings`、Environment 关联和重新进入、递归 Harness 调用、存储、检查点、观测、唤醒、取消、恢复、丢失、清理和保留。

Harness 不提供默认异步管理器、执行存储、后台任务注册表、父状态执行镜像或关闭方法。父 Run 或 Environment 关闭不取消已受理子任务，也不关闭 operator。`subagent_info` 和 `wait_subagent` 每次直接查询 Host。

### 明确的职责边界

内联执行是唯一内置子执行器。异步支持不提供：

- 进程本地调度器或默认存储；
- 必需的数据库或检查点协议；
- 完成回调或唤醒账本；
- 跨进程取消路由；
- 父 `HarnessState` 中的 Host 子级状态副本。

根据所属 Host，在 `SubagentOperator` 后实现这些语义。受理后不要保留活动父 `AgentContext` 或已进入 Environment，也不要将父状态变成第二份子级存储。

### 子 Run 的延迟工具

延迟支持由新的 `RunBindings.deferred_tools_supported` 选择，不取决于是否有父级。默认 `True`。根级和子级都可使用原生延迟调用、审批和 Host 配置的处理器。

没有子级反馈生命周期的 Host 设置 `deferred_tools_supported=False`。声明式延迟工具及其第一方指引会被省略。运行时 `CallDeferred` 和 `ApprovalRequired` 在同一模型循环内转为 `ToolDenied("Deferred tool interaction is unavailable for this Run.")`。自定义处理器不能绕过设置。意外终态延迟会以 `deferred_tools_unsupported` 失败。Harness UI 当前为异步子级选择不支持模式；普通异步提示续接仍可用。

#### Host 管理的反馈

内置内联执行器始终关闭延迟工具，即使父级支持。子级绑定工厂不能重新开启。内联 `resume_subagent` 仍只提供普通提示续接，不接受延迟反馈，也不代子级挂起父级。

负责子级执行的 Host 可开启延迟工具，并采用与根级相同的[原生恢复流程](state-and-resume.md#structured-suspension)。将确切 `HarnessRunResult.deferred` 批次与 `result.state` 一起保留，收集经身份验证的反馈，再用新绑定、`previous_state` 和 `DeferredToolResume` 直接调用该子级 `ExecutableAgent.run()` 或 `stream()`。Host 负责调度、检查点选择、反馈关联和交付。仅有观测事件不是可恢复检查点；恢复 Host 管理的子级不需要父模型调用。

只声明子级业务 `output_type`，可包含结构化 Pydantic 模型。构建根级和子级时，Harness 自动加入原生 `DeferredToolRequests` 支持；业务输出中显式包含该保留类型会被拒绝。挂起结果携带 `deferred`，没有业务输出。成功恢复后，`output_or_raise()` 返回声明的业务类型。

用量限制按子 Run 独立计算。每个子级获得自身定义限制与委派边编写限制逐字段的最严格交集，不继承父级预算或累加器。异步 Host 可进一步收紧。子事件单独标注归属；Host 可汇总用量用于显示，不引入共享强制上限。

## CodeAct

`CodeActCapability` 可提供：

- `run_code`：内联受限 Python；
- `run_program`：通过当前 Environment 读取的 `*.codeact.py` 程序。

运行时基于 Monty。支持的 `datetime` 和 `time` API 可读取系统时间，默认沙箱时区为 UTC。它没有隐式文件系统、网络、进程、环境或凭据访问。

### 声明允许使用的工具

工具所有者用类型化策略显式包装符合条件的工具：

```python
from a13n_harness.capabilities import CodeActCapability
from a13n_harness.toolsets import (
    CodeActPolicyToolset,
    CodeActToolPolicy,
)
from pydantic_ai.capabilities import Capability
from pydantic_ai.toolsets import FunctionToolset


def double(value: int) -> int:
    return value * 2


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

capabilities = (
    math_tools,
    CodeActCapability(),
)
```

不会从任意元数据、模型可见性或工具名推断使用资格，必须由所有者显式声明。

### 嵌套派发

受限代码获得生成的类型化宿主函数。嵌套调用通过当前最终构建的 Pydantic AI `ToolManager` 验证并执行，不使用第二个派发器。因此普通 Capability 钩子、Harness 受管理工具策略、提供方约束、事件、用量和延迟行为仍适用。根级内联延迟处理器可提供嵌套结果。子级拒绝或未解决根请求只使当前 CodeAct runner 调用失败；CodeAct 不持久化或恢复解释器栈帧。

源码验证、不可用函数、沙箱执行错误和资源超限都会返回失败工具结果，方便 Agent 修正代码或选择其他工具。即使未开始嵌套工具，也不消耗 runner 模型重试预算。外层调用 schema 验证和 runner 隔离规则仍采用正常重试策略。

嵌套调用可能已产生外部副作用时，不能只因后续 Python 程序失败就宣称可安全重试。只有嵌套调用开始后，失败元数据才将副作用标为不确定；已完成工作不回滚或自动重放。

### 通过 ToolProxy 使用大型工具集合

[ToolProxy 分组](tool-proxy.md#use-with-codeact)避免预先将大型集合填入 runner 目录。CodeAct 接收代理搜索/调用函数，按需发现确切 schema，并通过已有 `ToolManager` 桥接派发解析的目标。分组不授予 CodeAct 使用资格：先在来源工具发布类型化策略。`run_code` 和 `run_program` 都支持；代理调用保守地顺序执行，在 `asyncio.gather` 中也如此。

### 运行时状态

`run_code` 状态只存在于当前逻辑 Harness Run。调用间可保留普通解释器值，用 `restart=True` 清除。状态不进入 `HarnessState`，也不延续到新 Run。

`run_program`：

- 只接受严格 UTF-8 的 `*.codeact.py` 文件；
- 通过当前 Environment 读取；
- 要求恰好有 `async def main(inputs)`；
- 每次调用创建新的解释器会话；
- 接收 JSON 兼容输入。

Environment 路径不增加工具使用资格。代码只能调用注入的宿主函数。

## 如何选择

| 需求                                           | 使用方式                                                 |
| ---------------------------------------------- | -------------------------------------------------------- |
| 给具名子 Agent 一个范围明确的任务并等待        | 内联委派                                                 |
| 用本地 Python 控制流组合多个符合条件的工具调用 | CodeAct                                                  |
| 提交超出当前进程/Run 生命周期的持久任务        | Host 管理的异步子级或任务服务                            |
| 执行任意可信应用 Python                        | 普通应用代码，而非 CodeAct                               |
| 运行不可信 OS 代码                             | 真正隔离的 Environment provider，不能只靠 CodeAct 解释器 |

两种功能均可选。简单 Agent 应用应从普通原生 Capability 和工具开始，只有执行方式需要时才添加委派或 CodeAct。

### 显式保存数据，不保存解释器

启用 `CodeActCapability` 后，可在两种 runner 中用有意义的键保存 JSON 数据：

```python
await store(key="search.results", value={"ids": [12, 34], "next_page": 3})
```

后续输入、程序或续接 Run 可读取，无须重做搜索：

```python
results = await load(key="search.results")
results["ids"]
```

`load()` 列出全部键；`forget(key="search.results")` 删除一个键，并返回是否存在。没有消息/描述字段。缺失键报错，已存 null 加载为 `None`。加载对象是独立副本；修改后需再次 `store` 才能发布。

成功写入在后续沙箱失败和 `run_code(restart=True)` 后保留。跨 Run 延续要求 Host 保存并恢复 `HarnessState`；单靠此功能不是持久存储。普通变量和函数仍在 Run 结束时消失。新子级状态独立；Host 创建的状态 fork 是独立副本，不是共享映射。恢复数据不会恢复旧工具权限。

模型只收到有上限的键目录（最多 32 个键、4 KiB），不收到全部值。用 `load()` 发现省略的键。`CodeActConfig.max_state_entries` 默认 256，`max_state_bytes` 默认 10 MiB，限制含键在内的完整紧凑 JSON 命名空间。键需 1–256 个字符。值必须是有限 JSON；拒绝写入不替换已有数据。恢复状态也受这些限制。

CodeAct 让 Monty 解析 Python 名称，不用近似静态作用域检查器拒绝调用。真实宿主调用仍必须符合当前工具目录的使用策略。后续不可用调用可能在前面的工具执行后失败；完成的副作用和显式写入不回滚或自动重放。
