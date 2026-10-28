---
title: Tracing Harness UI
sidebarTitle: Tracing
description: Send a test Run to an OTLP backend, inspect its execution, and choose which content to export.
---

Use tracing to find where a Run spent time, which Model and tools it used, and whether saving its continuation succeeded. Tracing is off by default. Start with a working [first conversation](setup.md) and an OTLP/HTTP collector, or use the repository's local development workflow below.

## Enable automatic OTLP export

1. Open a Bash-compatible terminal. Set the tracing policy and your collector's base URL and credentials:

   ```bash
   export A13N_HARNESS_TRACE_LEVEL=verbose
   export A13N_HARNESS_TRACE_CONTENT=none
   export A13N_HARNESS_METRICS=off
   export OTEL_SERVICE_NAME=a13n-harness-ui
   export OTEL_TRACES_EXPORTER=otlp
   export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
   export OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector.example
   export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_COLLECTOR_CREDENTIAL'
   ```

2. Replace the endpoint and header placeholders. For Langfuse or Logfire, use the matching section below. Keep `trace_content=none` for the first check.

3. Start Harness UI from that terminal:

   ```console
   a13n-harness-ui
   ```

4. Send a short test prompt. After the reply, find `harness_ui.root` in your collector and expand its `harness.run` and model spans. For WebUI, start `a13n-harness-ui webui` from the same environment instead.

The environment must be set **before** the CLI, WebUI, or embedded App starts. `OTEL_EXPORTER_OTLP_ENDPOINT` is a base URL; the signal-specific `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` must include `/v1/traces`. Remove conflicting `OTEL_EXPORTER_OTLP_TRACES_*` overrides when switching backends.

When no Host provider exists, the App creates a private OTLP/HTTP tracer provider. Automatic setup supports `OTEL_TRACES_EXPORTER=otlp` or `none` and `http/protobuf`. Standard OTel resource, sampling, batching, TLS, and timeout settings apply. `OTEL_SDK_DISABLED=true` prevents automatic SDK setup. Metrics need a separately configured meter provider.

