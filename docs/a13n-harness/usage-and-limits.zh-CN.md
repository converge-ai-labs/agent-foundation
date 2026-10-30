---
title: 用量、限制与定价
description: 运行离线用量示例，选择统计重置时机，并配置费用估算。
---

先设置 Agent 的执行预算，再查看实际观测的用量，最后决定 Host 需要持久化什么。下面用同一份已保存对话，演示新建统计范围和显式继续原范围的区别。

## 设置 Run 预算

使用[快速开始](getting-started.md)中的环境。将代码保存为 `usage_demo.py`，在仓库根目录运行 `uv run --locked python usage_demo.py`。示例使用离线 Model 和内存 reporter，无需凭据。

```python
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder, HarnessState, RunBindings
from a13n_harness.usage import UsageSnapshot, select_usage_snapshot
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    yield "done"


class LatestUsage:
    """In-memory replacement store for this single-writer example."""

    def __init__(self) -> None:
        self.snapshots: dict[str, UsageSnapshot] = {}

    async def report(self, snapshot: UsageSnapshot) -> None:
        previous = self.snapshots.get(snapshot.usage_id)
        self.snapshots[snapshot.usage_id] = (
            snapshot.model_copy(deep=True)
            if previous is None
            else select_usage_snapshot(previous, snapshot)
        )


async def main() -> None:
    reporter = LatestUsage()
    executable = HarnessBuilder().build(
        AgentSpec(
            usage_limits=UsageLimits(request_limit=3, total_tokens_limit=10_000),
            retries={"tools": 2, "output": 1},
        ),
        output_type=str,
        model=FunctionModel(stream_function=respond),
    )
    first = await executable.run(
        "Start work", bindings=RunBindings.embedded(usage_reporter=reporter)
    )
    assert first.output_or_raise() == "done"
    assert first.state is not None
    restored = HarnessState.model_validate_json(first.state.model_dump_json())
    initial = UsageSnapshot.from_state(restored)
    assert initial is not None

    fresh = await executable.run(
        "A new accounting scope", previous_state=restored,
        bindings=RunBindings.embedded(usage_reporter=reporter),
    )
    continued = await executable.run(
        "Continue the original accounting scope", previous_state=restored,
        resume_usage=True,
        bindings=RunBindings.embedded(usage_reporter=reporter),
    )
    assert fresh.output_or_raise() == continued.output_or_raise() == "done"
    assert (first.usage.requests, fresh.usage.requests, continued.usage.requests) == (1, 1, 2)
    assert continued.run_id != first.run_id
    assert reporter.snapshots[initial.usage_id].summary.requests == 2
    print("requests: first=1, fresh=1, continued=2")


if __name__ == "__main__":
    asyncio.run(main())
```

输出为 `requests: first=1, fresh=1, continued=2`。每次调用只发起一个 Model 请求。第三次调用保留第一次的统计，所以汇总中包含两个请求。

