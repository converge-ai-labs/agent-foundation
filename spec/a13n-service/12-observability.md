# Observability: logs, metrics and traces

This chapter owns what the Service records about its own operation: which signal answers which question, the fields and events it logs, the metrics it serves, and how exported traces correlate with runs. Trace backends are [08](08-providers.md#trace-backends)'s, trace queries [07](07-facts-and-delivery.md#trace-query)'s, and the Harness's own instruments [the Harness observation model](../a13n-harness/19-observation-model.md)'s.

## Signals

Each signal answers one kind of question:

| Question                                                         | Signal                                                       |
| ---------------------------------------------------------------- | ------------------------------------------------------------ |
| What happened to this request or run?                            | [logs](#logs), searched by `request_id` or `run_id`          |
| Is the Service healthy, backed up or failing?                    | [metrics](#metrics), which alerts are written against        |
| What did the agent do inside an attempt?                         | the attempt's [trace](#traces) in the trace backend          |
| How much did a tenant use, and what did its runs do over a week? | PostgreSQL facts: `runs`, `run_attempts` and `usage_records` |

PostgreSQL facts are authoritative for every per-tenant and per-object number. Metrics carry global rates and distributions only, so no metric is labelled with a tenant, principal, run or other object. Four boundaries record logs and metrics: an HTTP request, an attempt, a sweep pass and an outbox delivery. A fact such as an accepted or sealed run is logged and counted only after the transaction that records it commits, so a rollback leaves no record.

Logs and metrics never carry credentials, request or response bodies, URLs or query strings; paths and query strings can carry identifiers and authorization codes. Service failure handlers record `exception_details`: bounded exception types, stack locations (file, line and function), cause and group relationships, and numeric HTTP status or OS error codes when available. Exception messages, notes, source lines and locals are excluded, including for defects ([09](09-runtime.md#http-ingress-and-escaping-failures)). Expected refusals can log their stable reason alone.

## Logs

The executable configures logging once, for the `a13n_service` and `a13n_harness` loggers, at `telemetry.log_level`. Its outputs:

- stdout, in the `telemetry.log_format` format (`json` or `pretty`), unless `telemetry.log_stdout` is false;
- a file at `telemetry.log_file`, always JSON, rotated when it reaches `telemetry.log_file_max_mb` with `telemetry.log_file_backups` rotated files kept. `log_stdout = false` requires a file.

Only `run` writes the file; other commands log to stdout, so no command rotates a file under a running server. Processes must not share one file path. The HTTP access log is off; `Request finished` replaces it.

A boundary binds fields that every record logged within it carries, including records of the Harness and of tasks started inside it. A field the call passes itself wins over a bound field of the same name.

| Boundary        | Bound fields           |
| --------------- | ---------------------- |
| HTTP request    | `request_id`           |
| worker loop     | `worker_id`            |
| attempt         | `run_id`, `attempt_id` |
| sweep pass      | `sweep`                |
| outbox delivery | `outbox_id`, `kind`    |

`request_id` is the `X-Request-Id` the response carries ([10](10-api.md)). The Service logs these events besides failures:

| Event                                                                             | Level                  | Fields                                                                                       |
| --------------------------------------------------------------------------------- | ---------------------- | -------------------------------------------------------------------------------------------- |
| `Request finished`                                                                | INFO                   | `method`, `route` (the route template, or `unmatched`), `status`, `duration_ms`              |
| `Run accepted`                                                                    | INFO                   | `run_id`, `thread_id`, `trigger`                                                             |
| `Attempt claimed`                                                                 | INFO                   | `run_id`, `attempt_id`, `queue_wait_ms`                                                      |
| `Attempt ended`                                                                   | INFO                   | `run_id`, `attempt_id`, `status`, `reason` (the failure code or yield reason), `duration_ms` |
| `Run sealed`                                                                      | INFO                   | `run_id`, `status`, `reason` (the failure code)                                              |
| `Outbox delivered`, `Outbox delivery failed` (will retry), `Outbox delivery dead` | INFO, WARNING, WARNING | `outbox_id`, `kind`, `reason` (the delivery error)                                           |
| `Sweep failed`                                                                    | WARNING                | `sweep`, `error_type`, `exception_details`                                                   |

`/healthz` and `/readyz` are not logged. A run accepted while handling a request is logged within it, so its `Run accepted` record carries both the `request_id` and the `run_id`: logs lead from a request to the runs it started without a mapping table.

## Metrics

When `telemetry.metrics_port` is set, every process serves its metrics at `/metrics` on `server.host` and that port, in the Prometheus text format; unset, metrics are off. The port must differ from `server.port`, so the API never serves metrics and an ingress routes only the API. Prometheus and VictoriaMetrics scrape it alike. The process's resource attributes, `service.name = a13n-service` and the package version, appear once as `target_info`.

One process-wide meter provider records the Service's instruments and, on executing roles, the Harness's and Pydantic AI's. Names follow OpenTelemetry and become Prometheus names on export: dots become underscores, a duration gains `_seconds` and a counter `_total`, so `a13n.runs.sealed` is scraped as `a13n_runs_sealed_total`. Durations are in seconds, the base unit of both. Every histogram declares its buckets, sized for what it measures.

| Instrument                     | Kind      | Labels                                                           | Records                                                                                |
| ------------------------------ | --------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `http.server.request.duration` | histogram | `http.request.method`, `http.route`, `http.response.status_code` | each HTTP request until its response ends, streams included, 5 ms to 10 s              |
| `a13n.runs.accepted`           | counter   | `trigger`                                                        | each accepted run                                                                      |
| `a13n.runs.sealed`             | counter   | `status`, and `reason` when the run failed or was cancelled      | each sealed run                                                                        |
| `a13n.attempt.queue_wait`      | histogram | —                                                                | how long a due run waited before its claim, 0.1 s to 10 min                            |
| `a13n.attempt.duration`        | histogram | `status`                                                         | each attempt from its claim to its end, 1 s to 1 h                                     |
| `a13n.worker.slots`            | gauge     | `state`: `free`, `busy`                                          | this worker's attempt slots                                                            |
| `a13n.backlog.size`            | gauge     | `queue`: `runs`, or an outbox kind                               | due work waiting to be claimed, up to max(10,000, the kind's configured backlog count) |
| `a13n.backlog.oldest_age`      | gauge     | `queue`                                                          | how long the oldest due item has waited; 0 when none waits                             |
| `a13n.outbox.deliveries`       | counter   | `kind`, `result`: `delivered`, `retry`, `deferred`, `dead`       | each settled delivery attempt                                                          |
| `a13n.sweep.passes`            | counter   | `sweep`, `result`: `succeeded`, `failed`                         | each sweep pass; a pass that exceeded its deadline failed                              |

Every label value comes from a set fixed in code: a failure `reason` is one of the codes the Service and the Harness define ([05](05-runs.md#execute)), and `http.route` is a route template, never a path.

Due work is an accepted run whose `available_at` has passed, or an unleased pending outbox row whose `available_at` has passed; a waiting run or work scheduled for later is not backlog. The `report_backlog` sweep ([09](09-runtime.md#sweeps)) refreshes the backlog gauges every 15 seconds with one bounded query per queue, so a scrape never queries a database. Every `all` and `control` replica reports the same values; a query takes their maximum. A gauge keeps its last value until the next refresh replaces it.

Outbox policies also set `backlog_count` (10,000), `backlog_age_seconds` (300) and `backlog_alert_seconds` (300). A count at the threshold or an oldest due age at the threshold starts a per-kind timer. Remaining above either threshold for the configured duration sets `a13n.outbox.backlog_alert` to 1 and logs `Outbox backlog sustained`; recovery clears it and logs `Outbox backlog recovered`. Timers reset on control process restart. `a13n.outbox.dead` is 1 while a kind has any retained dead rows; the transition logs `Outbox has dead deliveries`. Both gauges are labeled by `kind`; use the maximum across replicas. Logs contain identifiers and counts, never delivery payloads. Operators route these signals through their monitoring system; the Service does not send external notifications.

## Traces

Executing roles export Harness spans over OTLP/HTTP to the configured trace backend in background batches, with the resource attributes `service.name = a13n-service` and the package version, so a slow or failing backend never delays execution; each export call and the final flush are bounded by 10 seconds. `telemetry.trace_content` decides whether prompts, outputs and tool payloads leave the deployment.

Every exported span of an attempt, its inline child runs included, carries the attempt's correlation as Harness observation metadata:

| Attribute                                   | Value                  |
| ------------------------------------------- | ---------------------- |
| `a13n.observation.metadata.organization_id` | the run's organization |
| `a13n.observation.metadata.workspace_id`    | the run's workspace    |
| `a13n.observation.metadata.session_id`      | the run's session      |
| `a13n.observation.metadata.service_run_id`  | the Service run        |
| `a13n.observation.metadata.run_attempt_id`  | the attempt            |
| `a13n.observation.session.id`               | the thread             |

The thread is the observation session, by which a backend such as Langfuse groups a conversation; the Harness keeps its own `run_id` and `thread_id` metadata keys, hence `service_run_id`. One function owns these names, and trace queries select by the same attributes ([07](07-facts-and-delivery.md#trace-query)). The backend is both the export target and the query source ([08](08-providers.md#trace-backends)).

The Service adds no spans of its own. HTTP, database and delivery failures are located by request logs and metrics, since the Service is one process over one database, and a Service root span around an attempt would take the trace name, session and tags the Harness root span owns. The `run_id` and `attempt_id` of the attempt's log records are the `service_run_id` and `run_attempt_id` of its trace.

## Invariants

- Logs and metrics never carry credentials, bodies, URLs or query strings.
- A fact is logged and counted only after its transaction commits.
- Every metric label has a value set fixed in code; no metric names a tenant or an object.
- A scrape reads only the process's memory, never PostgreSQL or Redis.
- A request's records lead to the runs it accepted, and a run's records to its attempts' traces, through IDs the records carry.
