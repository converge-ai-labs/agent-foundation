---
title: Tracing Harness UI
sidebarTitle: Tracing
description: Export OpenTelemetry traces from Harness UI to Langfuse, Logfire, or any OTLP collector.
---

Harness UI uses the same OpenTelemetry hierarchy as the [Harness SDK](../a13n-harness/observation.md). It adds a bounded application operation around each admitted root submission and accepted async child segment, rather than duplicating model or tool spans. Tracing is optional and off by default.

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

When no Host tracer provider is already configured, the App creates its own OTLP/HTTP provider and passes it to every Harness build. Standard `OTEL_*` resource, sampling, batching, TLS, timeout and HTTP exporter configuration apply. A signal-specific trace endpoint must include `/v1/traces`; the common endpoint above is a base URL. Set `OTEL_SDK_DISABLED=true` to prevent automatic SDK setup.

The automatic path accepts `OTEL_TRACES_EXPORTER=otlp` or `none` and the `http/protobuf` protocol. It does not initialize a metrics exporter. Supply or configure a meter provider separately if you enable Harness metrics.

The App drains its own exporter after operations and collaborators finish, off the event loop and within its shutdown waiting budget. Export remains best effort; a trace is not proof that a checkpoint or side effect was saved.

## Langfuse

Use the Langfuse project endpoint and Basic authentication:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic%20BASE64_PUBLIC_KEY_COLON_SECRET_KEY,x-langfuse-ingestion-version=4'
```

Replace the placeholder with the base64 encoding of your project's `public-key:secret-key`; use the endpoint for your region or self-hosted server. The v4 header selects real-time OTLP ingestion. Shared Harness enrichment adds bounded trace name, Thread/session ID, tags and filterable metadata to selected descendants, with both automatic and explicit providers. It preserves native model, token, and tool observations. No Langfuse SDK is required for this OTLP path.

Look for `harness_ui.root`, its nested `harness.run`, and native Agent/model/tool spans. Async children have their own linked trace and Thread/session ID. Later turns on the same Thread are separate traces. Root application status can be failed even when the nested Harness completed, for example if continuation saving failed. Normal cancellation is not converted into an error.

Filter observation metadata by `root_thread_id` to correlate the whole workflow across separate sessions. Child traces also expose `parent_thread_id`, `subagent_role`, `execution_id`, `segment_index` and, on resume, `resumed_from_execution_id`. Resuming a child preserves its Thread/session but creates another execution segment and trace. The dispatch Link represents valid live context, not a persisted link to a previous process's trace. Roles are metadata, not prefixes added to session IDs.

## Logfire

Use the same instrumentation with Logfire's standard OTLP ingestion:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://logfire-us.pydantic.dev
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_LOGFIRE_WRITE_TOKEN'
```

For an EU project use `https://logfire-eu.pydantic.dev`. Replace both endpoint and headers when switching from Langfuse; remove any conflicting `OTEL_EXPORTER_OTLP_TRACES_*` overrides. No second agent instrumentor is needed.

An embedding application that already owns a Logfire or OTel provider can supply that exact provider instead:

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

Explicit instrumentation takes precedence over environment selection. Explicit `None` disables UI/Harness observation. Preconfigured global providers can also be selected through the Harness environment policy. The App never replaces, adds exporters to, or shuts down an external provider; its owning Host handles export and lifecycle. Selected UI/Harness spans receive the same automatic metadata regardless of exporter; no enrichment processor is required. The former optional `HarnessUiSpanProcessor` is no longer provided. Independently instrumented SDK spans do not receive its local-parent attribute propagation or Langfuse agent/tool type enrichment; Hosts needing that behavior own their SDK instrumentation. Do not also call `logfire.instrument_pydantic_ai()` for Harness-owned Agents.

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

Copy only when the private file does not exist. The templates default to public local Langfuse credentials and contain commented Logfire alternatives. `make cli` explicitly loads `dev/harness-ui/.env`; it does not write tracing settings to your user configuration. `CLI_ARGS` forwards normal CLI arguments and `HARNESS_UI_ENV` selects another private file. The SDK launcher initializes the provider with `opentelemetry-instrument`; the UI launcher relies on App setup.

## Deployment environment

The development template declares `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local`. This labels new root and child observations as `local`, independently of execution Environment profiles or mount names. For an existing private `dev/harness-ui/.env`, merge this resource attribute without replacing other settings and restart the CLI/App. Exported shell values take precedence; old observations retain their labels. An externally supplied provider must have its deployment resource set by its owning Host.

## Read the execution from the root down

Start at `harness_ui.root` (or a child trace's `harness_ui.subagent`):

1. Check root status and Thread/Run IDs. This span includes preparation and checkpoint saving, not only inference.
2. Expand `configuration` to see the captured Agent, Model, capabilities, selected integrations and Environment. This is a bounded summary, not a replay recipe; it omits credentials, prompts, endpoints and filesystem roots.
3. Follow `harness.run` and model/tool spans for timing, recovery, usage and cost. Check request-level model fields if a resolver changed the Model.
4. Filter by `root_thread_id` and child execution IDs to follow delegated work across traces.

### Host phases and skill inspection

`harness_ui.prepare` loads configuration and state; `harness_ui.finalize` saves continuation and finishes the Environment. Check `a13n.phase.step` and `a13n.ui.continuation.status` when a reply appears but saving fails. Skill summaries on the operation root and `harness.run` show available/read Skills for **that Thread**. A read is not proof the Agent followed the Skill.

## Content and limitations

At `standard` or `full`, operation roots also contain the current submission and final output, each capped at 8 KiB. The result is not inferred from the last model response. If saving fails after an answer was generated, the answer can remain visible while the operation status is failed. Inspect `a13n.input.capture`, `a13n.output.capture` and truncation flags for absent or partial values. `none` omits these bodies. Native media is descriptive only; no binary content, full history, credentials, raw exceptions or checkpoint bodies are copied into roots. `standard` opts into native model/tool content; `full` additionally opts into upstream binary and request parameter capture. `none` omits normal execution payloads but is **not** a complete scrubber for upstream exceptions, Agent descriptions, metadata or tool schemas. Review the [Harness information boundary](../a13n-harness/observation.md) before exporting real conversations, especially to a remote backend.

A missing trace is not a missing conversation. Transcript and checkpoint storage remain independent of sampling, export availability and backend retention.
