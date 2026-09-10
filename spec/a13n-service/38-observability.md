# Observability

## Design Position

a13n Service projects each bounded worker generation through one generic OpenTelemetry trace. One `RunAttempt` owns one parentless `a13n.service.run_attempt` root span; the existing `harness.run` span and its Pydantic AI descendants execute beneath that root through the same current OpenTelemetry context. A Thread Trace is a query view over all of a Thread's RunAttempt traces, not another durable resource and not one unbounded OpenTelemetry trace.

Service owns the process tracer provider, resource, sampling, processors, scope filter, exporter lifecycle, correlation projection, and bounded shutdown. It exports through at most one OTLP destination. Langfuse is one optional backend reached through that generic OTLP path; Service installs no Langfuse SDK, vendor tracing profile, or Langfuse persistence dependency.

Telemetry storage, retention, and archival belong to the deployment operator and selected backend. Service defines no trace database, archive schema, archive reader, or rehydration path. The separate [Trace Query](39-trace-query.md) contract defines an authorized, provider-neutral read API over a selected backend without changing that ownership. Telemetry is a best-effort diagnostic projection and is never lifecycle, state, audit, usage, billing, authorization, or delivery authority. Missing telemetry proves none of those facts.

## Boundaries

| Concern                                         | Owner                                                          | Contract                                                                           |
| ----------------------------------------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Session, Thread, Run, and RunAttempt lifecycle  | Owning Service domains                                         | Supplies authoritative identities and outcomes; telemetry only correlates them     |
| Service tracer provider and OTLP export         | This contract                                                  | Constructs one process stack and one destination at the executable boundary        |
| Harness and Pydantic AI observations            | [Harness Observation](../a13n-harness/19-observation-model.md) | Retains its existing span ownership and content semantics beneath the Service root |
| Inbound W3C Trace Context trust                 | [HTTP Ingress](05-http-ingress-and-request-contract.md)        | Validates transport context without granting product authority                     |
| RunAttempt root and Service phase spans         | This contract                                                  | Covers the claimed worker generation before, during, and after Harness execution   |
| Authorized Trace Query API and backend adapters | [Trace Query](39-trace-query.md)                               | Normalizes selected backend reads without owning stored telemetry                  |
| Telemetry storage, retention, and archival      | Deployment operator and selected backend                       | Remain outside Service and never become durable lifecycle authority                |
| OTLP fan-out and tail sampling                  | External OpenTelemetry Collector                               | Remain outside the Service process                                                 |

This contract owns tracing. Harness metrics retain their independent provider and instrument ownership. A trace setting neither enables nor disables an otherwise selected meter provider.

## Thread Trace and Vendor Mapping

The product and telemetry hierarchy is:

```text
Service Session
  -> Thread                         Thread Trace query unit
    -> Run
      -> RunAttempt                one OpenTelemetry Trace
        -> a13n.service.run_attempt  Service root span
          -> harness.run
            -> Pydantic Agent/model/tool spans
```

One Thread can span processes, deployments, waits, and many independently sampled Attempts. Service therefore never keeps a trace open for the life of a Thread. A Run with two worker generations has two RunAttempt traces even when both traces correspond to one user input. A Thread Trace query groups them by `thread_id`, then by `run_id`, and preserves each `run_attempt_id` and outcome separately.

Service projects the following generic grouping on every approved span in a RunAttempt trace:

| Attribute                     | Value                | Meaning                                                             |
| ----------------------------- | -------------------- | ------------------------------------------------------------------- |
| `session.id`                  | `thread_id`          | Cross-trace observability session and primary Thread Trace grouping |
| `a13n.thread.id`              | `thread_id`          | Stable Agent Foundation Thread correlation                          |
| `a13n.observation.session.id` | Service `session_id` | Higher product grouping that can contain root and child Threads     |

For Langfuse's OTLP ingestion this produces the explicit mapping:

```text
Langfuse Session      = Service Thread
Langfuse Trace        = Service RunAttempt
Langfuse Observation  = Service, Harness, or Pydantic AI span/event
```

