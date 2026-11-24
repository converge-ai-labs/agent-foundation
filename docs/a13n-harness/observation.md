---
title: Observation
description: Export structured OpenTelemetry traces, spans, and metrics from Harness runs to Logfire, Langfuse, or any OTLP collector with fine-grained privacy filters.
---

The Host configures OpenTelemetry providers and exporters once at process startup; Harness instruments every Run, model call, tool execution, recovery step, and child Agent automatically through `HarnessInstrumentation` (do not wrap built Agents in duplicate `logfire.instrument_pydantic_ai()` or `InstrumentedModel`).

## Configure OpenTelemetry in Python

Install `opentelemetry-sdk`, save `observe.py`, and run `uv run python observe.py`:

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

## Privacy levels, spans, and metrics

Control exported payload detail via `trace_content` (`HarnessTraceContent` or `A13N_HARNESS_TRACE_CONTENT`):

| Content policy | Text & tool payloads | Binary media | Model request parameters |
| -------------- | -------------------- | ------------ | ------------------------ |
| `none`         | Omitted              | Omitted      | Omitted                  |
| `standard`     | Included (default)   | Omitted      | Omitted                  |
| `full`         | Included             | Included     | Included                 |

Traces nest `harness.prepare`, `harness.skills.resolve`, native Agent/Model/tool spans, `harness.operation` (recovery, delegation, handoff, compaction, tool review), and `harness.finalize` inside `harness.run` (or under an active Host span from `instrumentation.get_tracer("agent-host")`). Metrics are emitted under the `a13n-harness` scope:

| Instrument                        | Type          | Unit        | Attributes            |
| --------------------------------- | ------------- | ----------- | --------------------- |
| `a13n.harness.run.duration`       | Histogram     | `s`         | `a13n.run.outcome`    |
| `a13n.harness.run.active`         | UpDownCounter | `{run}`     | none                  |
| `a13n.harness.run.model_attempts` | Histogram     | `{attempt}` | `a13n.run.outcome`    |
| `a13n.harness.operation.duration` | Histogram     | `s`         | `a13n.operation.kind` |

## Export via environment variables, Logfire, or Langfuse

`HarnessBuilder()` defaults to `instrumentation="environment"`, reading `A13N_HARNESS_TRACE_LEVEL` (`off` | `verbose`), `A13N_HARNESS_TRACE_CONTENT` (`none` | `standard` | `full`), and `A13N_HARNESS_METRICS` (`off` | `standard`) from `os.environ`:

- **Generic OTLP collector (`opentelemetry-distro[otlp]`):**
  ```bash
  export A13N_HARNESS_TRACE_LEVEL=verbose A13N_HARNESS_METRICS=standard
  export OTEL_SERVICE_NAME=agent-worker OTEL_TRACES_EXPORTER=otlp OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=none
  export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
  opentelemetry-instrument python app.py
  ```
- **Logfire:** Set `LOGFIRE_TOKEN`, `LOGFIRE_SERVICE_NAME=agent-worker`, `A13N_HARNESS_TRACE_LEVEL=verbose`, and `A13N_HARNESS_METRICS=standard`, then call `logfire.configure()` **before** constructing `HarnessBuilder()`.
- **Langfuse (traces via OTLP v4; keep `A13N_HARNESS_METRICS=off`):** Set `OTEL_EXPORTER_OTLP_ENDPOINT="$LANGFUSE_BASE_URL/api/public/otel"` and `OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(pk:sk)>,x-langfuse-ingestion-version=4"` (or run `make langfuse-up` locally at `http://127.0.0.1:3000`). When using `LangfuseSpanProcessor` in Python, include `"a13n-harness"` in `should_export_span`.

For Service-managed telemetry, see [Service logs, metrics and traces](../a13n-service/configuration.md#logs-metrics-and-traces).
