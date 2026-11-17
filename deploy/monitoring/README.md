# Monitoring bundle

Optional, editable Grafana dashboards and opt-in alert presets for a13n Service. The bundle uses your existing Prometheus-compatible backend and Grafana; it installs neither and adds no Grafana management integration. The [monitoring guide](../../docs/a13n-service/monitoring.md) explains diagnosis and the [observability contract](../../spec/a13n-service/12-observability.md) owns metric semantics.

| File                        | Reads                   | Purpose                                                                                                            |
| --------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `operations-dashboard.json` | Service process metrics | API, SSE, queue freshness, worker capacity, execution, scheduling, environments, persistence and delivery          |
| `usage-dashboard.json`      | Read-only PostgreSQL    | Independent started-Run and first-ingested model populations, pricing coverage, UTC trends and ID-based breakdowns |
| `alerts/availability.yaml`  | Service metrics         | Scrape failures, business API errors and reporter/sweep freshness                                                  |
| `alerts/capacity.yaml`      | Service metrics         | Due work, full or missing workers and bounded count ceilings                                                       |
| `alerts/execution.yaml`     | Service metrics         | Run failures, lease expiry, delivery state, sweep/item failures, missed schedules and live-output drops            |

Each preset is independent. Nothing enables alerts automatically. Prometheus loads their groups directly; vmalert can consume the same PromQL rules. Test and tune thresholds against your traffic before routing notifications. The old combined `alerts.yaml` is replaced by these presets; remove its entry when migrating to avoid duplicate alerts.

## Scrape every Service process

Set `telemetry.metrics_port` (different from `server.port`). The Helm chart enables port 9464 by default and exposes it only on Pods, never its Service or Ingress. Scrape control and worker processes independently.

Use these target labels consistently:

- `job="a13n-service"`: default selected job and the presets' selector.
- `deployment`: one shared database/scheduling domain. Keep this unique across clusters when forwarding to a shared backend.
- `role`: `all`, `control` or `worker`.
- `instance`: a distinct process target, provided by the scraper.

The chart's optional PodMonitor (`metrics.podMonitor.enabled=true`) sets `job`, `deployment=<namespace>/<release>` and `role` from the Pod component label. Add a cluster prefix through your collector's relabeling when needed. Set `metrics.podMonitor.labels` to match the operator's selector. The VictoriaMetrics operator can convert the PodMonitor to a VMPodScrape.

For plain Prometheus Kubernetes discovery:

```yaml
scrape_configs:
  - job_name: a13n-service
    kubernetes_sd_configs:
      - role: pod
    scrape_interval: 30s
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_name, __meta_kubernetes_pod_annotation_prometheus_io_scrape]
        regex: a13n-service;true
        action: keep
      - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
        regex: ([^:]+)(?::\d+)?;(\d+)
        replacement: $1:$2
        target_label: __address__
      - source_labels: [__meta_kubernetes_namespace, __meta_kubernetes_pod_label_app_kubernetes_io_instance]
        separator: /
        target_label: deployment
      - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_component]
        target_label: role
```

For static hosts, group targets by domain and role:

```yaml
scrape_configs:
  - job_name: a13n-service
    scrape_interval: 30s
    static_configs:
      - targets: [control.internal:9464]
        labels: {deployment: production-a, role: control}
      - targets: [worker-1.internal:9464, worker-2.internal:9464]
        labels: {deployment: production-a, role: worker}
```

Use a scrape interval of **30 seconds or less**, including the data source's minimum interval. The bundle's 60-second freshness cutoff allows for the 15-second report cadence, startup jitter and pass timeout at that scrape interval. Longer intervals require increasing the cutoff consistently in the dashboard and all presets; otherwise fresh inventory can appear missing between scrapes.

Scrape success is not readiness: check `/readyz` separately. `up == 0` cannot detect a target removed from discovery; use your infrastructure's expected-replica checks for that case. Missing series are not healthy zeroes.

## Import and customize dashboards

In Grafana, choose **Dashboards → New → Import** and upload either JSON. Select the matching **Prometheus** or **PostgreSQL** data source using the dashboard's first variable. Operations also selects **Job** and **Deployment**; Usage selects **Organization** and **Workspace**, displaying names with immutable IDs so same-name objects remain distinct.

Both JSON files are editable ordinary dashboards: modify panels, queries, thresholds and layout, save a copy, or export your customized JSON. Keep custom copies under different UIDs when importing later bundle updates. The Service neither overwrites nor manages your saved dashboards.

