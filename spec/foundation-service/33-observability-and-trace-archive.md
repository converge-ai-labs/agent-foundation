# Observability and Trace Archive

## Design Position

Foundation Service projects each bounded worker generation through one generic
OpenTelemetry trace. One `TurnAttempt` owns one parentless
`foundation.turn_attempt` root span; the existing `harness.run` span and its
Pydantic AI descendants execute beneath that root through the same current
OpenTelemetry context. A Thread Trace is a query view over all of a Thread's
TurnAttempt traces, not another durable resource and not one unbounded
OpenTelemetry trace.

Foundation owns the process tracer provider, resource, sampling, processors,
scope filter, exporter lifecycle, correlation projection, and bounded shutdown.
It exports through at most one OTLP destination. Langfuse is one optional hot
query backend reached through that generic OTLP path; Foundation installs no
Langfuse SDK, vendor profile, backend switch, or Langfuse persistence
dependency.

The OSS distribution also supplies an independently deployed, optional Trace
Archive. When deployed, a continuously running Archive Sink receives approved
OTLP from an external OpenTelemetry Collector and writes immutable,
schema-versioned Parquet parts directly to object storage. It stores no Raw
OTLP copy and never reads Langfuse ClickHouse or another hot backend. Scheduled
archive maintenance compacts committed parts, validates manifests, and applies
retention; it is not the first durable capture path.

Hot telemetry and cold archive are best-effort diagnostic projections. Neither
is lifecycle, state, audit, usage, billing, authorization, or delivery
authority. Missing telemetry proves none of those facts.

## Boundaries

| Concern                                                       | Owner                                                           | Contract                                                                              |
| ------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Session, Thread, Turn, and TurnAttempt lifecycle              | Owning Foundation domains                                       | Supplies authoritative identities and outcomes; telemetry only correlates them        |
| Service tracer provider and OTLP export                       | This contract                                                   | Constructs one process stack and one destination at the executable boundary           |
| Harness and Pydantic AI observations                          | [Harness Observation](../agent-harness/19-observation-model.md) | Retains its existing span ownership and content semantics beneath the Service root    |
| Inbound W3C Trace Context trust                               | [HTTP Ingress](05-http-ingress-and-request-contract.md)         | Validates transport context without granting product authority                        |
| TurnAttempt root and Service phase spans                      | This contract                                                   | Covers the claimed worker generation before, during, and after Harness execution      |
| Hot backend retention and access                              | Deployment operator and selected backend                        | Does not change Foundation lifecycle or cold-retention policy                         |
| OTLP fan-out and tail sampling                                | External OpenTelemetry Collector                                | Remains outside the Service process and isolates each downstream queue                |
| Parquet archive, manifests, compaction, and archive retention | Trace Archive                                                   | Preserves only approved exported spans as a non-authoritative cold dataset            |
| Operator archive reads                                        | Trace Archive Reader                                            | Reads object storage directly with operator credentials and explicit partition bounds |

This contract owns tracing. Harness metrics retain their independent provider
and instrument ownership. A trace setting neither enables nor disables an
otherwise selected meter provider.

## Thread Trace and Vendor Mapping

The product and telemetry hierarchy is:

```text
Foundation Session
  -> Thread                         Thread Trace query unit
    -> Turn
      -> TurnAttempt                one OpenTelemetry Trace
        -> foundation.turn_attempt  Service root span
          -> harness.run
            -> Pydantic Agent/model/tool spans
```

One Thread can span processes, deployments, waits, and many independently
sampled Attempts. Foundation therefore never keeps a trace open for the life of
a Thread. A Turn with two worker generations has two TurnAttempt traces even
when both traces correspond to one user input. A Thread Trace query groups them
by `thread_id`, then by `turn_id`, and preserves each `turn_attempt_id` and
outcome separately.

Foundation projects the following generic grouping on every approved span in a
TurnAttempt trace:

| Attribute                     | Value                   | Meaning                                                             |
| ----------------------------- | ----------------------- | ------------------------------------------------------------------- |
| `session.id`                  | `thread_id`             | Cross-trace observability session and primary Thread Trace grouping |
| `a13n.thread.id`              | `thread_id`             | Stable Agent Foundation Thread correlation                          |
| `a13n.observation.session.id` | Foundation `session_id` | Higher product grouping that can contain root and child Threads     |

