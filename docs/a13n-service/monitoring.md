---
title: Monitor and troubleshoot
description: Export Prometheus metrics and OpenTelemetry traces from Service, correlate HTTP request IDs with worker runs, and query workspace usage and spend.
---

| Question                                 | Where to look                                                   |
| ---------------------------------------- | --------------------------------------------------------------- |
| Why did an HTTP request or run fail?     | [Troubleshoot a request or run](#troubleshoot-a-request-or-run) |
| Are workers or queues falling behind?    | [Prometheus metrics](#metrics)                                  |
| What happened inside an agent attempt?   | Attempt [traces](inspect-runs.md#traces)                        |
| How many tokens or dollars were spent?   | [Workspace usage](#usage)                                       |
| Is a sandbox short on CPU, RAM, or disk? | [Environment monitoring](environment-monitoring.md)             |

## Troubleshoot a request or run

1. Copy `X-Request-Id` (`error.request_id`) from the response and search `control` logs for `request_id` (`Request finished`, `Run accepted`).
2. Follow `run_id` across `control` and `worker` logs (`Run accepted` → `Attempt claimed` → `Attempt ended` → `Run sealed`) and inspect `GET /api/v1/runs/{run_id}`.
3. When tracing is configured ([trace settings](configuration.md#logs-metrics-and-traces)), inspect `GET /api/v1/runs/{run_id}/attempts/{attempt_id}/trace`.
4. For delayed webhooks or emails, search logs for `Outbox` (`outbox_id`, `kind`) and check [outbox capacity](operations.md#outbox-retention-and-capacity).

Configure JSON stdout logs (default) or rotating log files (`telemetry.log_file`, `log_file_max_mb`, `log_file_backups`) in `service.toml`.

## Metrics

Enable a private Prometheus listener on a port separate from `server.port` and scrape **every `control` and `worker` replica** (see [`deploy/monitoring`](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/monitoring) for Grafana dashboards and alerts):

```toml
[telemetry]
metrics_port = 9464
```

| Signal                     | Key Prometheus metrics                                                                                     |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- |
| HTTP latency & errors      | `http_server_request_duration_seconds` (`a13n_request_kind="api"`)                                         |
| Run throughput             | `a13n_runs_accepted_total`, `a13n_runs_sealed_total`                                                       |
| Worker saturation          | `a13n_worker_slots` (`free`/`busy`), `a13n_attempt_queue_wait_seconds`, `a13n_attempt_duration_seconds`    |
| Queue backlog              | `a13n_backlog_size`, `a13n_backlog_oldest_age_seconds` (freshness in `a13n_backlog_updated_seconds`)       |
| Outbox & background sweeps | `a13n_outbox_deliveries_total`, `a13n_outbox_backlog_alert`, `a13n_outbox_dead`, `a13n_sweep_passes_total` |

Installed App plugins can also emit custom OpenTelemetry metrics on the same listener ([report custom metrics](app-plugins.md#report-custom-metrics)).

## Usage

Inspect token totals, cache rates, and USD spend in Console under **Insights → Usage** (with per-member filtering for workspace admins), or query the API over a half-open `[start, end)` RFC 3339 window (up to 366 days):

```sh
curl --get "$A13N_URL/api/v1/usage/overview" \
  -H "Authorization: Bearer $A13N_API_KEY" \
  --data-urlencode "start=$START" \
  --data-urlencode "end=$END" \
  --data-urlencode "timezone=UTC"
```

Paginated breakdowns are available at `/api/v1/usage/agents`, `/api/v1/usage/models`, and `/api/v1/usage/members` (requires `admin`), plus per-run/thread/session summaries at `GET /api/v1/usage` ([inspect runs](inspect-runs.md#usage)).

<span id="bind-and-verify-an-environment"></span> <span id="system-information-and-resource-monitoring"></span>
