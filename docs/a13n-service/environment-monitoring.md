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

Choose collection and history separately. A collector gets samples to storage; a query backend lets Console read them.

| Collection | Choose it when                                           | What to configure                                                                 |
| ---------- | -------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `proxy`    | You want temporary charts or reverse Device connections. | Service and Redis; optional OTLP HTTP export and query backend.                   |
| `native`   | A managed Envd target should export directly.            | An OTLP receiver; add a query backend for charts.                                 |
| `pull`     | vmagent or Alloy should scrape targets.                  | A configured collector and its delivery pipeline; add a query backend for charts. |
| `external` | You already operate ingestion.                           | A Prometheus HTTP query backend.                                                  |

**Local Cache** is the built-in proxy source with fixed configuration. Workspace administrators create custom sources in Console or with `POST /api/v1/metrics-data-sources`. For example, this body configures native export and a separate query destination:

```json
{
  "name": "Production metrics",
  "config": {
    "collection": "native",
    "export": {"endpoint": "https://ingest.example.com/v1/metrics", "protocol": "http/protobuf"},
    "query": {"endpoint": "https://metrics.example.com", "scope_labels": {"workspace_id": "ws_example"}}
  },
  "export_credentials": {"headers": {"Authorization": "Bearer <write-token>"}},
  "query_credentials": {"headers": {"Authorization": "Bearer <read-token>"}}
}
```

Replace the endpoints, labels and tokens for your backend, then select the returned source on the environment. Native HTTP export takes the complete metrics URL; native gRPC takes the receiver origin. Proxy export supports HTTP/protobuf. Query endpoints are the API base before `/api/v1/query` and `/api/v1/query_range`. Check deployment endpoint policy, TLS trust and network reachability for each destination.