For Langfuse's OTLP ingestion this produces the explicit mapping:

```text
Langfuse Session      = Foundation Thread
Langfuse Trace        = Foundation TurnAttempt
Langfuse Observation  = Foundation, Harness, or Pydantic AI span/event
```

An asynchronous child owns another Thread and therefore another Langfuse
Session. Its origin remains visible through bounded lineage attributes and a
best-effort span link; the parent Thread's observability session does not absorb
the child's traces. Inline Harness children retain their ordinary causal
parentage inside the active TurnAttempt trace.

Foundation's `Session` and Langfuse's Session intentionally have different
meanings. Neither mapping changes durable identities, Pydantic conversation
correlation, provider-native session state, or authorization.

## Provider and Runtime Configuration

Foundation-owned configuration has exactly two fields:

```toml
[observability]
tracing = true
trace_content = "none" # none | standard | full
```

They map to `FOUNDATION_OBSERVABILITY_TRACING` and
`FOUNDATION_OBSERVABILITY_TRACE_CONTENT` under the common runtime precedence.
Release defaults are `tracing=true` and `trace_content="none"`. Development
deployments can explicitly select another content value; there is no implicit
development profile, Workspace override, per-Run override, default/ceiling
pair, or configuration hot reload. The content value is dormant while tracing
is disabled and is not recorded as a span attribute.

Endpoint, protocol, headers, TLS, compression, sampler, batching, queue,
timeout, retry, and resource overrides use only standard `OTEL_*` settings.
Foundation defines no `profile`, `backend`, `langfuse_enabled`, or parallel
`FOUNDATION_*` transport settings. OTLP authentication values remain protected
deployment configuration and never enter effective-configuration output,
diagnostics, traces, or manifests.

Runtime behavior is:

| Configuration                                       | Provider behavior                                                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `tracing=false`                                     | Construct no real tracer provider, processor, or trace exporter; pass disabled instrumentation to Harness    |
| `tracing=true`, exporter unset or `none`            | Construct the structural instrumentation boundary with no network exporter and emit one safe startup warning |
| `tracing=true`, exporter `otlp`                     | Construct one batch processor and one OTLP exporter from standard OTel settings                              |
| Unsupported exporter or malformed static OTel value | Fail startup before serving work                                                                             |
| Export endpoint unavailable after startup           | Keep readiness and Agent work independent; bounded retry, drop, and safe diagnostics apply                   |

The Service uses the OTel `always_on` sampler unless standard
`OTEL_TRACES_SAMPLER` and `OTEL_TRACES_SAMPLER_ARG` select another head sampler.
One root decision applies to the complete TurnAttempt subtree. Result-aware
tail sampling belongs to an external Collector.

The runtime owns bounded flush and shutdown once per process. It never flushes
synchronously after each Harness Run. Flush timeout reports a safe diagnostic
and does not extend process shutdown without bound.

## Resource and Correlation Registry

The OpenTelemetry Resource carries process facts rather than repeating them on
every span:

| Attribute                     | Contract                                                |
| ----------------------------- | ------------------------------------------------------- |
| `service.name`                | Stable Foundation Service executable identity           |
| `service.version`             | Running distribution version                            |
| `deployment.environment.name` | Bounded deployment environment selected by the operator |
| `service.instance.id`         | Optional bounded process-instance correlation           |
| `a13n.service.role`           | `control`, `worker`, or `all`                           |

The Foundation-owned processor projects only validated values from this closed
correlation registry onto approved TurnAttempt spans:

| Attribute                           | Placement and meaning                                           |
| ----------------------------------- | --------------------------------------------------------------- |
| `a13n.organization.id`              | Owning Organization correlation                                 |
| `a13n.workspace.id`                 | Owning Workspace correlation and archive isolation key          |
| `a13n.observation.session.id`       | Foundation product Session                                      |
| `session.id`                        | Foundation Thread used as the cross-trace observability session |
| `a13n.thread.id`                    | Foundation Thread                                               |
| `a13n.turn.id`                      | Accepted Turn                                                   |
| `a13n.turn_attempt.id`              | Current worker generation                                       |
| `a13n.turn_attempt.number`          | Positive generation number within the Turn                      |
| `a13n.turn_attempt.replaces.id`     | Immediately replaced Attempt, when present                      |
| `a13n.turn_attempt.recovery.reason` | `lease_expired` or `retry_after_failure`, when present          |
| `a13n.run.id`                       | Harness Run after its durable fenced binding                    |
| `a13n.agent.preset.id`              | Selected AgentPreset identity                                   |
| `a13n.agent.preset.version.id`      | Exact selected immutable AgentPresetVersion                     |
| `a13n.model.id`                     | Exact selected Foundation ModelConfig identity                  |
| `a13n.model.provider.type`          | Bounded selected provider type                                  |