An asynchronous child owns another Thread and therefore another Langfuse Session. Its origin remains visible through bounded lineage attributes and a best-effort span link; the parent Thread's observability session does not absorb the child's traces. Inline Harness children retain their ordinary causal parentage inside the active RunAttempt trace.

Service's `Session` and Langfuse's Session intentionally have different meanings. Neither mapping changes durable identities, Pydantic conversation correlation, provider-native session state, or authorization.

## Producer and Export Configuration

Service's OTel producer policy has exactly two common fields:

```toml
[observability]
tracing = true
trace_content = "none" # none | standard | full
```

They map to `A13N_SERVICE_OBSERVABILITY_TRACING` and `A13N_SERVICE_OBSERVABILITY_TRACE_CONTENT` under the common runtime precedence. Release defaults are `tracing=true` and `trace_content="none"`. Development deployments can explicitly select another content value; there is no implicit development profile, Workspace override, per-Run override, default/ceiling pair, or configuration hot reload. The content value is dormant while tracing is disabled and is not recorded as a span attribute. Trace query selection and credentials are an independent control-plane concern owned by [Trace Query](39-trace-query.md#configuration-and-provider-selection); selecting or disabling a query provider never changes this producer or exporter configuration.

Endpoint, protocol, headers, TLS, compression, sampler, batching, queue, timeout, retry, and resource overrides use only standard `OTEL_*` settings. Service defines no `profile`, `backend`, `langfuse_enabled`, or parallel `A13N_SERVICE_*` transport settings. OTLP authentication values remain protected deployment configuration and never enter effective-configuration output, diagnostics, or traces.

Runtime behavior is:

| Configuration                                       | Provider behavior                                                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `tracing=false`                                     | Construct no real tracer provider, processor, or trace exporter; pass disabled instrumentation to Harness    |
| `tracing=true`, exporter unset or `none`            | Construct the structural instrumentation boundary with no network exporter and emit one safe startup warning |
| `tracing=true`, exporter `otlp`                     | Construct one batch processor and one OTLP exporter from standard OTel settings                              |
| Unsupported exporter or malformed static OTel value | Fail startup before serving work                                                                             |
| Export endpoint unavailable after startup           | Keep readiness and Agent work independent; bounded retry, drop, and safe diagnostics apply                   |

The Service uses the OTel `always_on` sampler unless standard `OTEL_TRACES_SAMPLER` and `OTEL_TRACES_SAMPLER_ARG` select another head sampler. One root decision applies to the complete RunAttempt subtree. Result-aware tail sampling belongs to an external Collector.

The runtime owns bounded flush and shutdown once per process. It never flushes synchronously after each Harness Run. Flush timeout reports a safe diagnostic and does not extend process shutdown without bound.

## Resource and Correlation Registry

The OpenTelemetry Resource carries process facts rather than repeating them on every span:

| Attribute                     | Contract                                                |
| ----------------------------- | ------------------------------------------------------- |
| `service.name`                | Stable a13n Service executable identity                 |
| `service.version`             | Running distribution version                            |
| `deployment.environment.name` | Bounded deployment environment selected by the operator |
| `service.instance.id`         | Optional bounded process-instance correlation           |
| `a13n.service.role`           | `control`, `worker`, or `all`                           |

The Service-owned processor projects only validated values from this closed correlation registry onto approved RunAttempt spans:

| Attribute                          | Placement and meaning                                                         |
| ---------------------------------- | ----------------------------------------------------------------------------- |
| `a13n.organization.id`             | Owning Organization correlation                                               |
| `a13n.workspace.id`                | Owning Workspace correlation                                                  |
| `a13n.observation.session.id`      | Service product Session                                                       |
| `session.id`                       | Service Thread used as the cross-trace observability session                  |
| `a13n.thread.id`                   | Service Thread                                                                |
| `a13n.service.run.id`              | Accepted Service Run                                                          |
| `a13n.run_attempt.id`              | Current worker generation                                                     |
| `a13n.run_attempt.number`          | Positive generation number within the Run                                     |
| `a13n.run_attempt.replaces.id`     | Immediately replaced Attempt, when present                                    |
| `a13n.run_attempt.recovery.reason` | `lease_expired`, `retry_after_failure`, `planned_handoff`, or `pending_input` |
| `a13n.agent.agent.id`              | Selected Agent identity                                                       |
| `a13n.agent.agent.revision.id`     | Exact selected immutable AgentRevision                                        |
| `a13n.model.id`                    | Exact selected Service Model identity                                         |
| `a13n.model.provider.type`         | Bounded selected provider type                                                |

The root additionally owns `a13n.run_attempt.outcome` with `succeeded`, `yielded`, `failed`, or `cancelled` after a matching authoritative Attempt decision, plus an optional bounded `a13n.run_attempt.failure.code`. An unfinished span or missing outcome does not invent a durable status. Harness retains `a13n.run.id` for its process-local Harness Run; Service never overwrites or reinterprets that field as its durable Run identity. Harness and Pydantic AI retain their other existing attributes and remain the sole owners of Harness Run, model, tool, streaming, native usage, and native exception fields.

First-party search annotates its existing tool-execution span with `a13n.search.provider.id` and `a13n.search.provider.type`, using the exact selected Search Provider identity and bounded catalog type from [Search Provider Management](41-search-provider-management.md). These attributes describe only that invocation, not every node in the Run; they are absent from unrelated spans. They add no duplicate tool or provider span and contain no account name, endpoint, credential, or query. Outcome, duration, and available usage retain their existing tool and provider observation owners.

Correlation values are never authorization evidence. The processor does not flatten arbitrary identity claims, request metadata, Agent metadata, headers, provider state, or `RunBindings.metadata`. A value that fails the owning ID or bounded scalar contract is omitted and diagnosed by safe category rather than truncated into a different identity.

## RunAttempt Trace Lifecycle

### Root boundary

The newly scheduled `RunAttemptExecutor` starts `a13n.service.run_attempt` immediately after the durable claim or takeover transaction commits the new `leased` RunAttempt. It starts a new trace with no parent even when an inbound or dispatch context remains available. The root stays current through preparation, Harness entry and cleanup, state publication, and the final Attempt/Run decision. It ends after that decision commits or after the local executor proves it can no longer publish authoritatively.

If the process terminates abruptly, the root may remain incomplete in a backend. A replacement Worker does not finish, rewrite, or synthesize the old span. Its newly claimed Attempt starts another trace. The authoritative old Attempt becomes `failed` only through the existing takeover transaction.

The first Attempt has no recovery reason. A replacement caused by expired lease uses `lease_expired`; a replacement after a known retryable Attempt failure uses `retry_after_failure`; a successor to a gracefully yielded Attempt uses `planned_handoff`; and continuation for accepted pending input uses `pending_input`. These values project the stored start reason; they are not inferred from trace timing. Telemetry never substitutes `worker_lost`, because lease expiry cannot distinguish a crash from partition, suspension, or an unresponsive process.

### Service phase spans

Service owns exactly these stable direct children of the root when the corresponding phase starts:

| Span                               | Boundary                                                                                                                                                                                                                                                                                   |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `a13n.service.reconstruct`         | Reads and validates complete Run state, exact AgentRevision, `EffectiveAgentConfig`, Run-pinned immutable Skill artifacts, frozen plugin configuration, current authority, fresh credentials, and process-local Agent values; ends when reconstruction required for Harness entry is ready |
| `a13n.service.environment.prepare` | Verifies fixed logical Environment selection, resolves Provider-owned credentials, coordinates use and creates/resumes/rebuilds or connects the target; runs before Harness for on_run or on first actual operation for on_use; records safe generation changes and failures               |
| `a13n.service.persist`             | Publishes the Harness outcome's complete state and result objects and performs the short fenced Attempt/Run decision; ends after commit or classified failure                                                                                                                              |

These spans provide phase duration and local execution outcome through `a13n.service.phase.outcome` (`succeeded`, `failed`, or `cancelled`). An escaping failure adds `error.type` (the exception class name, limited to 256 characters) and OTel ERROR status, never the exception message or stack. Cancellation is not an ERROR. A successful phase means the operation returned normally, not that the Run succeeded: publishing a failed Attempt is a successful persistence operation. The root owns the authoritative Attempt outcome and its stored safe failure code.

Reconstruction covers dependency preflight through a ready invocation, including Environment adapter binding, and ends before Harness entry. Eager Environment preparation can overlap reconstruction; phase durations are not additive. Lazy preparation and target recovery create an Environment span only when actual preparation starts. That span records `a13n.environment.id` and, on success, `a13n.environment.generation`. A saved outcome can be persisted without Harness entry; if accepted pending input requires execution instead, reconstruction resumes as another span. Persistence spans cover final publication, recovery of saved outcomes, and failure decisions, not routine checkpoints, heartbeats, or inbox polling.

These spans do not duplicate database, object-store, HTTP, provider, or Environment spans and do not keep a database session or transaction open across their full duration. An operation that never starts creates no placeholder span. Phase observation failures do not replace the execution result.

The existing `harness.run` span starts under the current root and retains the complete lifecycle defined by Harness. Native Pydantic AI spans remain its descendants. Service does not wrap or duplicate Agent attempts, model requests, tool execution, streaming, cancellation, usage, or inline children. Harness completion and Service persistence can therefore have different outcomes: `harness.run` can complete while `a13n.service.persist` and the RunAttempt root fail.

### Links and propagation

HTTP ingress validates W3C Trace Context at the configured trust boundary. Service does not persist OTel context, live spans, vendor objects, or trace IDs in Session, Thread, Run, or RunAttempt records. A string correlation attribute never substitutes for an OTel link.

A RunAttempt root can contain best-effort links to valid contexts still available in process for:

- the request that accepted the Run;
- a dispatch or scheduling boundary;
- the immediately replaced Attempt;
- authenticated feedback acceptance; or
- asynchronous child acceptance.

Each link carries at most one bounded `a13n.link.kind` from `run_acceptance`, `dispatch`, `lease_expired`, `retry_after_failure`, `planned_handoff`, `feedback`, or `async_child`. Missing context omits the link without affecting domain correlation. All directly causal work inside one Attempt uses normal parent/child context.

## Content Policy and Information Boundary

Service maps its deployment-level content value directly to `HarnessTraceContent`:

| Content    | Ordinary prompt/output/tool payload | Dedicated binary content | Model request parameters |
| ---------- | ----------------------------------: | -----------------------: | -----------------------: |
| `none`     |                                  No |                       No |                       No |
| `standard` |                                 Yes |                       No |                       No |
| `full`     |                                 Yes |                      Yes |                      Yes |

All three values preserve the same span topology, correlation, timing, outcome, retry/cancellation classification, and approved usage/cost fields. `none` answers which step ran, when, for how long, with what safe outcome and cost; it does not opt into normal execution payloads.

The mapping carries the exact upstream limitations defined by Harness. Pydantic AI can still emit Agent descriptions, tool definitions and schema defaults, Agent/run metadata, exception messages, and stack traces at `none`. `standard` and `full` can contain raw business content. `full` is the highest-exposure diagnostic choice and can export multimodal bytes and complete request parameters. Service provides no recursive sanitizer, payload classification, or guarantee that upstream content is secret-free.

The root projects accepted input and confirmed final Run output without copying the complete execution transcript:

| Root attribute     | Value                                                                                                     |
| ------------------ | --------------------------------------------------------------------------------------------------------- |
| `input.value`      | Accepted user input serialized as ordinary text or JSON when content is `standard` or `full`              |
| `input.mime_type`  | `text/plain` or `application/json` when `input.value` is present                                          |
| `output.value`     | Final user-visible Run output committed by this Attempt, as ordinary text or JSON at `standard` or `full` |
| `output.mime_type` | `text/plain` or `application/json` when `output.value` is present                                         |

At `none` all four fields are absent. At `standard` and `full`, inline accepted input is available from root creation; external input is projected after ordinary execution materializes its accepted payload. This is the accepted Run input, including feedback or continuation envelopes, not the adapted model prompt or reconstructed history. Text, JSON structures, and explicit JSON null retain their values.

Output is projected only after a durable read confirms both a completed Run and a succeeded Attempt, with that Attempt recorded as the sealed state's committer. A succeeded Attempt that leaves the Run waiting or continuing has no final output. A failed, cancelled, yielded, or superseded Attempt never copies another Attempt's result or synthesizes an error string as output. Inline output comes from the committed Run. External output reuses the payload materialized by the ordinary pre-commit integrity verification, including saved-outcome recovery, only when its immutable object digest matches the committed reference. Verification or publication alone does not expose root output. If no matching local payload is available at final projection, the body is omitted rather than fetched solely for tracing. Read the durable Run output for the authoritative result.

The root records `a13n.run_attempt.input.capture` and, after its durable outcome is observed, `a13n.run_attempt.output.capture` with these closed values:

| Value              | Meaning                                                                        |
| ------------------ | ------------------------------------------------------------------------------ |
| `captured`         | The corresponding value and MIME type are projected                            |
| `content_disabled` | Deployment content policy is `none`                                            |
| `external_payload` | The payload is external and no matching materialized body is available locally |
| `unavailable`      | No input value is available at this observation boundary (input only)          |
| `not_committed`    | This Attempt has no confirmed final Run output (output only)                   |

These indicators contain no payload, and an external input indicator changes to `captured` when ordinary preparation reads the payload. Missing telemetry or a projection failure remains non-authoritative. Service performs no additional object-store reads for these fields, and retains at most one external output candidate in the local recording Attempt until outcome projection or root end; `none` and non-recording roots retain none.

These fields do not copy system prompts, intermediate model messages, tool arguments or results, complete conversation history, Harness state, binary bytes, file contents, or usage records. `full` expands upstream Pydantic capture but does not make the Service root another dump of those descendants.

Except for this explicit root input/output boundary, Service-owned Resource, span, event, and link fields use only the closed registries in this contract. They never contain credentials, Secret values, Authorization, cookies, environment variables, raw headers or bodies, raw exception objects, native paths, arbitrary metadata, provider response bodies, or copied prompt/model/tool content. This producer constraint does not make allowed root or upstream Pydantic content safe. A deployment requiring stronger controls places a tested processor or Collector policy before data crosses its trust boundary.

Service adds no trace-specific byte truncation. Oversized upstream spans can be rejected by an exporter or backend and are then lost as telemetry without changing Agent or Run behavior.

## Scope Allowlist and Capacity

Before batching or export, the distribution-fixed scope filter admits only:

- the a13n Service scope `a13n-a13n-service`;
- the Harness scope `a13n-harness`; and
- the locked Pydantic AI scope `pydantic-ai` at a version compatible with the distribution lock.

FastAPI, HTTPX, SQLAlchemy, Psycopg, Redis, S3, plugin, custom, and unknown instrumentation scopes are dropped as complete spans. Runtime configuration cannot expand the allowlist. Adding a scope changes the data-export boundary and requires a compatibility and information review together with allow/drop tests.

The initial provider limits are the selected OTel SDK defaults:

| Limit                        |    Default |
| ---------------------------- | ---------: |
| Span attributes              |        128 |
| Span events                  |        128 |
| Span links                   |        128 |
| Attributes per event or link |        128 |
| Batch processor queue        |       2048 |
| Export batch                 |        512 |
| Schedule delay               |  5 seconds |
| Export timeout               | 30 seconds |

Standard OTel settings can override these values. Attribute, event, and link overflow uses the OTel `dropped_*_count` fields. Queue capacity and queue size use the selected SDK processor's native metrics. An exporter batch failure reports only a safe batch result because Service cannot prove which spans a remote backend accepted. Drop diagnostics never recursively create traces.

## Export Topology and Backend Retention

Service has one in-process trace exporter and at most one destination:

```text
Service -> Langfuse or another OTLP backend
Service -> external OTel Collector -> operator-selected destinations
Service -> no exporter
```

An external Collector can fan out the Service's sole OTLP stream. The operator owns its destinations, persistent queues, retry, capacity, and failure isolation. Service never adds another in-process exporter or repeats instrumentation for downstream delivery.

Langfuse stores the telemetry it successfully ingests and can provide its own Trace, Session, usage, latency, error, score, and evaluation views. Its deployment, ClickHouse schema, projects, users, access controls, and retention remain outside Service. The operator configures Langfuse ingestion through standard OTLP endpoint and header settings. Service does not provision projects, distribute project keys, embed Langfuse UI, or map Service Principal or RoleBinding values to Langfuse users. When configured, the independent Trace Query boundary calls a documented backend API with deployment-owned read credentials and applies Service authorization to its normalized results.

After Langfuse or another selected backend deletes an expired trace, Service cannot query or restore it. Any long-term export, archive, or data-lake integration is configured and operated outside Service.

## Security and Failure Semantics

Every selected OTLP destination receives exactly the content admitted to the Service export stream. Service does not claim `standard` or `full` data is sanitized, and a downstream system cannot regain fields omitted at `none`. Scope and content policy form the producer-side information boundary; downstream storage, transport, access control, encryption, retention, and deletion remain deployment responsibilities.

| Failure                                             | Result                                                                                     |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Invalid Service observability configuration         | Service startup fails with a safe configuration category                                   |
| Tracing enabled without exporter                    | Service starts in no-export mode and warns once                                            |
| Exporter unavailable, queue full, or span oversized | Affected telemetry can be retried or dropped; Agent, Run, and readiness remain independent |
| Worker exits before ending its root span            | Backend can show an incomplete span; takeover creates another Attempt and trace            |
| Invalid inbound trace context                       | Ignore the context and continue with a fresh local trace                                   |
| Backend retention expires                           | Trace disappears from that backend; Service does not restore or rehydrate it               |

## Compatibility and Verification

Stable span names, attribute meanings, root input/output semantics, scope allowlist, content mappings, configuration fields, and Langfuse grouping are compatibility contracts. Adding an exported scope, widening content, changing an identity mapping, or making an optional backend operationally required is an information-boundary change, not a private refactor.

Verification covers:

- disabled, no-export, OTLP, invalid-static-config, endpoint-failure, queue pressure, flush timeout, and throwing processor/exporter paths;
- one parentless root per RunAttempt, fixed Service phase spans, unchanged Harness/Pydantic ownership, replacement Attempts, and incomplete old roots;
- exact Session/Thread/Run/Attempt correlation and independent child Threads;
- each content value, root input/output presence, and the admitted/rejected scope matrix;
- Service-owned credential/header/body/metadata/exception exclusions;
- direct backend export and external Collector fan-out without a second Service exporter; and
- proof that every telemetry failure leaves lifecycle, state, delivery, and usage authority unchanged.

## Trade-offs

One trace per RunAttempt makes retries and worker replacement honest, bounded, and independently sampleable, at the cost of joining several traces for one Thread view. A generic OTLP path preserves backend choice. Service accepts a provider-specific read adapter at the control boundary so clients receive one API, while storage, retention, archival, and backend operation remain deployment concerns.

## Invariants

01. A Thread Trace is a query view over bounded RunAttempt traces and is not a durable resource or one long-lived OTel trace.
02. Every traced RunAttempt starts one parentless `a13n.service.run_attempt` root after durable claim.
03. Existing `harness.run` and Pydantic AI spans retain one owner and become descendants through current OTel context.
04. Durable and asynchronous predecessors use links when context remains available; Service persists no OTel context or live span object.
05. `session.id` identifies the Service Thread, while `a13n.observation.session.id` identifies the broader Service Session.
06. Sampling, content, exporter selection, backend selection, and backend retention are independent controls.
07. Production defaults to tracing enabled, `trace_content=none`, and `always_on` sampling; transport uses only standard OTel settings.
08. Service exports through at most one OTLP destination and installs no vendor SDK or backend-specific export switch.
09. Only distribution-approved Service, Harness, and locked Pydantic AI scopes cross the exporter boundary.
10. Service-owned telemetry never generates known credentials, authorization material, raw transport bodies, arbitrary metadata, or raw exception objects.
11. `standard` and `full` follow upstream Pydantic content semantics and carry no universal sanitization guarantee.
12. Telemetry failure, absence, or presence never changes or proves lifecycle, state, usage, audit, billing, authorization, or delivery facts.
13. Service defines no trace storage, archive schema, archive reader, or rehydration path; the deployment operator owns those facilities through the selected backend or external pipeline.