`AgentSpec.usage_limits` 提供预算。Run 的 `usage_limits=` 替换整个值，不逐字段合并。恢复的统计也计入所选预算。工具和输出重试控制 Agent 循环内的尝试；网络重试和中断模型恢复有独立设置。默认值和子级预算见[用量限制与重试](agents-and-runs.md#usage-limits-and-retries)。

## 用量

Run 结束后读取 `result.usage`，流式执行中读取 `stream.usage`。两者都返回独立的 `RunUsageSummary`，包含模型请求、token/音频计数、工具调用、提供方回执、Decimal USD 费用，以及未知费用和覆盖不完整字段。`result.usage_records` 提供汇总背后的各项用量及其归属。

提供方继续同一次生成时，会更新已有记录。反复观测累计值不会重复增加请求或 token。提供方集成可通过 `AgentContext.record_provider_usage()` 添加非模型回执；这些回执不改变原生模型 token 限制。

模型记录可带有 `call_id`，关联原始调用和 Host 的[派发前检查](hosting.md#check-model-calls-before-dispatch)。版本 1 的历史报告缺少该字段时加载为 `None`。去重使用用量贡献的身份：调用 ID 既不是 HTTP 请求 ID，也不是恰好一次的计费键。中断调用保留已观测用量；仅分配 ID 不代表产生费用。

### 保存与恢复用量

`HarnessState` 在 `a13n.usage` 命名空间中导出统计。在下一次调用时选择范围：

| 调用                                  | 统计行为                                                |
| ------------------------------------- | ------------------------------------------------------- |
| 默认设置的 `run()` 或 `stream()`      | 新建范围，包括从已保存状态继续、fork 和携带人工回答恢复 |
| `resume_usage=True`，且已保存用量匹配 | 在新的运行时 Run ID 下继续原 `usage_id`、用量归属和序列 |
| 引导活动流                            | 保持当前范围                                            |

示例 reporter 为每个 `usage_id` 保存一份最新快照。`select_usage_snapshot` 验证替换，并保留兼容的较新值。生产存储应原子提交替换，每个范围只由一个写入方更新。重置会新建范围，不删除之前持久化的消耗。统计缺失或不匹配时，显式恢复会失败。继续提供方挂起的生成必须使用 `resume_usage=True`。

增量存储实现 `UsageDeltaReporter.report_delta(delta)`。`delta.scope` 携带身份、序列和工具调用计数；`delta.records` 携带 `delta.after_sequence` 之后变化的记录的最新值。替换记录和进度应一起提交。提交结果不确定时，接受重试和重叠区间；拒绝超出已存进度而产生缺口的区间。两种 reporter 方法都存在时，Harness 选择 `report_delta`。完整检查点和结果记录仍可读取。

reporter 交付与显示交付分开。reporter 接收独立的统计数据；`usage_report` 显示事件携带有上限的变更分块。选择一条统计录入路径，不把快照或显示分块累加成费用。内联子级继承 reporter，但各自使用独立范围。报告失败会停止执行并保留待交付数据：应重试交付，而非重做 Model 工作。

显式恢复统计前，先确保恢复的序列和记录已经持久化。存储比执行检查点更新时，先应用 `latest_snapshot.restore(checkpoint)`。它只覆盖统计，保留该检查点的消息历史及当前执行授权要求。独立 worker 尝试使用不同范围。每份快照上限为 10,000 条记录和 16 MiB；超限会失败，不会丢弃观测。

## 估算模型费用

每个构建后的 Agent 都有一个模型费用策略。默认情况下，`HarnessBuilder` 注入使用不可变价格目录的 `CatalogModelCostCapability`。查看当前进程中的目录：

```python
from a13n_harness.pricing import get_current_pricing_catalog

pricing = get_current_pricing_catalog()
print(pricing.revision)
print(list(pricing)[:5])
exported = pricing.model_dump(mode="json")
```

`get_default_pricing_catalog()` 返回内置 `genai-prices` 数据和包内 Harness 补充。`get_current_pricing_catalog()` 还采用成功的进程内更新。两个函数都不下载数据。导出内容包含完整条目、规则条件、来源和内容版本；通过它查看支持的模型与费率，无需维护另一份模型价格清单。

### 在 Host 中更新价格

在最终应用进程中启动一次更新。下面的 helper 接收应用的异步服务函数，在服务结束时停止两个更新循环：

```python
from collections.abc import Awaitable, Callable

import anyio
from pydantic_ai import prices
from a13n_harness.model_catalog_updates import run_official_model_updates


async def serve_with_price_updates(serve: Callable[[], Awaitable[None]]) -> None:
    updater = prices.update_in_background()
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(run_official_model_updates)
            try:
                await serve()
            finally:
                tasks.cancel_scope.cancel()
    finally:
        updater.stop()
```

在应用入口调用 `await serve_with_price_updates(serve)`，其中 `serve` 管理服务器生命周期。调用该 helper 会启用下载。不要在导入或 fork 前初始化时调用。Harness UI 和 Service 已管理官方模型更新循环。

上游更新器立即下载，之后每小时刷新。官方循环刷新仓库中共享的模型信息/价格文件，使用启动抖动、每小时调度和重试退避。完整验证文件后，才一起替换两个视图。进程内共享调度避免每个 Agent 各自下载。失败时保留最后有效数据，启动时可直接使用内置数据。`A13N_OFFICIAL_MODELS_AUTO_UPDATE=0` 关闭新启动的官方循环，不影响上游价格更新。

新构建采用刷新后的价格。已有执行对象、活动 Run 和内联后代保留所选策略。异步 Host 应在事件循环外捕获目录：

```python
from anyio import to_thread
from a13n_harness import AgentDefinition, HarnessBuilder
from a13n_harness.pricing import get_current_pricing_catalog


async def build_with_current_prices(definition: AgentDefinition):
    catalog = await to_thread.run_sync(get_current_pricing_catalog)
    return HarnessBuilder().build(definition, pricing_catalog=catalog)
```

向 `build_with_current_prices` 传入完整定义，包括 Model 和输出类型。下载的标准条目替换对应包内价格；缺失条目保留内置覆盖。上游目录没有层级选择器，因此官方服务层级规则补充上游标准价格。显式完整条目覆盖优先于两者。

目录转换失败时保留最后有效目录。相同有效内容的版本不随下载时间改变。更新不创建磁盘缓存或价格历史，也不改写已保存的 Model 设置。停止更新器后，进程中已发布的数据仍可使用。需要可复现的内置基线时，将 `get_default_pricing_catalog()` 传给 `pricing_catalog=`。显式自定义费用 Capability 仍优先于此构建参数。

### 按实际服务层级定价

Harness 在原生用量累加前，使用 `ModelResponse.provider_details` 中的实际服务层级为每个响应定价。请求中的 `service_tier` 不选择费率。例如，priority 请求实际按 `default` 服务时，使用标准价格。

| 规则字段或响应值                                   | 效果                                   |
| -------------------------------------------------- | -------------------------------------- |
| `ModelPriceRule.service_tier`                      | 精确匹配层级，最后一条活动匹配规则生效 |
| 无层级元数据、`default`、`standard` 或 `on_demand` | 可使用无层级的标准规则                 |
| OpenAI 响应层级 `fast`                             | 选择 `priority` 规则                   |
| 其他非标准层级                                     | 需要显式规则，不使用通用折扣或加价     |
| `max_input_tokens`                                 | 包含边界的上限，更大的请求放弃该费率   |
| `PriceComponent.tiers`                             | 按 token 长度分段，与服务层级选择独立  |

未知层级会放弃 Harness 估值；已有上游费用会保留，否则费用维持未知。格式错误的层级元数据记录为定价失败，不使 Run 失败。缺少元数据时可沿用标准估计，但不能据此确定实际服务层级。

目录保留各端点的覆盖边界。OpenAI 和 Gemini Developer API 的响应元数据可选择各自已发布的层级规则。Vertex `traffic_type` 转为小写层级标识，但目录不会借用 Developer API 层级费率为 Vertex 定价。这类流量应提供端点专用策略。仅请求的容量或速度偏好不能确定实际 token 费率。

从已安装条目读取上下文边界、缓存/音频价格和日期规则。token 估计不包含地区加价、容量承诺、存储时间、grounding 费用和议价折扣等账单调整。重复工作日规则不建模公共节假日日历。官方层级补充通过包或官方文件更新刷新，与上游标准价格独立。

所选 Model 使用 `TokenPricingCapability` 时，在其完整条目中编写层级规则。已保存的仅标准策略在修改前保持原覆盖，不会被公共费率静默替换。Service 的标准价格编辑器保留已有的自定义层级规则。

### 覆盖定价

为响应的 `provider:model` 键提供完整替换条目。下面使用虚构费率：每百万输入和输出 token 分别为 USD 1 和 USD 2。请按自己的策略替换身份和价格：

```python
from decimal import Decimal

from a13n_harness import AgentSpec, HarnessBuilder
from a13n_harness.pricing import (
    CatalogModelCostCapability, ModelPriceRule, ModelPricingEntry, PriceComponent,
)
from pydantic_ai.models import Model


def build_with_custom_prices(model: Model):
    replacement = ModelPricingEntry(
        provider="demo", model="text-model",
        source="host", source_revision="demo-v1",
        rules=(ModelPriceRule(
            rule_id="standard",
            prices=(
                PriceComponent(price_key="input_mtok", price=Decimal("1")),
                PriceComponent(price_key="output_mtok", price=Decimal("2")),
            ),
        ),),
    )
    costs = CatalogModelCostCapability(pricing_updates={replacement.key: replacement})
    return HarnessBuilder().build(
        AgentSpec(), output_type=str, model=model, capabilities=(costs,),
    )
```

将配置好的原生 Model 传给 `build_with_custom_prices`。该条目适用于身份为 `demo:text-model` 的响应；其他身份保持目录覆盖。`pricing_updates` 替换整个条目，包括所有层级规则，不合并嵌套字段。

构建时提供一个 `AbstractModelCostCapability` 即可替换默认策略。多于一个会导致定义错误。`NoModelCostCapability()` 保留提供方/上游费用，不由 Harness 估值。内联子级继承父策略；同一子级独立执行时使用自己的构建策略。

未找到模型或定价失败不会使 Agent Run 失败。查看各记录的定价状态、版本、所选规则及实际费用来源。未知费用与零不同。

## 职责边界

Pydantic AI 检查原生用量限制。Harness 标注模型、子级和提供方工作的归属，并按执行对象固定的策略估值。Host 持久化和核对观测、应用商业计费规则，并管理更新器生命周期。终态结果是统计输入；持久计费依赖 Host 的提交。

指标和追踪见[观测](observation.md)。