Credentials are encrypted and write-only; responses report their presence. On update, omit a credential to keep it, send `null` to clear it, or supply an object to replace it. Changing a destination requires explicitly replacing or clearing its credential. Use [conditional updates](http.md#concurrency-control). Unbind all environments and Devices before deleting a source.

Scope read credentials at the backend to the workspace's permitted data. Scope labels filter built-in queries; arbitrary PromQL requires workspace administration and can query beyond those labels. Monitoring endpoints are for people and applications, outside agent tools and MCP. Backend errors remain visible rather than switching to Local Cache.

> [!WARNING]
> Native export delivers its write credential to the target process. Users and agents that execute there can read and reuse it. Use receiver-scoped, write-only credentials and trust those executors within that ingestion scope. Labels cannot prove sample identity. Choose pull or proxy if receiver credentials must stay outside the target.

## Confirm a deployment preset

An operator can offer a pull/query preset to an explicit list of workspaces. In an allowed workspace, an administrator can create a source from it:

1. Open **Add data source** and review the prefilled collector and query endpoint.
2. Leave **Use deployment query credentials** at **No**, or choose **Yes** to use the deployment's current read token at its original endpoint. Editing the endpoint clears that choice.
3. Select **Save data source**, then bind the source to an environment. The preset creates neither resource automatically.

If loading fails, retry or choose **Configure manually**. Reopen the form if its public preset changed. Credential inheritance saves an encrypted server-side snapshot; the token stays out of the browser. Later deployment rotation or preset removal leaves saved sources unchanged, so replace their credentials explicitly.

API clients read `GET /api/v1/metrics-data-sources/deployment-preset`, then create with its `deployment_preset_revision` and, when desired, `use_deployment_query_credentials: true`. Inheritance and explicit query credentials are mutually exclusive. Discovery requires administration and returns `null` when monitoring, collector configuration or workspace eligibility is missing. The backend must enforce data access for all allowed workspaces; the allowlist is not backend tenant isolation.

Operators configure the preset in [Service settings](configuration.md#environment-monitoring) or the [Helm deployment](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#environment-monitoring-collectors).

## Bootstrap an optional collector

Use this section for `pull` collection. Service writes a scrape fragment and requests reloads; the collector's existing delivery pipeline sends samples to your storage. Configure the historical query endpoint separately.

For Helm-managed vmagent, enable `monitoring.vmagent.enabled` and configure its remote-write destination. The chart supplies a dedicated writer, protected shared fragments, loopback reload and a persistent forwarding queue. Follow the [Helm lifecycle instructions](https://github.com/converge-ai-labs/agent-foundation/tree/main/deploy/kubernetes#environment-monitoring-collectors) for draining and retirement. The PVC is retained on disablement and uninstall; fence an unreachable node before forced replacement.

For an external vmagent or Alloy installation:

1. Mount a protected shared **directory** into Service and the collector. Generated files are replaced atomically, so a single-file mount is unsuitable.
2. Use compatible process UIDs so both can read mode-0600 fragments. The fragments contain listener credentials that also authorize EIP access.
3. Use storage with POSIX advisory locks and atomic rename. Writer replacements must share that directory.
4. Set `writer = true` on exactly one `all` or `control` process per collector. Other API replicas advertise the same collector with `writer = false`.
5. Allow the collector to reach target HTTP listeners and the writer to reach the protected reload endpoint. Service appends `/-/reload` to the configured origin or gateway prefix. Optional reload `headers` belong in protected Service settings.

### vmagent

Create `a13n-managed.yml` containing `[]` in the shared directory with mode 0600. Include its collector-side path in the existing vmagent configuration:

```yaml
scrape_config_files:
  - /etc/vmagent/a13n/a13n-managed.yml
```

Keep your existing scrape jobs and `-remoteWrite.url`. Configure Service with its own mount path:

```toml
[monitoring.collectors.vmagent]
directory = "/var/lib/a13n/vmagent"
endpoint = "http://vmagent:8429"
allow_http_reload = true
writer = true
```

Service replaces only `a13n-managed.yml`, a list of scrape jobs, then requests reload. Your main configuration and delivery settings remain operator-owned.

### Grafana Alloy

Run Alloy with a configuration directory containing your receiver and pipelines. If it defines `prometheus.remote_write "metrics"`, configure Service to use that receiver:

```toml
[monitoring.collectors.alloy]
directory = "/var/lib/a13n/alloy"
endpoint = "http://alloy:12345"
allow_http_reload = true
receiver = "prometheus.remote_write.metrics.receiver"
writer = true
```

Mount the same directory into Alloy. Service replaces only `a13n-managed.alloy`; the named receiver, destination and credentials must already be configured in your own files. OpenTelemetry Collector and Prometheus Server can participate in operator-owned pipelines, but Service supplies no scrape-configuration adapter for them.

### Reconciliation and retirement

Create a source whose config is `{"collection":"pull","collector":"vmagent"}` or `{"collection":"pull","collector":"alloy"}`, with a separate `query` configuration for charts. Bind it to a supported HTTP target, then check each stage:

| Check                                       | What it establishes                      | Next check                                                 |
| ------------------------------------------- | ---------------------------------------- | ---------------------------------------------------------- |
| `/api/v1/metrics-data-sources/capabilities` | A collector is configured.               | Inspect the environment's `/metrics/collector` status.     |
| `reload_requested`                          | The collector accepted a reload request. | Inspect applied config and scrape health at the collector. |
| Successful scrape                           | The collector can read the target.       | Verify storage delivery and a recent historical query.     |

Reconciliation defaults to 30 seconds and 512 targets per collector. `truncated: true` means inventory remains beyond the scan or target budget. Unsupported or unavailable targets are omitted and reported. Failed reloads retry on the next pass; vmagent applies reloads asynchronously.

To retire a target, unbind or disable its source while the writer runs, then verify the next successful reconciliation removes the job. Before stopping an external writer or disabling monitoring, remove the managed fragment and reload the collector yourself. Stopping the writer leaves previously loaded jobs active.

## Connect a reverse Device

Register a monitoring Device when an Envd target must connect back to Service. Registration is separate from an executable environment and creates no mount or execution target.

1. As a workspace administrator, create the registration with the expected native Device identity and a proxy source.
2. Save the attachment token returned once by creation. Metadata reads cannot retrieve it.
3. Enable the Envd sampler, then configure its reverse carrier for `/api/v1/devices/{device_id}/connect`, WSS, the `eip.v1` subprotocol and the attachment token in the Authorization Bearer header.
4. Select the Device in Console and verify fresh metrics. The receiving replica owns the connection and temporary history; other replicas route authorized queries to it.

For direct loopback development only, `A13N_MONITORING__ALLOW_LOOPBACK_WS=true` permits WS. Keep it off behind reverse proxies. For proxy TLS termination, configure `server.trusted_proxies` so Service accepts the trusted secure-scheme header. Connections reject query-string credentials, browser Origins and cookie-only authentication.

Rotating the token, disabling or unbinding the registration, or losing ownership closes the connection. Reverse Devices cannot use pull: they expose no collector-reachable HTTP endpoint. Native/external Device history uses operator-managed ingestion and can be queried without an active reverse connection.

## Query and size temporary history

Typed queries select one metric and a range. `history = "configured"` uses the selected query backend, or the proxy buffer when no backend is configured. `history = "temporary"` explicitly selects that buffer. Add `include_snapshot = true` for the latest EIP snapshot when building an overview or inventory view.

| Local Cache history                                        | Retention                         |
| ---------------------------------------------------------- | --------------------------------- |
| Minute-aggregated overview gauges                          | 24 hours                          |
| Per-interface network and per-device disk/cgroup I/O rates | 1 hour, up to 64 sources per kind |

Inspect coverage and truncation in each result. Counter resets and source replacement interrupt rate continuity; missing and stale observations leave gaps. Interfaces and disks keep separate identities. Pressure and filesystem history require an external backend.

Proxy capacity defaults to 64 connections per control process, configurable up to 512. At full inventory, numeric history uses less than 2 MiB per connection, plus object, snapshot and export overhead. Size process memory before increasing capacity. A database outage can retain existing history and reverse connections for up to three poll intervals while Redis ownership remains valid; observations, queries and export wait for current authority. Longer outages or lost ownership retire the cache.

External queries default to eight concurrent requests, a five-second deadline and a 4 MiB response limit. Each query allows up to 200 series, 90 days and 11,000 points per series. Choose a step suited to the chart width. External rates use five-minute PromQL windows; temporary rates use adjacent observations, so their estimates can differ. See [Settings reference](configuration-reference.md#monitoring) for bounds, and use external storage for durable history or larger fleets.
