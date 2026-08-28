# Agent Harness Observation Demo

This executable Host writes a deterministic Agent Harness Observation matrix to the repository's local Langfuse v4 stack. It covers four causal paths:

- `summary`: `HandoffCapability` invokes the explicit `summarize` tool, restores the continuation, and completes.
- `compaction`: `CompactionCapability` observes provider-reported usage above its threshold, produces a compact summary with tools disabled, replaces history, and completes.
- `view`: the managed `view` tool reads a synthetic PNG and invokes the dedicated `image-understanding` Pydantic Agent to return plain-text transcription. The resulting provider usage includes cache, audio, and reasoning counters.
- `subagent`: the parent calls the managed `delegate` tool, which contains one child `harness.run`, child Agent attempt, and child generation with explicit identity and delegation lineage.

All scenarios use deterministic Pydantic AI models and synthetic content. No model-provider credential is required.

## Profiles

The matrix runs two distinct trace profiles:

| Profile   | Harness policy                      | Expected structure                                                                                  |
| --------- | ----------------------------------- | --------------------------------------------------------------------------------------------------- |
| `summary` | `summary` trace, `none` content     | One root `harness.run` for each scenario. Pydantic and Harness-operation spans are omitted.         |
| `verbose` | `verbose` trace, `standard` content | Summary structure plus Pydantic Agent/generation/tool spans and material `harness.operation` spans. |

Each root run receives a bounded `HarnessObservationContext` with its trace name, one profile-specific product session, labels, and scalar metadata. A small allowlisted `SpanProcessor` maps those vendor-neutral root fields to documented Langfuse trace fields and copies them to descendants, string-encoding non-string metadata only for Langfuse's flattened metadata attributes. It also marks Harness/Pydantic Agent and tool observations with Langfuse types, and adds only `logfire.msg`/`logfire.tags` display hints that Logfire recognizes. It does not propagate arbitrary identity claims, Host references, or `RunBindings.metadata`, and it creates no separate Host root span.

Harness-owned spans remain content-safe. Pydantic model and tool input/output appears only in the verbose profile because that profile explicitly selects standard content capture.

## Run

From the repository root:

```bash
make langfuse-up
dev/observation-demo/run.sh all --profile matrix
```

Run one scenario in one profile:

```bash
dev/observation-demo/run.sh summary --profile summary
dev/observation-demo/run.sh compaction --profile verbose
dev/observation-demo/run.sh view --profile verbose
dev/observation-demo/run.sh subagent --profile verbose
```

Run all scenarios in one profile:

```bash
dev/observation-demo/run.sh all --profile summary
dev/observation-demo/run.sh all --profile verbose
```

Each scenario prints its profile, trace ID, final output, context-event sequence, usage-record count, and direct local Langfuse URL.

## Expected evidence

In verbose generation observations, Pydantic owns native input/output, cache, audio, and reasoning usage. The Harness custom cost Capability enriches the same generation span with:

- `gen_ai.usage.cost`;
- `a13n.usage.cost.source`;
- `a13n.usage.pricing.status`;
- `a13n.usage.pricing.revision`;
- `a13n.usage.pricing.rule.id`.

Langfuse maps the numeric cost into `costDetails.total` and `totalCost`. It maps native Pydantic counters into `usageDetails`, and Pydantic suppresses direct `details.input_tokens` / `details.output_tokens` synonyms.

Pydantic cache, audio, and reasoning categories are inclusive sub-buckets. Langfuse v4 currently treats arbitrary audio/reasoning detail counters as additive when deriving `usageDetails.total`, so that displayed total can exceed Pydantic `input_tokens + output_tokens`. The demo intentionally preserves those fields to expose the backend behavior; do not treat the Langfuse-derived total as Harness accounting truth.

`harness.run` and Pydantic Agent-attempt observations carry the bounded identity projection: issuer, subject, conventional `agent_id` and `user_id`, instance, parent instance, delegation, and actor when present. The synthetic `evaluation_cohort` claim and `host_refs.request_id` are deliberately present in the run bindings and must not appear in telemetry.

The `view` trace should contain:

```text
harness.run -> Agent -> view tool -> image-understanding Agent -> vision generation
```

The `subagent` trace should contain:

```text
parent harness.run -> parent Agent -> delegate tool -> child harness.run -> child Agent -> child generation
```

## Host boundary

The launcher loads the repository root `.env` through `uv run --env-file`; Harness itself continues to read only process environment variables and never opens `.env`. The launcher clears inherited model and telemetry selectors first, checks the local Langfuse readiness endpoint, and assigns `service.name=agent-foundation-observation-demo`.

The profile is supplied explicitly as `HarnessInstrumentation`, so root `.env` Harness detail settings cannot silently change the requested matrix. Summary and verbose traces use separate Langfuse sessions (`observation-summary-2026-08` and `observation-verbose-2026-08`) so the scenario traces are easy to compare. The root `.env` still owns the standard OpenTelemetry OTLP exporter endpoint, authentication header, transport, and batch settings.

Do not reuse verbose standard-content capture with sensitive application data without reviewing the telemetry trust boundary.
