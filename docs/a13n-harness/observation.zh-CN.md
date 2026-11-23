---
title: 可观测性
description: 通过细粒度隐私过滤器将 Harness 运行的结构化 OpenTelemetry 链路追踪、Span 和指标导出到 Logfire、Langfuse 或任意 OTLP 收集器。
---

宿主负责配置 OpenTelemetry Provider 与导出器，Harness 则通过 `HarnessInstrumentation` 自动埋点每次运行、模型调用、工具执行和子智能体。

## 在宿主中配置 OpenTelemetry

安装 `opentelemetry-sdk`，保存为 `observe.py` 并运行 `uv run python observe.py`：

```python title="observe.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import AgentSpec, HarnessBuilder, HarnessInstrumentation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel


async def respond(
    messages: list[ModelMessage], info: AgentInfo
) -> AsyncIterator[str]:
    del messages, info
    yield "Observed locally."


async def main() -> None:
    resource = Resource.create({"service.name": "observation-demo"})
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    try:
        instrumentation = HarnessInstrumentation(
            tracer_provider=tracer_provider,
            meter_provider=meter_provider,
        )
        executable = HarnessBuilder(instrumentation=instrumentation).build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
        )
        result = await executable.run("Check observation")
        print(result.output_or_raise())
        tracer_provider.force_flush()
        print("Spans:", [s.name for s in spans.get_finished_spans()])
    finally:
        meter_provider.shutdown()
        tracer_provider.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## 核心 Span 与指标

- **根 Span：** `harness.run`（包含模型请求、工具调用与内联子智能体的子 Span）。
- **指标：** `a13n.harness.run.duration`、Token 用量与错误计数。
- **运行上下文：** 在 `RunBindings` 中传入 `HarnessObservationContext(name=..., labels=..., metadata=...)` 为每次请求打标签。

## 导出到 OTLP、Logfire 或 Langfuse

将内存导出器替换为标准的 `OTLPSpanExporter` / `OTLPMetricExporter`（或 Logfire / Langfuse 的 OTLP 端点）即可接入生产监控。

- 可在 `HarnessInstrumentation` 中配置隐私过滤器，在导出到共享收集器时隐藏提示词与工具正文。
- Service 级别的遥测配置见 [Service 日志、指标与追踪](../a13n-service/configuration.md#logs-metrics-and-traces)。