The root additionally owns `a13n.turn_attempt.outcome` with `succeeded`,
`failed`, or `cancelled` after a matching authoritative Attempt decision, plus
an optional bounded `a13n.turn_attempt.failure.code`. An unfinished span or
missing outcome does not invent a durable status. Harness and Pydantic AI retain
their existing attributes and remain the sole owners of Run, model, tool,
streaming, native usage, and native exception fields.

Correlation values are never authorization evidence. The processor does not
flatten arbitrary identity claims, request metadata, Agent metadata, headers,
provider state, or `RunBindings.metadata`. A value that fails the owning ID or
bounded scalar contract is omitted and diagnosed by safe category rather than
truncated into a different identity.

## TurnAttempt Trace Lifecycle

### Root boundary

The Worker starts `foundation.turn_attempt` immediately after the durable claim
or takeover transaction commits the new `leased` TurnAttempt. It starts a new
trace with no parent even when an inbound or dispatch context remains
available. The root stays current through preparation, Harness entry and
cleanup, state publication, and the final Attempt/Turn decision. It ends after
that decision commits or after the local Worker proves it can no longer publish
authoritatively.

If the process terminates abruptly, the root may remain incomplete in a
backend. A replacement Worker does not finish, rewrite, or synthesize the old
span. Its newly claimed Attempt starts another trace. The authoritative old
Attempt becomes `failed` only through the existing takeover transaction.

The first Attempt has no recovery reason. A replacement caused by expired lease
uses `lease_expired`; a replacement after a known retryable Attempt failure
uses `retry_after_failure`. Telemetry never substitutes `worker_lost`, because
lease expiry cannot distinguish a crash from partition, suspension, or an
unresponsive process.

### Service phase spans

Foundation owns exactly these stable direct children of the root when the
corresponding phase starts:

| Span                            | Boundary                                                                                                                                                                                                                                                                               |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `foundation.reconstruct`        | Reads and validates complete Turn state, exact AgentPresetVersion, Turn-pinned Runtime lock, immutable Skill/plugin artifacts, frozen dependencies, current authority, fresh credentials, and process-local Agent values; ends when reconstruction required for Harness entry is ready |
| `foundation.environment.attach` | Selects and connects the exact state-owned Environment configuration; ends when fresh attachments, runtime mounts, and `EnvironmentRuntime` are usable or attachment fails                                                                                                             |
| `foundation.persist`            | Publishes the Harness outcome's complete state and result objects and performs the short fenced Attempt/Turn decision; ends after commit or classified failure                                                                                                                         |

These spans provide phase duration, outcome, and bounded failure class. They do
not duplicate database, object-store, HTTP, provider, or Environment spans and
do not keep a database session or transaction open across their full duration.
An operation that never starts creates no placeholder span.

The existing `harness.run` span starts under the current root and retains the
complete lifecycle defined by Harness. Native Pydantic AI spans remain its
descendants. Foundation does not wrap or duplicate Agent attempts, model
requests, tool execution, streaming, cancellation, usage, or inline children.
Harness completion and Foundation persistence can therefore have different
outcomes: `harness.run` can complete while `foundation.persist` and the
TurnAttempt root fail.

### Links and propagation

HTTP ingress validates W3C Trace Context at the configured trust boundary.
Foundation does not persist OTel context, live spans, vendor objects, or
trace IDs in Session, Thread, Turn, or TurnAttempt records. A string correlation
attribute never substitutes for an OTel link.

A TurnAttempt root can contain best-effort links to valid contexts still
available in process for:

- the request that accepted the Turn;
- a dispatch or scheduling boundary;
- the immediately replaced Attempt;
- authenticated feedback acceptance; or
- asynchronous child acceptance.

