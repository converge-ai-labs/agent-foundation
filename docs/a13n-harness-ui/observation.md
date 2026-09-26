# Tracing Harness UI

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

Replace the placeholder with the base64 encoding of your project's `public-key:secret-key`; use the endpoint for your region or self-hosted server. The v4 header selects real-time OTLP ingestion. Shared Harness enrichment adds bounded trace name, Thread/session ID, tags and filterable metadata to selected descendants, with both automatic and explicit providers. It preserves native Pydantic model, token and tool observations. No Langfuse SDK is required for this OTLP path.

Look for `harness_ui.root`, its nested `harness.run`, and native Agent/model/tool spans. Async children have their own linked trace and Thread/session ID. Later turns on the same Thread are separate traces. Root application status can be failed even when the nested Harness completed, for example if continuation saving failed. Normal cancellation is not converted into an error.

Filter observation metadata by `root_thread_id` to correlate the whole workflow across separate sessions. Child traces also expose `parent_thread_id`, `subagent_role`, `execution_id`, `segment_index` and, on resume, `resumed_from_execution_id`. Resuming a child preserves its Thread/session but creates another execution segment and trace. The dispatch Link represents valid live context, not a persisted link to a previous process's trace. Roles are metadata, not prefixes added to session IDs.

## Logfire

Use the same instrumentation with Logfire's standard OTLP ingestion:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://logfire-us.pydantic.dev
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_LOGFIRE_WRITE_TOKEN'
```

For an EU project use `https://logfire-eu.pydantic.dev`. Replace both endpoint and headers when switching from Langfuse; remove any conflicting `OTEL_EXPORTER_OTLP_TRACES_*` overrides. No second Pydantic instrumentor is needed.

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

1. Check its application status and Thread/Run or execution IDs. The root covers preparation and saving as well as inference.
2. Expand `configuration` in root observation metadata. It is a compact summary of the captured Run composition after reconstruction: Agent/model selection, allowlisted model parameters, active Capability IDs, plugin/MCP selections, tool allowlist mode, immediate subagent roster, Environment provider/adapter and Run Extensions. The configuration generation, Thread configuration version and package prompt revision identify the inputs used for this run. Request-level model fields remain authoritative if a resolver changes the model later.
3. Follow `harness.run`, native Agent/model/tool spans and material Harness operations for timing, outcomes, recovery, token usage and request-local cost provenance.
4. Use child execution lineage and `root_thread_id` to follow work across traces and sessions.

Configuration details stay on the operation root, not every generation or tool. The summary is capped at 8 KiB, lists at 16 entries with explicit counts and omissions. It includes only a fixed allowlist of numeric/boolean model settings and recognized reasoning/service-tier choices. It excludes instructions, global guidance, credentials and references, headers, request bodies, endpoint URLs, filesystem roots and arbitrary plugin/MCP/Environment configuration. It is available even with `trace_content=none`; disabling or sampling out the operation skips summary construction entirely. It is diagnostic context, not a complete replay recipe or another source of configuration authority.

### Host phases and skill inspection

`harness_ui.prepare` covers configuration/state loading and Agent reconstruction before `harness.run`. `harness_ui.finalize` covers Environment finalization and continuation saving after it. These are sibling spans, not another execution wrapper. Inspect the last `a13n.phase.step` and `a13n.ui.continuation.status` to distinguish inference, cleanup and saving problems. No per-file/per-hook span framework is introduced. Child preparation before task admission remains in the caller's context rather than being backdated into the child trace.

Phase metadata and output expose the actual decisions: preparation reports continuation/deferred-resume selection and Capability count; finalization reports continuation status, Environment finalization and cleanup-error count. A failed save remains visible even when Harness returned an answer. `none` keeps these structural metadata fields but omits phase output bodies; no checkpoint or answer body is duplicated into phase output.

The operation root and `harness.run` expose bounded available/accessed skill summaries. They report this Thread's observations, never a child's reads as the parent's. The actual native read-tool span carries the skill name/source; `harness.skills.resolve` measures catalog resolution at its real execution point. Access includes partial/repeated successful reads through recognized file tools. It is not proof of full loading, use from history, shell access, or compliance.

## Content and limitations

At `standard` or `full`, operation roots also contain the current submission and final output, each capped at 8 KiB. The result is not inferred from the last model response. If saving fails after an answer was generated, the answer can remain visible while the operation status is failed. Inspect `a13n.input.capture`, `a13n.output.capture` and truncation flags for absent or partial values. `none` omits these bodies. Native media is descriptive only; no binary content, full history, credentials, raw exceptions or checkpoint bodies are copied into roots. `standard` opts into native model/tool content; `full` additionally opts into upstream binary and request parameter capture. `none` omits normal execution payloads but is **not** a complete scrubber for upstream exceptions, Agent descriptions, metadata or tool schemas. Review the [Harness information boundary](../a13n-harness/observation.md) before exporting real conversations, especially to a remote backend.

A missing trace is not a missing conversation. Transcript and checkpoint storage remain independent of sampling, export availability and backend retention.
