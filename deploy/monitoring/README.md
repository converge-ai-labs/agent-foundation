# Monitoring bundle

Alert rules and Grafana dashboards for a13n Service. They read what the Service records: Prometheus metrics for health and backlog, and PostgreSQL facts for usage. The [monitoring guide](../../docs/a13n-service/monitoring.md) explains the signals and how to troubleshoot with them.

| File                        | Reads                                         | Use                                                                                                  |
| --------------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `alerts.yaml`               | Prometheus metrics, job `a13n-service`        | Seven alerts: down, 5xx, stuck backlog, saturated workers, failures, dead deliveries, failing sweeps |
| `operations-dashboard.json` | Prometheus metrics, job `a13n-service`        | Requests, runs, execution, backlog and background work                                               |
| `usage-dashboard.json`      | The Service database through a read-only role | Runs and model usage per organization, workspace and model                                           |

Prometheus and VictoriaMetrics both work: vmagent scrapes the same endpoint, vmalert reads the same rule file, and MetricsQL accepts the dashboards' PromQL.

## Scrape the Service

Set `telemetry.metrics_port` so every Service process serves `/metrics` on that port; it must differ from `server.port`. The Helm chart does this by default (`metrics.enabled`, port 9464) and never exposes the port through its Service or Ingress. Scrape every process: control and worker replicas each report their own requests, attempts and slots.

The rules and dashboards select the scrape job `a13n-service`:

- **Prometheus Operator or VictoriaMetrics operator.** Install the chart with `metrics.podMonitor.enabled=true`, plus the labels your operator's `podMonitorSelector` matches in `metrics.podMonitor.labels`. The PodMonitor names the job `a13n-service`. The VictoriaMetrics operator converts it to a `VMPodScrape` by default.

- **Prometheus with Kubernetes discovery.** The chart annotates Service Pods with `prometheus.io/scrape`, `prometheus.io/port` and `prometheus.io/path`. Keep only those Pods in a job named `a13n-service`:

  ```yaml
  scrape_configs:
    - job_name: a13n-service
      kubernetes_sd_configs:
        - role: pod
      relabel_configs:
        - source_labels: [__meta_kubernetes_pod_label_app_kubernetes_io_name, __meta_kubernetes_pod_annotation_prometheus_io_scrape]
          regex: a13n-service;true
          action: keep
        - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
          regex: ([^:]+)(?::\d+)?;(\d+)
          replacement: $1:$2
          target_label: __address__
  ```

- **Other hosts.** List each process's `host:port` under a `static_configs` job named `a13n-service`.

## Load the alert rules

Check the rules, then load them:

```sh
promtool check rules deploy/monitoring/alerts.yaml
```

- Prometheus: add the file to `rule_files`. vmalert: pass it with `-rule`.

- Prometheus Operator: the file's groups become the spec of a `PrometheusRule`. Replace the `release` label with the labels your `ruleSelector` matches:

  ```sh
  { printf 'apiVersion: monitoring.coreos.com/v1\nkind: PrometheusRule\nmetadata:\n  name: a13n-service\n  labels:\n    release: kube-prometheus-stack\nspec:\n'
    sed 's/^/  /' deploy/monitoring/alerts.yaml; } | kubectl -n a13n-service apply -f -
  ```

Tune thresholds for your traffic: the 5xx and failure ratios fire at 5% and 20%, and a queue counts as stuck when its oldest due item has waited 10 minutes.

## Import the dashboards

In Grafana, choose **Dashboards → New → Import**, upload a file and select its data source.

The usage dashboard needs a PostgreSQL data source with a role that can only read the tables it queries. Point it at a read replica when you have one; its queries scan the selected time range of `runs` and `usage_records`.

```sql
CREATE ROLE a13n_usage_reader LOGIN PASSWORD 'choose-a-password';
GRANT CONNECT ON DATABASE a13n_service TO a13n_usage_reader;
GRANT USAGE ON SCHEMA public TO a13n_usage_reader;
GRANT SELECT ON organizations, workspaces, models, runs, usage_records TO a13n_usage_reader;
```

Service tests check that every metric the rules and the operations dashboard select is one the Service serves, and run the usage dashboard's queries against the current schema.