Each link carries at most one bounded `a13n.link.kind` from
`turn_acceptance`, `dispatch`, `lease_expired`, `retry_after_failure`,
`feedback`, or `async_child`. Missing context omits the link without affecting
domain correlation. All directly causal work inside one Attempt uses normal
parent/child context.

## Content Policy and Information Boundary

Foundation maps its deployment-level content value directly to
`HarnessTraceContent`:

| Content    | Ordinary prompt/output/tool payload | Dedicated binary content | Model request parameters |
| ---------- | ----------------------------------: | -----------------------: | -----------------------: |
| `none`     |                                  No |                       No |                       No |
| `standard` |                                 Yes |                       No |                       No |
| `full`     |                                 Yes |                      Yes |                      Yes |

All three values preserve the same span topology, correlation, timing, outcome,
retry/cancellation classification, and approved usage/cost fields. `none`
answers which step ran, when, for how long, with what safe outcome and cost; it
does not opt into normal execution payloads.

The mapping carries the exact upstream limitations defined by Harness.
Pydantic AI can still emit Agent descriptions, tool definitions and schema
defaults, Agent/run metadata, exception messages, and stack traces at
`none`. `standard` and `full` can contain raw business content. `full` is the
highest-exposure diagnostic choice and can export multimodal bytes and complete
request parameters. Foundation provides no recursive sanitizer, payload
classification, or guarantee that upstream content is secret-free.

Foundation-owned Resource, span, event, and link fields use only the closed
registries in this contract. They never contain credentials, Secret values,
Authorization, cookies, environment variables, raw headers or bodies, raw
exception objects, native paths, arbitrary metadata, provider response bodies,
or copied prompt/model/tool content. This producer constraint does not make
allowed upstream Pydantic fields safe. A deployment requiring stronger controls
places a tested processor or Collector policy before data crosses its trust
boundary.

Foundation adds no trace-specific byte truncation. Oversized upstream spans can
be rejected by an exporter or backend and are then lost as telemetry without
changing Agent or Turn behavior.

## Scope Allowlist and Capacity

Before batching or export, the distribution-fixed scope filter admits only:

- the Foundation Service scope `a13n-foundation-service`;
- the Harness scope `a13n-harness`; and
- the locked Pydantic AI scope `pydantic-ai` at a version compatible with the
  distribution lock.

FastAPI, HTTPX, SQLAlchemy, Psycopg, Redis, S3, plugin, custom, and unknown
instrumentation scopes are dropped as complete spans. Runtime configuration
cannot expand the allowlist. Adding a scope changes the data-export boundary
and requires a compatibility and information review together with allow/drop
tests.

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

Standard OTel settings can override these values. Attribute, event, and link
overflow uses the OTel `dropped_*_count` fields. Queue capacity and queue size
use the selected SDK processor's native metrics. An exporter batch failure
reports only a safe batch result because Foundation cannot prove which spans a
remote backend accepted. Drop diagnostics never recursively create traces.

## Export Topologies and Hot Query

Foundation has one in-process trace exporter and one destination:

```text
archive disabled: Service -> Langfuse or another OTLP backend
archive disabled: Service -> no exporter
archive enabled:  Service -> OTel Collector -> hot backend
                                         \-> Trace Archive Sink
```

Archive-enabled deployments send the Service's sole OTLP stream to an external
Collector. The Collector owns independent persistent queues, retry, capacity,
and failure isolation for its hot and archive exporters. Foundation never adds
a second in-process exporter or repeats instrumentation for archive delivery.

Langfuse stores the telemetry it successfully ingests and provides its own hot
Trace, Session, usage, latency, error, score, and evaluation views. Its
deployment, ClickHouse schema, projects, users, access controls, and retention
remain outside Foundation. The operator configures Langfuse through standard
OTLP endpoint and header settings and accesses Langfuse directly; Foundation
does not provision projects, distribute project keys, proxy queries, embed its
UI, or map Foundation Principal/RoleBinding values to Langfuse users.

Hot retention and cold retention are independent. After Langfuse deletes an
expired trace, it is no longer searchable in the Langfuse UI. Presence in
Parquet does not rehydrate that trace or extend Langfuse visibility.

## Trace Archive Architecture

