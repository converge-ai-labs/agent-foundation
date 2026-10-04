---
title: 用量、限制与定价
description: 用用量限制约束 Run，读取用量记录，并通过定价策略估算费用。
---

用量限制用于约束一次逻辑 Run，用量记录用于了解执行情况，定价策略用于估算费用。三者各有职责：请求预算用于强制限制，观测费用不是账单，终态结果也不是持久计费记录。

## 设置 Run 预算

```python
from a13n_harness import AgentSpec
from pydantic_ai.usage import UsageLimits

spec = AgentSpec(
    usage_limits=UsageLimits(request_limit=100, total_tokens_limit=200_000),
    retries={"tools": 2, "output": 1},
)
```

Run 可以替换整个 `UsageLimits`，不会逐字段合并。工具/输出重试不配置网络重试或中断模型恢复。默认值、子级和覆盖见[用量限制与重试](agents-and-runs.md#usage-limits-and-retries)。

## 用量

`result.usage` 和 `stream.usage` 返回脱离运行对象的 `RunUsageSummary` 值，包含当前模型请求、token/音频计数、工具调用、提供方回执、Decimal USD 费用，以及明确的未知费用/覆盖不完整标记。它们不是 Pydantic AI 可变的 `RunUsage`。`result.usage_records` 包含当前带归属的用量贡献。反复观测提供方挂起的生成，会更新同一份贡献，不增加请求或累加累计 token。

模型记录有可选 `call_id`，将贡献关联到原始 Model 调用，包括 Host 可选的[派发前检查](hosting.md#check-model-calls-before-dispatch)。用量报告使用 schema 版本 `1`；没有 `call_id` 的记录加载为 `call_id=None`，表示派发关联未知，不证明没有提供方调用。此 ID 不是 HTTP 请求 ID 或恰好一次的计费键，也不改变现有记录去重。中断调用只保留实际观测的用量；检查或分配 ID 本身不代表产生费用。

提供方集成可通过 `AgentContext.record_provider_usage()` 记录稳定的非模型回执。

### 保存与恢复用量

用量存储在 `a13n.usage` Capability 命名空间中，随 `HarnessState` 导出。默认情况下，每次 `run()` 或 `stream()` 调用都会重置用量统计，包括继续已保存状态的 Run、在 fork 出的 Thread 上执行的 Run，以及携带人工介入回答恢复执行的 Run。在运行中的流内引导不会重置统计。重置不删除之前持久化的消耗。

要显式继续同一统计范围：

```python
from a13n_harness.usage import UsageSnapshot

first = await executable.run("Start work")
assert first.state is not None
saved = UsageSnapshot.from_state(first.state)
assert saved is not None
continued = await executable.run(
    "Continue work", previous_state=first.state, resume_usage=True
)
```

运行时 Run ID 改变，但快照的 `usage_id`、原始贡献归属和序列继续。用量状态缺失或不匹配会明确失败。提供方挂起的生成要求此模式；继续该生成时重置会在派发前被拒绝。

Host 可通过 `RunBindings.usage_reporter` 绑定异步 `UsageReporter.report(snapshot: UsageSnapshot)`。每个 `usage_id` 只保存最新快照，用 `select_usage_snapshot` 验证替换，并原子提交各范围。不要累加累计快照，也不要将显示分块作为第二条录入路径。流的 `usage_report` 显示事件携带有上限的变更记录分块；reporter 接收完整独立状态。内联子级继承 reporter，但范围独立。报告失败会停止执行，应重试报告交付，而非重做模型任务。

需要增量存储时，可改为绑定 `UsageDeltaReporter.report_delta(delta: UsageDelta)`。`delta.scope` 包含统计范围的标识、当前序列和工具调用计数；`delta.records` 只包含 `delta.after_sequence` 之后发生变化的记录的最新值。返回前，必须将这些替换记录和范围进度原子提交。提交结果不确定时，应接受重试和重叠区间，但拒绝超出已存进度、造成缺口的区间。Harness 保留未确认的变化用于重试，与显示交付相互独立。如果 reporter 同时实现两种方法，Harness 调用 `report_delta`。完整检查点和结果记录仍然可用。在使用 `resume_usage=True` 前，需确保恢复的统计序列及对应记录已经持久化到你的存储中。

持久统计比所选执行检查点更新时，显式恢复统计前使用 `latest_snapshot.restore(checkpoint)`。它只覆盖统计，不移动消息历史或恢复执行权限。每个范围的写入需串行。独立 worker 尝试需不同范围，防止晚到旧 worker 覆盖新统计。快照最多 10,000 条记录、16 MiB；达到容量会失败，不静默丢弃观测。

## 估算模型费用

模型费用估值默认开启。`HarnessBuilder` 注入 `CatalogModelCostCapability`，为构建后的 Agent 固定当前有效的价格目录。Host 未开启更新时，使用内置 `genai-prices` 数据和 Harness 补充。`get_default_pricing_catalog()` 始终读取该内置基线；`get_current_pricing_catalog()` 还采用成功的上游更新。两者返回不可变目录，不下载。读取或导出当前快照：

```python
from a13n_harness.pricing import get_current_pricing_catalog

pricing = get_current_pricing_catalog()
entry = pricing["openai:gpt-5.5"]
exported = pricing.model_dump(mode="json")
```

### 在 Host 中更新价格

Pydantic AI 提供 `prices.update_in_background()`。在最终应用进程中启动一次，不要在导入时或 fork worker 进程前启动。以下示例使用应用自己的 `serve()` 函数：

```python
from pydantic_ai import prices

async def main():
    with prices.update_in_background():
        await serve()
```

上游更新器立即下载，之后每小时下载。启动无须等待首次下载：内置价格立即可用，下载失败保留最后有效数据。后续每次 `HarnessBuilder.build()` 自动捕获验证后的更新，无须重启或清空缓存。已构建的执行对象即使复用也保留旧价格；重建才会更新。同一规则保证活动 Run 和内联后代的价格稳定。

异步 Host 应在事件循环外捕获目录，再传给 `HarnessBuilder.build()`。显式快照只用于默认定价；自定义模型费用 Capability 仍优先：

```python
from anyio import to_thread
from a13n_harness import HarnessBuilder
from a13n_harness.pricing import get_current_pricing_catalog

catalog = await to_thread.run_sync(get_current_pricing_catalog)
executable = HarnessBuilder().build(definition, pricing_catalog=catalog)
```

下载的条目覆盖包内标准价格；缺失条目保留内置覆盖。上游 `genai-prices` 无服务层级选择器，因此包内层级规则补充更新后的标准条目。价格和来源参与实际版本标识。显式完整条目覆盖仍可替换或移除这些规则。转换失败保留上一有效目录。同样下载内容的版本不随获取时间改变。不创建价格历史或磁盘缓存。停止更新器不清除已下载价格；构建必须忽略其他进程活动、使用内置数据时，将 `get_default_pricing_catalog()` 传入 `pricing_catalog`。

### 按实际服务层级定价

Harness 使用 `ModelResponse.provider_details` 中的**实际服务层级**，不使用请求的 `service_tier`。Pydantic AI 为 OpenAI Chat/Responses（含流式）和 Gemini Developer API 提供此信息。priority 请求降级为 `default` 时按标准费率计算。费用在每个响应的原生用量累加前计算，包括继承的子级和辅助策略。

`ModelPriceRule.service_tier` 选择确切层级；省略值描述标准定价。该层级内最后匹配的活动规则生效，与日期/时间条件独立。缺少层级元数据时使用标准价格；`default`、`standard` 和 `on_demand` 可使用无层级规则。其他层级需要显式匹配规则，不存在通用折扣/加价倍数。OpenAI 响应的 `fast` 名称选择 `priority` 费率。提供方未公布上下文上限以上价格时，`max_input_tokens` 限制费率适用范围。按 token 长度分段的费率仍由 `PriceComponent.tiers` 定义，是独立维度。

未知或不支持的层级会使 Harness 放弃估值，不静默用标准费率。已有上游费用仍保留原费用来源；否则费用未知，不是零。实际层级元数据格式错误会报告定价失败，不使 Run 失败。缺少层级元数据**不能** 证明采用标准服务。

内置 token 价格覆盖在 **2026 年 9 月 26 日** 依据官方价目表检查：

| 提供方                                                                                                                    | 包含的公共规则                                                                                                                                                                                                                                                          | 重要限制                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [OpenAI](https://developers.openai.com/api/docs/pricing)                                                                  | Flex 与 Fast 表合计 25 个模型：GPT-6 Astra/Sol/Luna；GPT-5.6 Sol/Terra/Luna；GPT-5.5/Pro；GPT-5.4/Mini/Nano/Pro；GPT-5.2；GPT-5.1；GPT-5/Mini/Nano；GPT-4.1/Mini/Nano；GPT-4o/2024-05-13/Mini；o3；o4-mini                                                              | 每个模型只包含已公布层级。[Fast](https://developers.openai.com/api/docs/guides/fast-mode) 也使用 `priority` 名称。公布的长上下文费率从超过 272,000 输入 token 开始。GPT-5.5 Fast、GPT-5.4 Fast 和 GPT-5.5 Pro Flex 超过该边界时放弃估值，不编造费率。 |
| [Gemini Developer API](https://ai.google.dev/gemini-api/docs/pricing)                                                     | Gemini 3.8/3.7/3.6 Flash、3.5 Flash/Flash-Lite、3.1 Flash-Lite/Pro Preview、3 Flash Preview、2.5 Pro/Flash/Flash-Lite 的 Standard、[Flex](https://ai.google.dev/gemini-api/docs/flex-inference) 和 [Priority](https://ai.google.dev/gemini-api/docs/priority-inference) | 保留确切缓存和音频价格，包括多个模型不变的 Flex 缓存价格。Pro 上下文价格分界在超过 200,000 token 时。3.6–3.8 Flash 的初始费率于 2027 年 1 月 1 日改变。                                                                                               |
| [Vertex AI](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/priority-paygo)                                    | 不自动提供层级费率                                                                                                                                                                                                                                                      | 实际 `traffic_type` 作为小写层级标识传递（如 `on_demand_priority`）。不借用 Developer API 费率为 Vertex 流量定价。需在自定义策略中编写端点专用规则。                                                                                                  |
| [Anthropic](https://platform.claude.com/docs/en/api/service-tiers) 和 [Groq](https://console.groq.com/docs/service-tiers) | 不推断非标准费率                                                                                                                                                                                                                                                        | Anthropic priority 和 Groq performance 是容量契约，不是通用 token 附加费；Groq Flex 使用按需价格。原生上游适配器目前不公开实际层级。Anthropic Fast 使用独立 `speed` 维度，此处也不可用。请求设置不是计费证据。                                        |

这些是 token 费用估计，不能保证与账单一致：地区加价、容量承诺、存储时间、grounding、其他产品费用和议价不能从层级推导。内置层级价格随包更新，不随上游标准价格下载器改变。

所选 Model 的 `TokenPricingCapability` 策略（包括已保存 Service Model 定价）需在完整条目中添加层级规则。已有仅标准条目不会静默换成公共目录价格；未显式配置前，它们对非标准层级放弃估值。在 Service 中，Console 标准价格编辑器保留编写的层级规则。

### 覆盖定价

要替换价格，创建完整 `ModelPricingEntry`，传入浅更新字典。每个值替换对应 `provider:model` 的整个条目，优先于下载和内置价格，不合并嵌套字段：

```python
from a13n_harness import HarnessBuilder
from a13n_harness.pricing import CatalogModelCostCapability

costs = CatalogModelCostCapability(
    pricing_updates={replacement.key: replacement},
)
executable = HarnessBuilder().build(
    spec,
    output_type=str,
    capabilities=(costs,),
)
```

构建时通过 `capabilities=` 提供一个自定义 `AbstractModelCostCapability`，会原子替换默认策略。多于一个会导致定义错误。使用 `NoModelCostCapability()` 可显式只保留提供方或上游库费用，不由 Harness 估值。内联子 Run 继承父级所选策略，使共享用量树估值一致；同一子定义独立执行时使用自己的构建策略。

定价失败或未找到模型不会使 Agent Run 失败。记录标明定价状态、目录版本、所选规则和实际费用来源。持久汇总、核对、议价折扣、计费和 exporter 交付仍由 Host 负责。

## 职责边界

- Pydantic AI 负责原生用量累加和限制检查。
- Harness 标注根级、子级和提供方工作归属，并为执行对象捕获定价策略。
- Host 负责持久汇总、计费、议价，以及是否开启后台目录更新。

指标和追踪见[观测](observation.md)。
