# Observation

The Harness exposes an opt-in OpenTelemetry observation layer. The Host owns SDK configuration, resources, sampling, processors, exporters, propagation, flush, and shutdown. The Harness never constructs an exporter or collector client.

`HarnessBuilder()` reads the bounded `A13N_HARNESS_*` policy variables at construction time. Their defaults keep observation disabled. Pass `instrumentation=None` to disable observation explicitly regardless of the environment, or pass a `HarnessInstrumentation` value to override the environment with exact providers.

## Configure a generic OpenTelemetry Host

Install and configure the OpenTelemetry SDK in the executable Host, then pass the exact providers to the Harness:

```python
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

from a13n_harness import (
    HarnessBuilder,
    HarnessInstrumentation,
    HarnessTraceContent,
    HarnessTraceLevel,
)

resource = Resource.create({"service.name": "agent-worker"})
tracer_provider = TracerProvider(resource=resource)
meter_provider = MeterProvider(resource=resource)

# Add Host-selected span processors, metric readers, and exporters here.

builder = HarnessBuilder(
    instrumentation=HarnessInstrumentation(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        trace_level=HarnessTraceLevel.STANDARD,
        trace_content=HarnessTraceContent.NONE,
    )
)
```

At least one provider is required when `HarnessInstrumentation` is present:

- tracer only enables trace-only observation;
- meter only enables metrics-only observation;
- both providers enable both signals;
- `instrumentation=None` disables both signals.

The Host must flush and shut down its providers at the process boundary. The Harness does not flush after each run.

## Select trace structure and content independently

Trace levels select structure:

| Level      | Selected structure                                                                     |
| ---------- | -------------------------------------------------------------------------------------- |
| `summary`  | One `harness.run` span for each logical Harness run.                                   |
| `standard` | Summary plus Pydantic AI Agent, model, tool, usage, streaming, and cancellation spans. |
| `verbose`  | Standard plus independently meaningful `harness.operation` spans.                      |

Content policy applies only to Pydantic AI instrumentation:

| Policy     | Normal text and tool content | Binary content | Serialized model request parameters |
| ---------- | ---------------------------- | -------------- | ----------------------------------- |
| `none`     | Omitted                      | Omitted        | Omitted                             |
| `standard` | Included                     | Omitted        | Omitted                             |
| `full`     | Included                     | Included       | Included                            |

A non-`none` policy requires standard or verbose tracing. The default is summary tracing with `none` content when a tracer provider is supplied.

`none` means the Host does not opt into ordinary execution payloads. It is not a universal redaction boundary for fields emitted by upstream Pydantic AI instrumentation. Before enabling standard or verbose tracing, treat Agent descriptions, metadata, tool definitions and schema defaults, and upstream exception text as telemetry-visible. `standard` also cannot redact binary values nested inside arbitrary custom models or dataclasses.

## Enable observation and select a collector through environment variables

The default builder mode is `instrumentation="environment"`. It reads these Harness policy variables once and obtains the OpenTelemetry global providers already configured by the executable Host:

| Variable                     | Values                                  | Default |
| ---------------------------- | --------------------------------------- | ------- |
| `A13N_HARNESS_TRACE_LEVEL`   | `off`, `summary`, `standard`, `verbose` | `off`   |
| `A13N_HARNESS_TRACE_CONTENT` | `none`, `standard`, `full`              | `none`  |
| `A13N_HARNESS_METRICS`       | `off`, `standard`                       | `off`   |

Invalid values and incompatible content/trace combinations fail during `HarnessBuilder()` construction.

Use the official OpenTelemetry Python distro and OTLP exporter to configure the SDK and collector target. The Harness does not reimplement their environment handling:

```bash
python -m pip install 'opentelemetry-distro[otlp]'

export A13N_HARNESS_TRACE_LEVEL=standard
export A13N_HARNESS_TRACE_CONTENT=none
export A13N_HARNESS_METRICS=standard

export OTEL_SERVICE_NAME=agent-worker
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318

opentelemetry-instrument python -m my_agent_host
```

`OTEL_EXPORTER_OTLP_ENDPOINT` selects a common collector endpoint; the official OTLP/HTTP exporters append `/v1/traces` and `/v1/metrics`. Signal-specific `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` take precedence when set. Standard `OTEL_*` variables also own resources, sampling, batching, headers, TLS, compression, timeout, and temporality.

The `opentelemetry-instrument` launcher configures global SDK providers before application code constructs `HarnessBuilder()`. An executable that configures providers in code can instead call `HarnessInstrumentation.from_environment(tracer_provider=..., meter_provider=...)`, or pass a fully explicit `HarnessInstrumentation` value. Explicit builder configuration wins over `A13N_HARNESS_*`; `instrumentation=None` is an explicit opt-out.

## Parent Harness runs through current context

The Harness uses only the current OpenTelemetry context. Make a Host span current while entering and consuming the stream:

```python
host_tracer = tracer_provider.get_tracer("agent-host")

with host_tracer.start_as_current_span("host.work"):
    result = await executable.run("Complete the task")
```

`harness.run` becomes a child of `host.work`. The Harness does not accept a span, vendor observation, or trace ID argument.

For streaming, the Harness keeps its logical-run span open through cleanup and consumer backpressure, but detaches it before every public stream item is returned. Consumer-side spans therefore remain children of the Host context rather than becoming children of `harness.run`.

A Thread is correlation, not a trace. A later resume normally starts a new trace unless the Host activates a bounded distributed parent. For durable or independently scheduled work, prefer a new trace with a standard span link.

## Harness metric registry

A supplied meter provider enables these low-cardinality Harness instruments under the `a13n-harness` instrumentation scope:

| Instrument                        | Type          | Unit        | Attributes            |
| --------------------------------- | ------------- | ----------- | --------------------- |
| `a13n.harness.run.duration`       | Histogram     | `s`         | `a13n.run.outcome`    |
| `a13n.harness.run.active`         | UpDownCounter | `{run}`     | none                  |
| `a13n.harness.run.model_attempts` | Histogram     | `{attempt}` | `a13n.run.outcome`    |
| `a13n.harness.operation.duration` | Histogram     | `s`         | `a13n.operation.kind` |

Pydantic AI separately owns native token usage, cost, and time-to-first-chunk metrics. The Harness does not duplicate them. IDs, Agent names, failure codes, content, paths, and error text are never Harness metric dimensions.

## Instrumentation ownership

The Harness is the only Pydantic AI instrumentation owner for Agents it builds. It rejects authored, plugin, and run-scoped Pydantic `Instrumentation` capabilities and direct `InstrumentedModel` values. It also disables ambient `Agent.instrument_all()` state on every built Agent.

Do not call `logfire.instrument_pydantic_ai()` for a Harness-built Agent. Instead, configure Logfire or Langfuse as a Host profile over the same OpenTelemetry providers:

- create exactly one Host root span;
- supply the exact selected provider objects to `HarnessInstrumentation`;
- attach multiple processors or exporters to that shared provider when exporting to multiple backends;
- include the `a13n-harness` instrumentation scope in backend export filters;
- never create parallel Langfuse and Logfire roots for the same work unit.

The Harness has no Langfuse- or Logfire-specific runtime dependency.