Trace Archive is inactive unless its separate components are deployed. Its
deployment does not add Service roles, make Collector or object storage a
Foundation Service readiness dependency, or change ordinary Service startup.
The OSS archive consists of:

- a continuously running Archive Sink that receives OTLP trace export;
- a scheduled Maintainer that compacts, validates, and applies retention; and
- an operator Python Reader and CLI over the committed object dataset.

The Sink accepts only ended spans that already passed the Service scope and
content boundary and that contain a valid environment, Workspace, trace ID,
and span ID. It revalidates the distribution scope allowlist and the complete
archive request before publishing any part. A request containing a malformed,
unapproved, or unpartitionable span receives a permanent bounded OTLP
rejection and commits no manifest. The Sink cannot recover content omitted at
collection, admit a rejected scope, or read higher-detail data from another
store. Archive rejection does not affect the independent hot exporter.

The Sink converts OTLP directly into immutable Parquet parts. It seals a part
after a bounded byte/row threshold or bounded short time window and uses Zstandard
Parquet compression. Exact buffering thresholds are operational settings with
release-bounded defaults; they do not change row meaning or permit unbounded
memory. A single valid large row can occupy its own part.

## `trace-archive-v1` Dataset

### Physical layout

The dataset has one `spans` table:

```text
schema=trace-archive-v1/
table=spans/
environment=<environment>/
workspace=<workspace-hash>/
event_date=YYYY-MM-DD/
part-<ingest-id>-<ordinal>.parquet
```

`event_date` is the UTC date of `start_time_unix_nano`. `environment` must match
`[a-z0-9][a-z0-9._-]{0,62}` and is stored literally. `workspace-hash` is the
lowercase 64-character hexadecimal SHA-256 digest of the exact UTF-8
`a13n.workspace.id`. The raw Workspace ID remains inside the Parquet row so a
Reader can verify the hash and reject a mismatch.

There is no hour, trace, or Thread bucket. Compacted files sort rows by
`thread_id`, `trace_id`, `start_time_unix_nano`, `span_id`, and
`content_sha256`. Reader pruning uses Environment, Workspace hash, date
partitions, and Parquet statistics.

### Span row

One ended OTLP span produces one logical row. Parquet uses required fields
unless the table marks a field optional:

| Field                      | Logical type                 | Meaning                                                                  |
| -------------------------- | ---------------------------- | ------------------------------------------------------------------------ |
| `schema_version`           | UTF-8 constant               | `trace-archive-v1`                                                       |
| `environment`              | UTF-8                        | Validated deployment environment                                         |
| `organization_id`          | optional UTF-8               | Foundation Organization correlation                                      |
| `workspace_id`             | UTF-8                        | Exact validated Workspace correlation                                    |
| `session_id`               | optional UTF-8               | Foundation product Session                                               |
| `thread_id`                | optional UTF-8               | Foundation Thread and observability session                              |
| `turn_id`                  | optional UTF-8               | Foundation Turn                                                          |
| `turn_attempt_id`          | optional UTF-8               | Foundation TurnAttempt                                                   |
| `harness_run_id`           | optional UTF-8               | Harness Run when bound                                                   |
| `trace_id`                 | fixed 16-byte binary         | OTel trace identity                                                      |
| `span_id`                  | fixed 8-byte binary          | OTel span identity                                                       |
| `parent_span_id`           | optional fixed 8-byte binary | Empty parent becomes null                                                |
| `trace_state`              | optional UTF-8               | Valid OTel trace state                                                   |
| `flags`                    | unsigned 32-bit integer      | OTLP span flags                                                          |
| `name`                     | UTF-8                        | Span name                                                                |
| `kind`                     | UTF-8 enum                   | `unspecified`, `internal`, `server`, `client`, `producer`, or `consumer` |
| `start_time_unix_nano`     | unsigned 64-bit integer      | OTel start instant                                                       |
| `end_time_unix_nano`       | unsigned 64-bit integer      | OTel end instant                                                         |
| `status_code`              | UTF-8 enum                   | `unset`, `ok`, or `error`                                                |
| `status_message`           | optional UTF-8               | Upstream status description when present                                 |
| `resource_schema_url`      | optional UTF-8               | OTLP Resource schema URL                                                 |
| `resource_attributes`      | list of `ArchiveAttribute`   | Sorted Resource attributes                                               |
| `scope_name`               | UTF-8                        | Instrumentation scope name                                               |
| `scope_version`            | optional UTF-8               | Instrumentation scope version                                            |
| `scope_schema_url`         | optional UTF-8               | Instrumentation scope schema URL                                         |
| `scope_attributes`         | list of `ArchiveAttribute`   | Sorted scope attributes                                                  |
| `span_attributes`          | list of `ArchiveAttribute`   | Sorted span attributes                                                   |
| `events`                   | list of `ArchiveEvent`       | Events in source order                                                   |
| `links`                    | list of `ArchiveLink`        | Links in source order                                                    |
| `dropped_attributes_count` | unsigned 32-bit integer      | OTLP dropped count                                                       |
| `dropped_events_count`     | unsigned 32-bit integer      | OTLP dropped count                                                       |
| `dropped_links_count`      | unsigned 32-bit integer      | OTLP dropped count                                                       |
| `content_sha256`           | fixed 32-byte binary         | Deterministic logical-row content hash                                   |