Operations sums local slots and connections. Shared database snapshots are filtered by reporter freshness **before** taking a per-deployment maximum. A fresh empty queue is zero; an expired snapshot is absent. Counts at the exported ceiling are lower bounds. API latency/error panels exclude probes, static responses and SSE; stream header latency and connection outcomes have their own panels. A healthy long-lived stream is not a slow API request.

### PostgreSQL access

The Usage dashboard is **operator-only**. Its selectors filter analysis; they do not enforce Service authorization. Anyone allowed to query its data source can read every row granted to that database role. Do not expose an unrestricted data source to workspace viewers. Use an appropriately restricted database/view or the authorized Service usage API for tenant-facing reports.

Point the data source at a read replica when available. Create a read-only login (choose and store its password through your normal secret system):

```sql
CREATE ROLE a13n_usage_reader LOGIN PASSWORD 'choose-a-password';
GRANT CONNECT ON DATABASE a13n_service TO a13n_usage_reader;
GRANT USAGE ON SCHEMA public TO a13n_usage_reader;
\i deploy/monitoring/usage-reader.sql
```

Run the bundled [column grants](usage-reader.sql) as the table owner with `psql` from the repository root. Reapply them when upgrading from the previous whole-table recipe: they revoke those table grants before granting only the columns used by the queries and selectors. Keep this role out of broader role memberships. It can read the granted reporting facts across organizations, but not Run payloads/caller headers, Model configuration or Organization settings. `SELECT *` is intentionally denied.

Queries use UTC date strings and the half-open selected range `[from,to)`. Model contributions use their first `ingested_at`; later replacements revise that original bucket. Runs are independently selected by `started_at`, with average runtime over sealed members. Waiting seals before human approval; resumed successors count separately. Cached input is already included in input tokens. Known cost is recorded USD, never current-price repricing: zero requests → 0, all prices unknown → unavailable, partial pricing → known subtotal plus unpriced count. Breakdown tables show the top 100 rows; overview totals are untruncated.

## Opt in to alert presets

Validate the presets you plan to enable:

```sh
promtool check rules deploy/monitoring/alerts/*.yaml
```

For Prometheus, list only wanted presets in `rule_files`; for vmalert pass their paths with `-rule`:

```yaml
rule_files:
  - /etc/prometheus/a13n/availability.yaml
  - /etc/prometheus/a13n/capacity.yaml
  # Enable execution.yaml when its diagnostics match your on-call responsibilities.
```

For Prometheus Operator, wrap each selected file's `groups` under a `PrometheusRule.spec`, and set metadata labels to match your `ruleSelector`. The chart does not create a PrometheusRule or provision Grafana.

Preset starting points include >5% API 5xx with >0.02 failures/sec, >=5 failed Runs with >20% failure share among completed+failed seals, and >10-minute due queue age. Outbox policy pressure has its own sustained-backlog rule; new dead-delivery events alert for their recent window, while retained dead rows remain a dashboard diagnostic. Sweep failure alerts require both a minimum count and a >20% failure share, and live-output degradation excludes deliberate oversized-frame refusal. Holds and minimum event counts reduce idle/low-traffic noise. Cancelled/waiting Runs, planned handoffs, empty worker scans and expected provider renewal limits are separate outcomes. Inspect queue-specific owners before changing capacity.

## Environment monitoring is separate

This bundle observes the Service. [Environment monitoring](../../docs/a13n-service/environment-monitoring.md) observes individual environments with its own authorized data sources and object identities. The [Helm chart](../kubernetes/README.md#environment-monitoring-collectors) can install an optional vmagent, and Service supports external vmagent/Alloy scrape fragments. Operators own forwarding destinations, storage and retention. Neither path turns Service metrics into per-tenant accounting.

## Validation

Ordinary Service tests check exporter families and bounded labels, lifecycle/rollback observations, actual dashboard SQL values, identity collisions, unknown prices and time boundaries. The opt-in test uses disposable PostgreSQL/Redis plus pinned Prometheus and Grafana, validates rules with promtool, evaluates freshness/alert fixtures, executes all Operations PromQL, imports both editable dashboards, and queries every Usage panel through Grafana with the documented read-only role:

```sh
docker pull prom/prometheus:v3.2.1
docker pull grafana/grafana:12.3.1
A13N_MONITORING_TEST=1 uv run pytest packages/a13n-service/tests/test_monitoring_bundle_live.py
```

For manual browser review, set `A13N_MONITORING_REVIEW_FILE` to an absolute JSON output path. The test writes localhost endpoints after backend checks and keeps its owned containers alive until a file with the same stem and `.done` suffix exists (30-minute deadline). This fixture uses synthetic data and anonymous local Grafana admin access; it is a test fixture, not deployment configuration. Cleanup removes only its own containers.