If no trace arrives, check the endpoint, credentials, signal-specific overrides, sampling settings, and [local diagnostics](automation-and-troubleshooting.md#logs-updates-and-exit). A successful reply verifies model execution; seeing the trace verifies export. Saved conversations remain available independently of tracing and backend retention.

## Langfuse

Keep the tracing policy above and replace the endpoint and headers:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=Basic%20BASE64_PUBLIC_KEY_COLON_SECRET_KEY,x-langfuse-ingestion-version=4'
```

Encode your project's `public-key:secret-key` as a single-line base64 value and use it in place of the placeholder. Select the endpoint for your project's region or self-hosted server. The `x-langfuse-ingestion-version=4` header selects real-time v4 ingestion. This integration uses OTLP directly; no Langfuse SDK is needed.

After a test Run, locate its Thread ID as the Langfuse session ID. Each turn has its own trace. Async children use their own Thread/session IDs; filter metadata by `root_thread_id` to find the whole family. Harness adds names, tags, and correlation metadata to selected descendant spans while retaining native model, token, and tool observations.

## Logfire

Keep the same tracing policy and replace both endpoint and headers:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://logfire-us.pydantic.dev
export OTEL_EXPORTER_OTLP_HEADERS='Authorization=YOUR_LOGFIRE_WRITE_TOKEN'
```

Use your project's write token and region; EU projects use `https://logfire-eu.pydantic.dev`. Harness already instruments its Agents, so do not also call `logfire.instrument_pydantic_ai()`.

For an embedding Host that already owns a Logfire or OTel provider, pass that provider to the App. Inside the async entry point from [Embedding](embedding.md), after loading `settings` and `configuration_path`, use this integration fragment:

```python
from a13n_harness import HarnessInstrumentation, HarnessTraceContent
from a13n_harness_ui.app import open_harness_ui_app

instrumentation = HarnessInstrumentation(
    tracer_provider=provider,
    trace_content=HarnessTraceContent.NONE,
)
async with open_harness_ui_app(
    settings,
    configuration_path=configuration_path,
    instrumentation=instrumentation,
) as app:
    thread = await app.create_thread()
    receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Say hello.")
    operation = await app.wait_root_operation(receipt.receipt_id)
    print(operation.status)
```

Here `provider` is the Host's already configured tracer provider. Explicit instrumentation takes precedence over environment selection; explicit `None` disables UI/Harness observation. With the default selection, the `A13N_HARNESS_*` policy can use an existing global OTel provider. The Host owns export and shutdown for either kind of external provider. The App neither replaces it nor adds exporters.

UI/Harness metadata enrichment works with either provider path. Independently instrumented SDKs remain the embedding Host's responsibility. The former `HarnessUiSpanProcessor` is no longer provided.

## Repository development

From a prepared [source checkout](installation.md#run-from-source), start local Langfuse and run the scripted App smoke test:

```bash
make langfuse-up
make harness-ui-smoke
```

The smoke test uses a local scripted model and prints Thread/Run IDs. Find two root traces and a linked child trace in Langfuse. It retains its disposable instance under `var/harness-ui-smoke/<id>/` and needs no real model credentials.

To trace your own interactive work, run `make cli`. It uses checkout-local configuration/data and explicitly loads the ignored `dev/harness-ui/.env`. The launcher creates that private file from its example when missing and preserves an existing file. Review its content policy before using real conversations. `CLI_ARGS` forwards normal CLI options; `HARNESS_UI_ENV` selects another private environment file.

The separate Harness developer workflow uses `dev/harness/.env` and `make harness-dev HARNESS_ARGS=summary`. That launcher initializes its provider with `opentelemetry-instrument`; Harness UI's launcher uses the App's automatic setup.

## Deployment environment

Set `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local` to label new observations as `local`. The development template already includes this value. Merge it with other resource attributes in an existing private environment file, then restart the App. Exported shell values take precedence over that file.

This label describes the deployment, independently of execution Environment profiles and mount names. Existing traces keep their labels. An external provider's Host must set the deployment resource on that provider.

## Read the execution from the root down

1. Open `harness_ui.root`, or `harness_ui.subagent` for a child segment. Check the operation status and Thread/Run IDs. The application span includes preparation and continuation saving as well as execution.
2. Expand `configuration` to check the captured Agent, Model, Capabilities, integrations, and Environment. This bounded summary omits prompts, credentials, endpoints, and filesystem roots.
3. Follow `harness.run` and its model/tool spans for timings, recovery, usage, and cost. Request-level model fields show changes made by a Model resolver.
4. For delegated work, filter by `root_thread_id`. Use `parent_thread_id`, `subagent_role`, `execution_id`, and `segment_index` to identify a child segment. A resumed child keeps its Thread/session ID and adds `resumed_from_execution_id`, but starts another trace.

A child's dispatch Link connects valid live trace context. Durable execution lineage can outlive that context, so a resumed child need not link to a previous process's trace. Normal cancellation remains cancellation rather than an error.

### Host phases and Skill inspection

| Span or field                                           | Use it to answer                                                       |
| ------------------------------------------------------- | ---------------------------------------------------------------------- |
| `harness_ui.prepare`                                    | Did configuration, state loading, and Environment preparation succeed? |
| `harness_ui.finalize`                                   | Did Environment finalization and continuation saving succeed?          |
| `a13n.phase.step`                                       | Which preparation or finalization step failed?                         |
| `a13n.ui.continuation.status`                           | Was a continuation selected for the next Run?                          |
| Skill summaries on the operation root and `harness.run` | Which Skills were available and read in this Thread?                   |

If an answer is visible but the application operation failed, inspect finalization before retrying the task. The Harness Run can succeed while saving fails. A Skill read records access; it does not establish that the Agent followed the instructions.

## Content and limitations

Choose the content policy before exporting real conversations:

| `A13N_HARNESS_TRACE_CONTENT` | Captured content                                                                       |
| ---------------------------- | -------------------------------------------------------------------------------------- |
| `none`                       | Structural diagnostics; omits normal execution bodies                                  |
| `standard`                   | Current submission and final output on operation roots, plus native model/tool content |
| `full`                       | Standard content plus upstream binary and request-parameter capture                    |

Operation-root input and output are each capped at 8 KiB. Inspect `a13n.input.capture`, `a13n.output.capture`, and truncation flags for missing or partial values. Roots describe native media without copying binaries, full history, credentials, raw exceptions, or checkpoint bodies. Upstream model/tool spans have their own information boundary: `none` is not a complete scrubber for exceptions, Agent descriptions, metadata, or tool schemas. Review [Harness tracing](../a13n-harness/observation.md) when choosing an export policy.

At shutdown, the App drains its own exporter after active work and collaborators close, off the event loop and within its waiting budget. Export is best effort. Use the saved Thread state—not a trace or its presence in a backend—to resume work.
