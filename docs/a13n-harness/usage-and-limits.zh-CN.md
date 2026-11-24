---
title: 用量、限额与计费单价
description: 在 Harness 运行上强制执行请求数与 Token 预算，跨轮次和子智能体累计用量，并使用可定制单价目录估算费用。
---

通过 `AgentSpec.usage_limits`（`pydantic_ai.usage.UsageLimits`）设置请求数与 Token 上限，从 `result.usage` / `result.usage_records` 读取归因明细与 USD 费用，并通过 `usage_reporter` 向宿主实时推送快照。

## 设置运行预算并采集用量

```python title="usage_demo.py"
import asyncio
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.usage import UsageSnapshot, select_usage_snapshot
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits


class LatestUsage:
    def __init__(self) -> None:
        self.snapshots: dict[str, UsageSnapshot] = {}

    async def report(self, snapshot: UsageSnapshot) -> None:
        prev = self.snapshots.get(snapshot.usage_id)
        self.snapshots[snapshot.usage_id] = (
            snapshot.model_copy(deep=True) if prev is None else select_usage_snapshot(prev, snapshot)
        )


async def main() -> None:
    reporter = LatestUsage()
    executable = HarnessBuilder().build(
        AgentSpec(
            usage_limits=UsageLimits(request_limit=10, total_tokens_limit=50_000),
            retries={"tools": 2, "output": 1},
        ),
        output_type=str,
        model=TestModel(),
    )
    first = await executable.run("Start work", bindings=RunBindings.embedded(usage_reporter=reporter))
    continued = await executable.run(
        "Continue same accounting scope",
        previous_state=first.state,
        resume_usage=True,
        bindings=RunBindings.embedded(usage_reporter=reporter),
    )
    print(first.usage.requests, continued.usage.requests)


asyncio.run(main())
```

## 用量结构与记账作用域

- **`result.usage` / `stream.usage`：** 返回独立的 `RunUsageSummary`（模型请求数、输入/输出/缓存/音频 Token、工具调用数、`Decimal` 美元费用及未知单价标记）。
- **`result.usage_records`：** 单次调用的归因明细，携带 `call_id`（可与宿主的[发送前准入检查](hosting.md#check-model-calls-before-dispatch)关联）、`source`（主模型、`tool.review`、`files.media_understanding`、子智能体等）及命中的计价规则。
- **记账作用域：** 普通 `run()` / `stream()` 默认开启全新的 `usage_id` 作用域；传入 `resume_usage=True` 可沿用 `HarnessState`（`a13n.usage`）中保存的记账作用域继续累加，也可实现 `UsageDeltaReporter.report_delta(delta)` 进行增量落库。

## 计价目录与自定义单价

`HarnessBuilder` 默认安装 `CatalogModelCostCapability`，使用内置及后台刷新的单价目录（`a13n_harness.pricing.get_current_pricing_catalog()`，并根据响应实际服务的 `service_tier` 匹配档位单价）。

- **宿主后台自动更新：** 在常驻服务进程中启动一次 `prices.update_in_background()` 与 `a13n_harness.model_catalog_updates.run_official_model_updates`（设置 `A13N_OFFICIAL_MODELS_AUTO_UPDATE=0` 可关闭官方目录拉取）。
- **覆盖自定义单价：** 在构建期替换指定 `provider:model` 条目（或传入 `NoModelCostCapability()` 关闭本地估值）：

```python
from decimal import Decimal
from a13n_harness.pricing import (
    CatalogModelCostCapability,
    ModelPriceRule,
    ModelPricingEntry,
    PriceComponent,
)

entry = ModelPricingEntry(
    provider="demo",
    model="text-model",
    source="host",
    source_revision="v1",
    rules=(
        ModelPriceRule(
            rule_id="standard",
            prices=(
                PriceComponent(price_key="input_mtok", price=Decimal("1")),
                PriceComponent(price_key="output_mtok", price=Decimal("2")),
            ),
        ),
    ),
)
costs = CatalogModelCostCapability(pricing_updates={entry.key: entry})
```
