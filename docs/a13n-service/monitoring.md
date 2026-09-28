# Monitor and troubleshoot

The Service records three signals, and each answers one kind of question:

| Question                                             | Where to look                                                          |
| ---------------------------------------------------- | ---------------------------------------------------------------------- |
| What happened to this request or run?                | [Logs](#logs), searched by `request_id` or `run_id`                    |
| Is the Service healthy, backed up or failing?        | [Metrics](#metrics), with the alert rules and the operations dashboard |
| What did the agent do inside an attempt?             | The attempt's [trace](agents-and-runs.md#traces)                       |
| How much did a tenant use, and what did its runs do? | The [usage](#usage) facts in PostgreSQL                                |

Logs and metrics never contain credentials, request or response bodies, URLs or query strings. Metrics never name a tenant or an object: per-tenant numbers come from PostgreSQL.

## Logs

Every process logs to stdout by default, as JSON or, with `telemetry.log_format = "pretty"`, as readable text. `a13n-service run` can also write a JSON file that it rotates by size:

```toml
[telemetry]
log_level = "INFO"
log_file = "/var/log/a13n/service.log"
log_file_max_mb = 100   # rotate when the file reaches this size
log_file_backups = 5    # rotated files kept: service.log.1 … service.log.5
log_stdout = false      # only the file
```

Each process needs a file of its own. Operator commands such as `migrate` always log to stdout.

Records carry the IDs of the work they belong to, whichever code logged them:

| While handling        | Records carry          |
| --------------------- | ---------------------- |
| an HTTP request       | `request_id`           |
| a worker's claim loop | `worker_id`            |
| an attempt            | `run_id`, `attempt_id` |
| a sweep pass          | `sweep`                |
| a webhook or email    | `outbox_id`, `kind`    |

Besides failures, the Service logs these events:

| Event                                                                | Fields                                                                                 |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `Request finished`                                                   | `method`, `route` (the route template), `status`, `duration_ms`; probes are not logged |
| `Run accepted`                                                       | `run_id`, `thread_id`, `trigger`                                                       |
| `Attempt claimed`                                                    | `run_id`, `attempt_id`, `queue_wait_ms`                                                |
| `Attempt ended`                                                      | `run_id`, `attempt_id`, `status`, `reason`, `duration_ms`                              |
| `Run sealed`                                                         | `run_id`, `status`, `reason` (the failure code)                                        |
| `Outbox delivered`, `Outbox delivery failed`, `Outbox delivery dead` | `outbox_id`, `kind`, `reason`                                                          |
| `Sweep failed`                                                       | `sweep`, `error_type`                                                                  |

Configure proxies in front of the Service to redact query strings from their own access logs as well.

## Metrics

Set `telemetry.metrics_port`, and every process serves Prometheus metrics at `/metrics` on `server.host` and that port. Prometheus and VictoriaMetrics scrape it alike. Keep the port private: it is separate from `server.port` so that nothing routes it with the API. The Helm chart serves it on port 9464 by default.

```toml
[telemetry]
metrics_port = 9464
```

| Metric                                 | Labels                                                           | Meaning                                       |
| -------------------------------------- | ---------------------------------------------------------------- | --------------------------------------------- |
| `http_server_request_duration_seconds` | `http_request_method`, `http_route`, `http_response_status_code` | request duration, streams included            |
| `a13n_runs_accepted_total`             | `trigger`                                                        | accepted runs                                 |
| `a13n_runs_sealed_total`               | `status`, `reason`                                               | sealed runs; `reason` is the failure code     |
| `a13n_attempt_queue_wait_seconds`      | —                                                                | how long a due run waited for a worker        |
| `a13n_attempt_duration_seconds`        | `status`                                                         | attempt duration from claim to end            |
| `a13n_worker_slots`                    | `state`: `free`, `busy`                                          | a worker's attempt slots                      |
| `a13n_backlog_size`                    | `queue`                                                          | due runs, or due deliveries of an outbox kind |
| `a13n_backlog_oldest_age_seconds`      | `queue`                                                          | how long the oldest due item has waited       |
| `a13n_outbox_deliveries_total`         | `kind`, `result`                                                 | webhook, email and other delivery outcomes    |
| `a13n_sweep_passes_total`              | `sweep`, `result`                                                | background sweep passes                       |

Workers also serve the Harness's metrics, such as `a13n_harness_run_duration_seconds`, and model token usage as `gen_ai_client_token_usage`; see [Harness observation](../a13n-harness/observation.md). Backlog counts come from a query that control replicas repeat every 15 seconds and are capped at 10,000; every control replica reports the same values, so take their maximum.

The [monitoring bundle](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/monitoring) has alert rules, an operations dashboard and a usage dashboard, with instructions for Prometheus, VictoriaMetrics, their Kubernetes operators and Grafana.

## Troubleshoot a request or run

Start from the `X-Request-Id` of the response, which error bodies repeat as `request_id`:

1. Search the logs for the `request_id`. `Request finished` gives the route and status. A request that started a run also logged `Run accepted` with the `run_id`; a refused request has none, and its status and error body say why. A message queued behind an active run starts its run later, so find that run in the thread's inbox.

2. Search the logs for the `run_id`. Where its records stop tells where the run is:

   | Last record of the run                     | The run                                          | Look next at                                                                                       |
   | ------------------------------------------ | ------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
   | `Run accepted`                             | waits for a worker                               | `a13n_backlog_oldest_age_seconds{queue="runs"}`, `a13n_worker_slots` and whether workers are ready |
   | `Attempt claimed`                          | is executing                                     | the attempt's trace                                                                                |
   | `Attempt ended` with `failed` or `yielded` | returned to the queue to be retried or continued | `reason`, then the next `Attempt claimed`                                                          |
   | `Run sealed`                               | has ended                                        | `status` and `reason`; `GET …/runs/{run_id}` carries the failure message                           |

3. Open the attempt's trace with `GET …/runs/{run_id}/attempts/{attempt_id}/trace`, or find it in Langfuse or Logfire by the `service_run_id` and `run_attempt_id` metadata.

4. For a webhook or email that never arrived, search for the `Outbox` records of its `kind`: a dead delivery names its `reason`.

## Usage

Per-tenant usage is recorded as facts in PostgreSQL, never in metrics. A workspace reads its own through the [usage API](agents-and-runs.md#usage). Operators can import the usage dashboard from the monitoring bundle, which reads runs and model usage per organization, workspace and model through a read-only database role.

The outbox also exposes `a13n_outbox_backlog_alert{kind}` for a sustained threshold breach and `a13n_outbox_dead{kind}` for retained dead deliveries. Use the maximum across control replicas. Both are 0/1 gauges updated every 15 seconds; the backlog timer resets when the process restarts. See [outbox retention and capacity](operations.md#outbox-retention-and-capacity) for policy defaults, overrides and response guidance.

### Workspace Usage in the Console

Open **Observe → Usage** to inspect the current workspace. Choose the last 7 or 30 days, or apply a custom range of up to 366 days. The overview shows model costs, tokens, cached input, cache rate, Run count, request count and average Run time. Switch the daily chart between Spend and Tokens, then compare Agents or models in the paginated breakdown.

Spend includes recorded model costs in USD. Requests without a price are counted separately: a partial subtotal is not the full cost, and entirely unknown costs display as unavailable. Cached input is already included in input tokens. Cache rate divides the summed cached input by summed input, rather than averaging request percentages.

Usage dates follow ingestion time; Run counts follow start time. Average Run time covers started Runs that have sealed, measured from start to seal, including intervening execution waits. A waiting Run seals before human approval; a resumed successor is a separate Run. Late usage can update an earlier day's totals. The chart follows your browser's displayed timezone.

These reads query stored facts directly and work without a trace backend. The HTTP endpoints are `GET /api/v1/usage/overview` (totals and daily buckets), `/api/v1/usage/agents` and `/api/v1/usage/models` (paged breakdowns). All take `start` and exclusive `end` RFC 3339 timestamps; overview also accepts an IANA `timezone`, and breakdowns accept `limit` and `cursor`. The existing `/api/v1/usage` continues to support per-Run, Thread and Session summaries.
