---
title: Observation
description: Export structured OpenTelemetry traces, spans, and metrics from Harness runs to Logfire, Langfuse, or any OTLP collector with fine-grained privacy filters.
---

The Host configures OpenTelemetry providers and exporters; Harness instruments every Run, model call, tool execution, and child Agent automatically through `HarnessInstrumentation`.

## Configure OpenTelemetry in the Host

Install `opentelemetry-sdk`, save as `observe.py`, and run `uv run python observe.py`:

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

## Key spans and metrics

- **Root span:** `harness.run` (with child spans for model requests, tool calls, and inline subagents).
- **Metrics:** `a13n.harness.run.duration`, token usage, and error counters.
- **Run context:** Pass `HarnessObservationContext(name=..., labels=..., metadata=...)` on `RunBindings` to tag traces per request.

## Export to OTLP, Logfire, or Langfuse

Swap the in-memory exporter for any standard OpenTelemetry `OTLPSpanExporter` / `OTLPMetricExporter` (or Logfire / Langfuse OTLP endpoints).

- Configure privacy controls on `HarnessInstrumentation` to omit prompt/tool bodies when exporting to shared collectors.
- For Service-level telemetry configuration, see [Service logs, metrics and traces](../a13n-service/configuration.md#logs-metrics-and-traces).
