# Tracing Harness UI

Harness UI uses the same OpenTelemetry hierarchy as the [Harness SDK](../a13n-harness/observation.md).
It adds a bounded application operation around each admitted root submission and
accepted async child segment, rather than duplicating model or tool spans.
Tracing is optional and off by default.

## Enable automatic OTLP export

Set the environment before starting the CLI, WebUI, or `open_harness_ui_app()`:

```bash
export A13N_HARNESS_TRACE_LEVEL=verbose
export A13N_HARNESS_TRACE_CONTENT=none
export A13N_HARNESS_METRICS=off
export OTEL_SERVICE_NAME=a13n-harness-ui
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector.example
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_COLLECTOR_CREDENTIAL'
a13n-harness-ui
```

When no Host tracer provider is already configured, the App creates its own
OTLP/HTTP provider and passes it to every Harness build. Standard `OTEL_*`
resource, sampling, batching, TLS, timeout and HTTP exporter configuration apply.
A signal-specific trace endpoint must include `/v1/traces`; the common endpoint
above is a base URL. Set `OTEL_SDK_DISABLED=true` to prevent automatic SDK setup.

The automatic path accepts `OTEL_TRACES_EXPORTER=otlp` or `none` and the
`http/protobuf` protocol. It does not initialize a metrics exporter. Supply or
configure a meter provider separately if you enable Harness metrics.

The App drains its own exporter after operations and collaborators finish, off
the event loop and within its shutdown waiting budget. Export remains best
effort; a trace is not proof that a checkpoint or side effect was saved.

## Langfuse

Use the Langfuse project endpoint and Basic authentication:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic%20BASE64_PUBLIC_KEY_COLON_SECRET_KEY,x-langfuse-ingestion-version=4'
```

Replace the placeholder with the base64 encoding of your project's
`public-key:secret-key`; use the endpoint for your region or self-hosted server.
The v4 header selects real-time OTLP ingestion. The App-owned profile copies
bounded trace name, Thread/session ID and tags to descendants, and preserves
native Pydantic model, token and tool observations. No Langfuse SDK is required
for this OTLP path.

Look for `harness_ui.root`, its nested `harness.run`, and native Agent/model/tool
spans. Async children have their own linked trace and Thread/session ID. Later
turns on the same Thread are separate traces. Root application status can be
failed even when the nested Harness completed, for example if continuation
saving failed. Normal cancellation is not converted into an error.

## Logfire

Use the same instrumentation with Logfire's standard OTLP ingestion:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://logfire-us.pydantic.dev
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_LOGFIRE_WRITE_TOKEN'
```

For an EU project use `https://logfire-eu.pydantic.dev`. Replace both endpoint
and headers when switching from Langfuse; remove any conflicting
`OTEL_EXPORTER_OTLP_TRACES_*` overrides. No second Pydantic instrumentor is needed.

An embedding application that already owns a Logfire or OTel provider can supply
that exact provider instead:

```python
from a13n_harness import HarnessInstrumentation, HarnessTraceContent
from a13n_harness_ui.app import open_harness_ui_app

# provider is the tracer provider already configured by your embedding Host.
instrumentation = HarnessInstrumentation(
    tracer_provider=provider,
    trace_content=HarnessTraceContent.NONE,
)
async with open_harness_ui_app(settings, instrumentation=instrumentation) as app:
    thread = await app.create_thread()
```

Explicit instrumentation takes precedence over environment selection. Explicit
`None` disables UI/Harness observation. Preconfigured global providers can also
be selected through the Harness environment policy. The App never replaces,
adds exporters to, or shuts down an external provider; its owning Host handles
export, enrichment and lifecycle. For Langfuse presentation on an explicit SDK
provider, the Host may add `HarnessUiSpanProcessor` from
`a13n_harness_ui.observation` before its exporter processors. A Logfire SDK Host
can instead retain its own profile. Do not also call
`logfire.instrument_pydantic_ai()` for Harness-owned Agents.

## Repository development

The repository provides separate ignored private environment files:

```bash
cp dev/harness-ui/.env.example dev/harness-ui/.env
cp dev/harness/.env.example dev/harness/.env
make langfuse-up
make cli
make harness-ui-smoke
make harness-dev HARNESS_ARGS=summary
```

Copy only when the private file does not exist. The templates default to public
local Langfuse credentials and contain commented Logfire alternatives. `make cli` explicitly loads `dev/harness-ui/.env`; it does not write tracing settings
to your user configuration. `CLI_ARGS` forwards normal CLI arguments and
`HARNESS_UI_ENV` selects another private file. The SDK launcher initializes the
provider with `opentelemetry-instrument`; the UI launcher relies on App setup.

## Content and limitations

Host operation spans contain correlation and status, not prompts, outputs,
credentials, raw exceptions or checkpoint bodies. `standard` opts into native
model/tool content; `full` additionally opts into upstream binary and request
parameter capture. `none` omits normal execution payloads but is **not** a
complete scrubber for upstream exceptions, Agent descriptions, metadata or tool
schemas. Review the [Harness information boundary](../a13n-harness/observation.md)
before exporting real conversations, especially to a remote backend.

A missing trace is not a missing conversation. Transcript and checkpoint storage
remain independent of sampling, export availability and backend retention.