`ArchiveAttribute` is a Parquet struct with `key`, `value_type`, one nullable
scalar slot for `string`, `boolean`, signed `int64`, `float64`, or `binary`, and
one nullable homogeneous list slot for each scalar type. Exactly one value slot
matches `value_type`. Keys are unique within an attribute list and sorted by
UTF-8 byte order. OTLP `array_value` and byte values retain their types rather
than becoming display strings. OTLP map/list shapes outside the admitted OTel
attribute contract are rejected instead of being lossy-flattened.

`ArchiveEvent` contains `time_unix_nano`, `name`, sorted attributes, and
`dropped_attributes_count`. `ArchiveLink` contains `trace_id`, `span_id`,
optional `trace_state`, `flags`, sorted attributes, and
`dropped_attributes_count`.

`content_sha256` is SHA-256 over deterministic CBOR encoding of every logical
row field except `content_sha256`. Map-like attribute collections are encoded
in their required key order; event and link order is preserved; binary values
remain bytes. This hash compares logical content across different Parquet files
and compression layouts.

### Immutable manifests

Each partition contains immutable canonical-JSON manifests at:

```text
schema=trace-archive-v1/
table=spans/
environment=<environment>/
workspace=<workspace-hash>/
event_date=YYYY-MM-DD/
_manifests/manifest-<created-unix-nano>-<manifest-id>.json
```

A `trace-archive-manifest-v1` record contains:

```python
class ArchivePartRef:
    object_key: str
    object_sha256: str
    byte_size: int
    row_count: int
    min_start_time_unix_nano: int
    max_start_time_unix_nano: int


class ArchiveConflict:
    workspace_id: str
    trace_id: str
    span_id: str
    content_sha256: tuple[str, ...]
    source_parts: tuple[str, ...]


class TraceArchiveManifest:
    schema_version: Literal["trace-archive-manifest-v1"]
    manifest_id: str
    operation: Literal["ingest", "compaction", "retention"]
    environment: str
    workspace_hash: str
    event_date: date
    created_at: datetime
    added_parts: tuple[ArchivePartRef, ...]
    removed_parts: tuple[str, ...]
    source_manifests: tuple[str, ...]
    conflicts: tuple[ArchiveConflict, ...]
```

Part and manifest publication is create-only. An ingest manifest lists newly
committed parts. A compaction manifest atomically adds its already-persisted
outputs and removes exact input keys. A retention manifest removes exact active
parts before physical deletion. Reader state is the union of all added part
keys minus every exact removed key; this set operation is independent of
manifest listing order. Reapplying a manifest or maintenance pass is
idempotent. Manifests remain available for integrity diagnosis until Workspace
purge.

An OTLP request is acknowledged only after every required Parquet part and
every matching ingest manifest is durably published. If only a subset publishes,
the Sink returns failure. Unmanifested parts are invisible orphans and are
eligible for cleanup only after a grace interval longer than the maximum Sink
publish and acknowledgement deadline and after two consecutive maintenance
listings still find no referencing manifest. Already manifested rows can be
delivered again when the Collector retries. This deliberately provides
at-least-once capture without a global hot-path deduplication index.

## Compaction, Duplicates, and Late Data

The Maintainer reads only committed parts. It groups a logical span by
`(workspace_id, trace_id, span_id)`:

- rows with the same identity and `content_sha256` collapse to one row;
- distinct hashes for the same identity remain as distinct variants;
- the compaction manifest records every conflicting hash and source part; and
- no implementation silently selects one conflicting variant as truth.

The Reader returns conflict diagnostics with raw variants and cannot present a
conflicted identity as one proven span. Trace Archive is not an immutable-span
authority and cannot repair an upstream conflict.

A span arriving after an earlier compaction writes a new immutable delta part
and ingest manifest. The next read unions it with active compacted output; a
later compaction applies the same identity and hash rules. Compaction is never
required for first durability and never delays Sink acknowledgement.

Collector delivery to the Archive uses a persistent queue and retry policy
independent from the hot exporter. Queue exhaustion, damaged queue storage,
upstream sampling/drop, or unavailable object storage can still create a gap.
Manifests prove which Archive objects committed; they do not prove that every
Agent execution or exported span reached the Archive.

## Archive Retention and Deletion

Archive is off unless its separate components are deployed. An enabled Archive
must select either a positive integer number of retention days or the literal
`forever`; missing, zero, negative, or unknown retention fails Archive startup.
There is no implicit finite or permanent retention default.

For finite retention, the scheduled Maintainer expires a partition from its
UTC `event_date`. It publishes a retention manifest for all active parts before
deleting their objects and reports any failed deletion or manifest mismatch.
An object-store lifecycle rule is optional defense in depth. If it deletes a
part before the Archive contract does, the Reader reports a missing committed
part integrity error.

The only explicit bulk deletion is an authorized whole-Workspace purge. Purge
first blocks new Collector delivery for that Workspace and writes a terminal
purge marker outside the data prefix. The Sink checks that marker immediately
before every manifest publication and rejects later data for that environment
and Workspace hash. Purge then waits beyond the maximum bounded Sink publish
deadline and repeatedly deletes every partition and manifest until two
consecutive listings are empty. This closes the in-flight publication race and
prevents a delayed Collector retry from recreating purged history. The marker
contains no raw Workspace ID or content.

Archive exposes no independent Thread Trace hard delete, legal hold, or
per-span mutation. Foundation's Thread contract exposes no independent Thread
hard delete, and archive projection cannot create a stronger lifecycle. A
Workspace purge or retention action changes no PostgreSQL row, state object,
Item, lifecycle event, UsageRecord, audit record, or Harness state.

## Archive Reader

The initial read surface is an operator Python Reader SDK and CLI. It uses the
operator's object-storage identity directly and is not a Foundation product API
or Langfuse proxy. Every query requires:

- exact Environment;
- exact Workspace ID, from which the Reader derives and verifies the hash; and
- a non-empty UTC start/end range.

Optional filters are `trace_id`, `thread_id`, `turn_id`, and
`turn_attempt_id`. The Reader returns either logical span rows or a reconstructed
parent/child tree grouped by trace ID. It also returns the manifest set,
unavailable or missing parts, orphan references, duplicate counts, conflict
variants, and ranges whose completeness cannot be proved.

There is no resident query service, regular Workspace-user API, Web UI,
Foundation-to-Langfuse rehydration, or promise that archived data remains in
the original Langfuse interface. Object possession and a Workspace ID do not
grant product authority; Reader deployment and object credentials are operator
security responsibilities.

## Security and Failure Semantics

Archive receives exactly the content admitted to the Service OTLP stream. It
does not claim `standard` or `full` data is sanitized, and it cannot regain
fields omitted at `none`. Scope and content policy therefore form one security
boundary for hot and cold destinations.

Foundation and Trace Archive define no Parquet application encryption, object
SSE mode, KMS key ID, or key-rotation setting. Transport encryption and
at-rest object encryption are deployment and storage-infrastructure concerns.
Object credentials and key material never enter traces, Parquet rows,
manifests, ordinary logs, or error responses.

