---
title: Monitor environments
description: Get a first resource chart, connect a historical backend, and operate environment collectors.
---

Use environment monitoring to see CPU, memory, network and disk activity on an Envd target, independently of agent runs. Start with **Local Cache** to get charts without a metrics database. Add an external backend when you need durable history. For Service's own health and run usage, see [Service monitoring](monitoring.md).

## Bind and verify an environment

You need an environment you can manage, a supported Envd target and a Service deployment with monitoring enabled. Local Cache works with external HTTP targets and managed Docker/E2B `envd_http` targets. The target must be running and its Envd sampler enabled.

1. Open the environment in Console. Under **Monitoring**, select **Configure**.
2. Set **Metrics data source** to **Local Cache**, then select **Save selection**. For a new managed Docker/E2B `envd_http` environment, select the source before creation so Service enables the 10-second sampler.
3. For an existing target without sampling, ask its operator to enable the [Envd sampler](../a13n-envd/configuration.md#system-information-and-resource-monitoring). Selecting a source or starting an existing environment does not reconfigure its daemon.
4. Let the target produce samples, then select **Open dashboard**. Check a recent time range and inspect **Query details** for freshness and actual coverage. Perform your normal workload to see changes in resource use.

Local Cache keeps samples in the owning Service process's memory. Redis coordinates ownership and queries between replicas. Restarting the process or changing ownership can discard history; use an external backend for retention across those events.

You can also check the last 15 minutes through HTTP. Set `A13N_URL` to the Service origin, `A13N_API_KEY` to a workspace key with read access, and `ENVIRONMENT_ID` to the selected environment. A private environment also requires owner or administrator access. This shell example uses `curl` and Unix-second output from `date`:

```sh
END=$(date +%s)
START=$((END - 900))
curl --fail-with-body --silent --show-error \
  -H "Authorization: Bearer $A13N_API_KEY" \
  -H 'Content-Type: application/json' \
  --data "{\"metric\":\"system_memory_used_bytes\",\"history\":\"temporary\",\"start\":$START,\"end\":$END,\"step\":60}" \
  "${A13N_URL%/}/api/v1/environments/$ENVIRONMENT_ID/metrics/query"
```

For Local Cache, inspect `observation.status` and `history.series`. Fresh samples and points establish collection; a saved binding alone does not. If the chart stays empty, check the selected source, target readiness and sampler first. For pull sources, also inspect `GET /api/v1/environments/{environment_id}/metrics/collector` and the collector's target status.

API clients set `metrics_data_source_id` at creation or use `PUT /api/v1/environments/{environment_id}/metrics-binding` with the environment's ETag in `If-Match`. Set the source ID to `null` to unbind. Custom source selection on external targets and custom identity labels require administrator approval.

For managed creation, `metrics_application` records the source version and collection mode used at launch. Its `applied_at` confirms creation with those inputs; check the receiver to confirm ingestion. Later proxy/pull selections can change live collection against an already sampled target. Native exporter changes require target reconfiguration or replacement. Unbinding or disabling a source leaves previously injected credentials on the target until you retire or reconfigure that exporter.

## Use dashboards in Console

Open **Environments → Metrics** and select an environment or monitoring Device. Search the target picker by name, ID or labels such as `region=west`, and combine search with type, status and data-source filters.

Use the time-range button to choose a recent range, absolute dates or auto-refresh. Drag across a chart to zoom; **Back to live** resumes live refresh. Hover to compare synchronized values, click legends to hide series, or use the series search on panels with more than four series.

To adapt the built-in **Environment overview** or a custom dashboard:

1. Select **Edit dashboard** and add or edit a panel.
2. Choose a typed Environment / Device metric. Administrators can instead select a data source and enter PromQL; Local Cache supports typed metrics only.
3. Select **Run queries**, inspect the result, then select **Apply panel**. Set the unit, time-series or statistic view, and panel size.
4. Drag panels into place and select **Save dashboard** to share the layout. **Discard** abandons the draft. **Restore defaults** stages the built-in layout for you to save.

Dashboard editing requires workspace write access. In-app navigation retains each dashboard's draft; save before a page reload or sign-out. If someone else saves first, your draft remains available until you explicitly reload it.

Charts request roughly 600 evaluation steps and display up to 32 series per panel. Use **Query details** to inspect truncation, retention and coverage, then narrow large queries. A failed refresh can retain a labelled previous result alongside the error. Missing observations remain gaps rather than zero values. Dashboard layouts are durable; Local Cache samples are temporary.

## Choose a data source

<span id="confirm-a-deployment-preset"></span> <span id="bootstrap-an-optional-collector"></span> <span id="vmagent"></span> <span id="grafana-alloy"></span> <span id="reconciliation-and-retirement"></span> <span id="connect-a-reverse-device"></span> <span id="query-and-size-temporary-history"></span>

- [Operate environment collectors](environment-collectors.md)
