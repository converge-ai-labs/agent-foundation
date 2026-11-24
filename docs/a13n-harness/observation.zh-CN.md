---
title: 可观测性
description: 从 Harness 运行中导出结构化 OpenTelemetry 链路追踪、Span 与指标，支持细粒度隐私过滤并对接 Logfire、Langfuse 或任意 OTLP 采集器。
---

宿主进程在启动时统一初始化 OpenTelemetry Provider 与导出器；Harness 通过 `HarnessInstrumentation` 自动为每次运行、模型请求、工具调用、恢复步骤及子智能体生成埋点（请勿在同一智能体上重复叠加 `logfire.instrument_pydantic_ai()` 或 `InstrumentedModel`）。

## 在 Python 中配置 OpenTelemetry

安装 `opentelemetry-sdk`，保存 `observe.py` 并运行 `uv run python observe.py`：

```python title="observe.py"
import asyncio
from collections.abc import AsyncIterator

from a13n_harness import (
    AgentSpec,
    HarnessBuilder,
    HarnessInstrumentation,
    HarnessObservationContext,
    HarnessTraceContent,
    RunBindings,
)
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
            trace_content=HarnessTraceContent.STANDARD,
        )
        executable = HarnessBuilder(instrumentation=instrumentation).build(
            AgentSpec(), output_type=str, model=FunctionModel(stream_function=respond)
        )
        bindings = RunBindings.embedded(
            observation=HarnessObservationContext(
                name="answer-question",
                session_id="conversation-42",
                labels=("interactive", "support"),
                metadata={"channel": "web"},
            )
        )
        result = await executable.run("Check observation", bindings=bindings)
        print(result.output_or_raise())
        tracer_provider.force_flush()
        print("Spans:", [s.name for s in spans.get_finished_spans()])
    finally:
        meter_provider.shutdown()
        tracer_provider.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## 隐私级别、Span 层级与核心指标

通过 `trace_content`（`HarnessTraceContent` 或环境变量 `A13N_HARNESS_TRACE_CONTENT`）控制载荷采集粒度：

| 内容策略   | 文本与工具输入输出 | 二进制媒体 | 序列化模型请求参数 |
| ---------- | ------------------ | ---------- | ------------------ |
| `none`     | 省略               | 省略       | 省略               |
| `standard` | 包含（默认）       | 省略       | 省略               |
| `full`     | 包含               | 包含       | 包含               |

追踪链路在根 `harness.run`（或 `instrumentation.get_tracer("agent-host")` 创建的宿主父 Span）下依次嵌套 `harness.prepare`、`harness.skills.resolve`、原生 Agent/Model/Tool Span、`harness.operation`（恢复、委派、Handoff、压缩、工具风险审查）以及 `harness.finalize`。指标在 `a13n-harness` 作用域下导出：

| 指标名称                          | 类型          | 单位        | 属性标签              |
| --------------------------------- | ------------- | ----------- | --------------------- |
| `a13n.harness.run.duration`       | Histogram     | `s`         | `a13n.run.outcome`    |
| `a13n.harness.run.active`         | UpDownCounter | `{run}`     | 无                    |
| `a13n.harness.run.model_attempts` | Histogram     | `{attempt}` | `a13n.run.outcome`    |
| `a13n.harness.operation.duration` | Histogram     | `s`         | `a13n.operation.kind` |

## 通过环境变量对接 OTLP、Logfire 或 Langfuse

`HarnessBuilder()` 默认启用 `instrumentation="environment"`，从 `os.environ` 读取 `A13N_HARNESS_TRACE_LEVEL`（`off` | `verbose`）、`A13N_HARNESS_TRACE_CONTENT`（`none` | `standard` | `full`）与 `A13N_HARNESS_METRICS`（`off` | `standard`）：

- **标准 OTLP 采集器（`opentelemetry-distro[otlp]`）：**
  ```bash
  export A13N_HARNESS_TRACE_LEVEL=verbose A13N_HARNESS_METRICS=standard
  export OTEL_SERVICE_NAME=agent-worker OTEL_TRACES_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=none
  export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
  opentelemetry-instrument python app.py
  ```
- **Logfire：** 设置 `LOGFIRE_TOKEN`、`LOGFIRE_SERVICE_NAME=agent-worker`、`A13N_HARNESS_TRACE_LEVEL=verbose` 与 `A13N_HARNESS_METRICS=standard`，并在构造 `HarnessBuilder()` **之前**调用 `logfire.configure()`。
- **Langfuse（通过 OTLP v4 导出追踪；保持 `A13N_HARNESS_METRICS=off`）：** 设置 `OTEL_EXPORTER_OTLP_ENDPOINT="$LANGFUSE_BASE_URL/api/public/otel"` 与 `OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(pk:sk)>,x-langfuse-ingestion-version=4"`（本地调试可执行 `make langfuse-up` 启动 `http://127.0.0.1:3000`）。若在 Python 中直接挂载 `LangfuseSpanProcessor`，请在 `should_export_span` 中显式保留 `"a13n-harness"` 作用域。

Service 平台级遥测配置请参阅 [Service 日志、指标与追踪](../a13n-service/configuration.md#logs-metrics-and-traces)。
