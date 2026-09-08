# Agent Harness Observation Demo

This executable Host writes deterministic, complete Agent Harness traces to the repository's local Langfuse v4 stack. It covers four causal paths:

- `summary`: `HandoffCapability` invokes the explicit `summarize` tool, restores the continuation, and completes.
- `compaction`: `CompactionCapability` observes provider-reported usage above its threshold, produces a compact summary with tools disabled, replaces history, and completes.
- `view`: the managed `view` tool reads a synthetic PNG and invokes the dedicated `image-understanding` Pydantic Agent to return plain-text transcription. The resulting provider usage includes cache, audio, and reasoning counters.
- `subagent`: the parent calls the managed `delegate` tool, which contains one child `harness.run`, child Agent attempt, and child generation with inherited identity and Harness-generated instance/delegation lineage.

All scenarios use deterministic Pydantic AI models and synthetic content. No model-provider credential is required.

## Trace policy

Every scenario explicitly supplies one Host-owned tracer provider through `HarnessInstrumentation`. A selected tracer provider always enables the complete structure: one root `harness.run`, Pydantic Agent/model/tool descendants, and material `harness.operation` spans. The demo uses the default `standard` content policy. There is no summary or intermediate trace profile.

Each root run receives a bounded `HarnessObservationContext` with its trace name, one product session, labels, and scalar metadata. A small allowlisted `SpanProcessor` maps those vendor-neutral root fields to documented Langfuse trace fields and copies them to descendants, string-encoding non-string metadata only for Langfuse's flattened metadata attributes. It also marks Harness/Pydantic Agent and tool observations with Langfuse types, and adds only `logfire.msg`/`logfire.tags` display hints that Logfire recognizes. It does not propagate arbitrary identity claims, Host references, or `RunBindings.metadata`, and it creates no separate Host root span.

Harness-owned spans remain content-safe. Pydantic model and tool input/output follows the selected `standard` content policy.

## Run

From the repository root:

```bash
make langfuse-up
dev/observation-demo/run.sh all
```

Run one scenario:

```bash
dev/observation-demo/run.sh summary
dev/observation-demo/run.sh compaction
dev/observation-demo/run.sh view
dev/observation-demo/run.sh subagent
```

Each scenario prints its trace ID, final output, context-event sequence, usage-record count, and direct local Langfuse URL.

## Offline regression check

Run all four scenarios with an in-memory OpenTelemetry exporter, without `.env`, model credentials, or a running Langfuse stack:

```bash
make test PYTHON_TEST_DIRS=packages/a13n-harness/tests/test_observation_demo.py PYTHON_TEST_WORKERS=0
```

The smoke tests assert successful outputs, context events, a single connected trace per scenario, nested operation/Agent/model paths, inline child lineage, and native usage/custom cost attributes. The Harness Linux CI suite runs these tests, including when only this demo changes. This verifies SDK-exported spans, not Langfuse ingestion or UI rendering.

## Expected evidence

In generation observations, Pydantic owns native input/output, cache, audio, and reasoning usage. The Harness custom cost Capability enriches the same generation span with:

- `gen_ai.usage.cost`;
- `a13n.usage.cost.source`;
- `a13n.usage.pricing.status`;
- `a13n.usage.pricing.revision`;
- `a13n.usage.pricing.rule.id`.

Langfuse maps the numeric cost into `costDetails.total` and `totalCost`. It maps native Pydantic counters into `usageDetails`, and Pydantic suppresses direct `details.input_tokens` / `details.output_tokens` synonyms.

Pydantic cache, audio, and reasoning categories are inclusive sub-buckets. Langfuse v4 currently treats arbitrary audio/reasoning detail counters as additive when deriving `usageDetails.total`, so that displayed total can exceed Pydantic `input_tokens + output_tokens`. The demo intentionally preserves those fields to expose the backend behavior; do not treat the Langfuse-derived total as Harness accounting truth.

`harness.run` and Pydantic Agent-attempt observations carry the bounded identity projection: issuer, subject, conventional `agent_id` and `user_id`, instance, parent instance, delegation, and actor when present. The synthetic `evaluation_cohort` claim is deliberately present in the run bindings and must not appear in telemetry.

The `view` trace should contain:

```text
harness.run -> Agent -> view tool -> image-understanding Agent -> vision generation
```

The `subagent` trace should contain:

```text
parent harness.run -> parent Agent -> delegate tool -> delegation operation -> child harness.run -> child Agent -> child generation
```

The view scenario constructs a Direct Local `Environment` through `DirectLocalEnvironmentProvider` and passes it through the run's `environments` mapping. The subagent scenario uses the built-in inline `SubagentCapability()` and the `delegate` tool's `prompt` argument; no custom child-binding manager is required.

## Host boundary

The launcher loads the repository root `.env` through `uv run --env-file`; Harness itself continues to read only process environment variables and never opens `.env`. The launcher clears inherited model and telemetry selectors first, checks the local Langfuse readiness endpoint, and assigns `service.name=agent-foundation-observation-demo`.

The tracer provider is supplied explicitly through `HarnessInstrumentation`, so root `.env` Harness signal settings cannot silently disable the requested trace. All scenarios use the `observation-demo-2026-08` Langfuse session. The root `.env` still owns the standard OpenTelemetry OTLP exporter endpoint, authentication header, transport, and batch settings.

Do not reuse standard-content capture with sensitive application data without reviewing the telemetry trust boundary.