| Failure                                             | Result                                                                                                         |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Invalid Service observability configuration         | Service startup fails with a safe configuration category                                                       |
| Tracing enabled without exporter                    | Service starts in no-export mode and warns once                                                                |
| Exporter unavailable, queue full, or span oversized | Affected telemetry can be retried or dropped; Agent, Turn, and readiness remain independent                    |
| Worker exits before ending its root span            | Backend can show an incomplete span; takeover creates another Attempt and trace                                |
| Invalid inbound trace context                       | Ignore the context and continue with a fresh local trace                                                       |
| Archive request partially persists                  | Return failure; Collector retries; orphan cleanup and compaction handle physical duplicates                    |
| Archive queue or object storage fails               | Hot exporter and Agent work continue; archive gap remains diagnosable but not repairable from authority claims |
| Committed part is missing or corrupt                | Reader reports integrity failure and does not fabricate rows                                                   |
| Same span identity has different content            | Preserve variants and conflict evidence; never silently overwrite                                              |
| Hot retention expires                               | Trace disappears from the hot backend even when a Parquet copy remains                                         |

## Compatibility and Verification

Stable span names, attribute meanings, scope allowlist, content mappings,
configuration fields, Langfuse grouping, Parquet row schema, partition layout,
hash algorithms, and manifest semantics are compatibility contracts. Adding an
exported scope, widening content, changing an identity mapping, or making an
optional backend operationally required is an information-boundary change, not
a private refactor.

Verification covers:

- disabled, no-export, OTLP, invalid-static-config, endpoint-failure, queue
  pressure, flush timeout, and throwing processor/exporter paths;
- one parentless root per TurnAttempt, fixed Service phase spans, unchanged
  Harness/Pydantic ownership, replacement Attempts, and incomplete old roots;
- exact Session/Thread/Turn/Attempt correlation and independent child Threads;
- each content value and the admitted/rejected scope matrix;
- Service-owned credential/header/body/metadata/exception exclusions;
- direct hot export and Collector fan-out without a second Service exporter;
- Parquet type and hash round trips, partition pruning, manifests, partial
  writes, retry duplicates, late spans, deterministic compaction, conflicts,
  retention, Workspace purge, and missing-part diagnostics; and
- proof that every telemetry and archive failure leaves lifecycle, state,
  delivery, and usage authority unchanged.

## Trade-offs

One trace per TurnAttempt makes retries and worker replacement honest, bounded,
and independently sampleable, at the cost of joining several traces for one
Thread view. A generic OTLP path preserves backend choice but leaves vendor
deployment and access outside Foundation. Direct Parquet capture avoids both a
Raw OTLP layer and a hot-backend database dependency, while at-least-once parts
and manifest-based compaction accept temporary duplicates and explicit
conflicts instead of a global ingestion index.

## Invariants

01. A Thread Trace is a query view over bounded TurnAttempt traces and is not a durable resource or one long-lived OTel trace.
02. Every traced TurnAttempt starts one parentless `foundation.turn_attempt` root after durable claim.
03. Existing `harness.run` and Pydantic AI spans retain one owner and become descendants through current OTel context.
04. Durable and asynchronous predecessors use links when context remains available; Foundation persists no OTel context or live span object.
05. `session.id` identifies the Foundation Thread, while `a13n.observation.session.id` identifies the broader Foundation Session.
06. Sampling, content, hot retention, cold retention, and backend selection are independent controls.
07. Production defaults to tracing enabled, `trace_content=none`, and `always_on` sampling; transport uses only standard OTel settings.
08. Foundation exports through at most one OTLP destination and installs no vendor SDK or backend-specific switch.
09. Only distribution-approved Foundation, Harness, and locked Pydantic AI scopes cross the exporter boundary.
10. Foundation-owned telemetry never generates known credentials, authorization material, raw transport bodies, arbitrary metadata, or raw exception objects.
11. `standard` and `full` follow upstream Pydantic content semantics and carry no universal sanitization guarantee.
12. Telemetry failure, absence, or presence never changes or proves lifecycle, state, usage, audit, billing, authorization, or delivery facts.
13. Trace Archive is an optional independent OSS component and never makes Collector, Langfuse, or object storage a required Service dependency.
14. Archive writes approved OTLP directly to immutable versioned Parquet parts and stores no Raw OTLP copy.
15. Only manifested parts are readable; at-least-once delivery can duplicate rows, and deterministic compaction never silently overwrites conflicting span content.
16. Archive retention and whole-Workspace purge change only the cold projection and never mutate Foundation authority.
17. Archived data is operator-readable through bounded object queries and does not remain or reappear in Langfuse after hot deletion.
18. A manifest proves committed Archive objects, not completeness of Agent execution or telemetry delivery.
