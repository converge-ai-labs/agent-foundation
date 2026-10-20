---
title: Monitor environments
description: Select collection and history backends for environment and Device metrics, and operate optional pull collectors.
---

Environment monitoring shows the resources used by an Envd target independently of agent runs. Select a workspace metrics data source on each environment. An unselected environment has no Service background collection or automatic exporter setup. [Service monitoring](monitoring.md) covers the health of Service itself.

## Use dashboards in Console

Open **Observe → Metrics dashboards** in your workspace. Select an environment or Device to view its configured metrics. The built-in **Environment overview** is editable, just like a custom dashboard.

1. Select **Edit dashboard**. Add, edit or remove panels. Drag the panel handle to move a panel and its corner to resize it. The panel editor also accepts numeric position and size; small screens stack the saved layout.
2. Choose a typed Environment / Device metric, or select a data source and write PromQL. PromQL requires workspace administrator access. Local Cache supports typed metrics, not arbitrary PromQL.
3. Select **Run queries** to preview the panel, then **Apply panel** to update your draft. Choose a unit or statistic view as needed.
4. Select **Save dashboard** to share the changes with the workspace. **Discard** cancels them. **Restore defaults** replaces the built-in dashboard's draft; save to persist it. If another person saved first, your draft stays available until you explicitly reload. In-app navigation retains separate drafts for each dashboard; save before reloading the page or signing out.

Use the target picker to search names, IDs or environment labels such as `region=west`; combine search words with type, status and data-source filters. Data-source and Device lists also offer search and status filters.

Open the time-range button for presets, auto-refresh and absolute dates. Hover a chart to inspect synchronized values. Click legend entries to show or hide series. Panels with more than four series include a search field that filters the plotted lines and legend without querying again. Drag across a chart to zoom, or set an absolute range. Historical ranges pause auto-refresh; **Back to live** resumes it. A failed or pending refresh can show the previous result with a label and the query error. A different query or target starts with its own data.

Charts request roughly 600 evaluation steps and display up to 32 series per panel. **Query details** reports truncation, temporary retention and actual coverage. Narrow a high-cardinality query to inspect other series. Dashboard configuration is durable; selecting Local Cache does not make its sample history durable.

## Choose a data source

| Collection | Use when                                                                        | Infrastructure                                                       |
| ---------- | ------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `native`   | A managed Envd target should export directly.                                   | An OTLP receiver; optional Prometheus HTTP query backend.            |
| `pull`     | An existing collector should scrape targets.                                    | Operator-installed vmagent or Grafana Alloy; optional query backend. |
| `proxy`    | A small deployment needs temporary history, or targets connect back to Service. | Service and Redis; optional OTLP HTTP export and query backend.      |
| `external` | You already operate ingestion and only need queries.                            | A Prometheus HTTP query backend.                                     |

**Local Cache** is the built-in proxy source. Its configuration is fixed. Custom sources separate collection, export and historical queries. Service does not install a collector or time-series database. A failed backend never silently switches to Local Cache.

Workspace administrators create sources through `/api/v1/metrics-data-sources`. For example, this request body selects native export and an independent query endpoint:

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

Use the API's workspace context and authentication described in [HTTP API](http.md). Credentials are encrypted and write-only. Responses expose presence flags. On update, omit a credential to retain it, send `null` to clear it, or supply an object to replace it. Changing an endpoint requires explicitly replacing or clearing its credential. Updates use `If-Match` with the resource ETag. A source cannot be deleted while an environment or Device references it.

Use backend-scoped read credentials. Scope labels filter built-in queries; they do not restrict arbitrary PromQL. Custom PromQL requires workspace administration authority. Private environment queries also check environment ownership. Monitoring endpoints are human-facing and are excluded from agent tools and MCP.

> [!WARNING]
> A native source delivers its write credential to the target process. Environment managers can select that source, and users or agents executing on the target can read and reuse the credential. Use receiver-scoped, write-only credentials and trust those executors within the permitted ingestion boundary. Resource labels do not prevent forged sample identities. Use pull or proxy collection when receiver credentials must stay outside targets.

## Bind and verify an environment

1. Select `metrics_data_source_id` when creating or updating the environment. Use `null` to clear the selection. External identity mappings and custom sources require administrator approval.
2. For managed Docker or E2B `envd_http` creation, select the source before creation starts. Native, proxy and pull selections enable the 10-second sampler. Native also injects metrics-specific OTEL settings and write credentials.
3. Inspect `metrics_application`. It records the source version and collection mode frozen for creation. `applied_at` records successful creation with those inputs, not successful ingestion.
4. Query `/api/v1/environments/{environment_id}/metrics/query`. For pull collection, also inspect `/api/v1/environments/{environment_id}/metrics/collector` and the collector's own target status.

Existing targets need operator configuration when their daemon has no sampler. Start/resume does not reinject settings or recreate the target. A later selection can change live proxy/pull collection, but does not rewrite an already launched native exporter. Unbinding or disabling a source does not revoke credentials previously delivered to a target; retire or reconfigure that exporter separately.

Native HTTP export uses the complete metrics URL. Native gRPC uses the receiver origin. Proxy export supports HTTP/protobuf. Query endpoints are API bases before `/api/v1/query` and `/api/v1/query_range`. Endpoint policy, TLS trust and network reachability must permit each destination.

## Bootstrap an optional collector

Service manages scrape configuration and reloads for these two collectors:

| Collector     | Configuration key | Service-owned fragment                                     | Tested version |
| ------------- | ----------------- | ---------------------------------------------------------- | -------------- |
| vmagent       | `vmagent`         | `a13n-managed.yml`, included through `scrape_config_files` | 1.128.0        |
| Grafana Alloy | `alloy`           | `a13n-managed.alloy`, connected to an existing receiver    | 1.10.2         |

The Linux interoperability tests exercise authenticated Envd scraping, reload, tenant labels, target removal and preservation of the operator's delivery configuration. They use Prometheus 3.2.1 as a disposable remote-write receiver. These are tested versions, not minimum-version guarantees. The tests are opt-in and do not cover collector TLS or cloud-provider deployment.

An OTLP receiver, including an operator-configured OpenTelemetry Collector, can serve native or proxy export independently. Service has no automatic scrape-configuration adapter for OpenTelemetry Collector or Prometheus Server.

Use an existing collector installation with an operator-owned delivery pipeline. Service replaces only its generated scrape fragment. The collector must reach each target's HTTP listener. The Service writer must reach the collector's reload endpoint. Configure historical storage and a Prometheus HTTP query endpoint separately; a pull collector alone does not provide dashboard history.

1. Create a protected shared directory. Mount the directory into Service and the collector, rather than mounting the generated file: updates use atomic replacement.
2. Run both processes with a UID that can read Service's mode-0600 fragments. Limit directory access to trusted operators. Fragments contain listener credentials that also authorize EIP access.
3. Use storage supporting POSIX advisory locks and atomic rename. Rolling replacements of the writer must share the same directory.
4. Configure exactly one `all` or `control` process with `writer = true` per collector. Other API replicas use the same collector configuration with `writer = false`.
5. Protect reload access with network policy or authenticated ingress. Configure optional `headers` in the protected Service settings. The endpoint is an origin or gateway prefix; Service appends `/-/reload`.

### vmagent

Create an initial `a13n-managed.yml` containing `[]` in the shared directory with mode 0600. Add its collector-side path to your existing vmagent configuration:

```yaml
scrape_config_files:
  - /etc/vmagent/a13n/a13n-managed.yml
```

Keep existing scrape jobs and `-remoteWrite.url` settings. Configure Service using its own mount path:

```toml
[monitoring.collectors.vmagent]
directory = "/var/lib/a13n/vmagent"
endpoint = "http://vmagent:8429"
writer = true
```

The generated fragment is a top-level list of scrape jobs, not a full `scrape_configs` document. Service submits a reload after replacing the fragment.

### Grafana Alloy

Run Alloy with a configuration directory. Keep your receiver and existing pipelines in operator-owned `.alloy` files in that directory. For example, an existing `prometheus.remote_write "metrics"` component exposes `prometheus.remote_write.metrics.receiver`:

```toml
[monitoring.collectors.alloy]
directory = "/var/lib/a13n/alloy"
endpoint = "http://alloy:12345"
receiver = "prometheus.remote_write.metrics.receiver"
writer = true
```

Service writes only `a13n-managed.alloy`. Mount the same directory at Alloy's configuration path. The receiver expression must already exist; Service does not create or edit its remote-write destination or credentials.

### Reconciliation and retirement

Create a source with `{"collection":"pull","collector":"vmagent"}` or `{"collection":"pull","collector":"alloy"}`. Add `query` independently when historical queries are needed. `/api/v1/metrics-data-sources/capabilities` reports which collectors are configured. Configuration availability is separate from runtime health.

Reconciliation defaults to every 30 seconds, with up to 512 environments per collector and eight concurrent route discoveries. It supports external HTTP and managed Docker/E2B `envd_http` targets. Unsupported or unavailable targets are omitted and reported, rather than redirected through Service proxy collection.

`reload_requested` means the collector accepted a reload request. Verify applied configuration, scrape health and delivery at the collector; vmagent reload is asynchronous. Failed reloads are retried on reconciliation. Pending or expired receipts do not prove collection is working.

To remove a source while the writer runs, disable or unbind it and verify the next successful reconciliation removes its jobs. Before stopping the writer, removing its configuration or setting `monitoring.enabled = false`, remove the managed fragment and reload the collector yourself. Stopping reconciliation leaves previously loaded jobs active.

## Connect a reverse Device

A monitoring Device registration is separate from an executable environment. Workspace administrators create it with an expected native Device identity and select a proxy source. Creation returns a one-time attachment token. Store it securely; metadata reads cannot retrieve it.

Connect an Envd reverse carrier to `/api/v1/devices/{device_id}/connect` using WSS, the `eip.v1` subprotocol and the attachment token as Bearer authentication. Direct loopback WS requires the explicit development setting `A13N_MONITORING__ALLOW_LOOPBACK_WS=true` (default false). Keep it disabled behind a reverse proxy: a loopback peer may be a proxy carrying remote traffic. For TLS termination, configure `server.trusted_proxies` so the trusted proxy's secure scheme reaches Service. Query-string credentials, browser Origins and cookie-only authentication are refused. Configure the Envd sampler before connecting.

The receiving replica owns the socket and temporary history. Other API replicas route authorized queries to that owner. Token rotation, disablement, source removal and lost ownership retire the carrier. Reverse Devices cannot select pull collection because the carrier does not supply a collector-reachable HTTP endpoint. Native/external Device history can be queried independently of a connected carrier.

## Query and size temporary history

Typed environment and Device queries select one metric and a time range. `history = "configured"` uses the selected source's query backend, or its temporary buffer when no query backend exists. `history = "temporary"` explicitly selects a proxy source's buffer. `include_snapshot = true` adds the latest EIP snapshot for overview and inventory views; ordinary chart queries return freshness and the requested series only.

Local Cache retains minute-aggregated overview gauges for 24 hours and per-interface network and per-device disk/cgroup I/O rates for one hour. Resource history retains at most 64 sources per kind, including recently disappeared sources. Results report actual coverage and truncation. Missing and stale observations remain gaps; counter resets and source replacement break rate continuity. Interface and disk identities stay separate rather than being summed indiscriminately. Pressure and filesystem history require an external backend.

Proxy capacity defaults to 64 connections per control process and can be configured up to 512. A full inventory uses less than 2 MiB of numeric history per connection, plus Python objects, the latest snapshot and export state. Size process memory for your inventory before increasing admission. Restart or ownership changes can discard temporary history. Redis carries ownership and bounded query messages; it does not store historical samples.

External queries have a separate concurrency budget, default eight, a five-second deadline and a 4 MiB response limit. Each query is bounded to 200 series, 90 days and 11,000 evaluation points per series. Choose a step suited to the chart width. External rate queries use five-minute PromQL windows; temporary rates use adjacent observations, so their estimates can differ.

See [Settings reference](configuration-reference.md#monitoring) for configuration bounds. Use an external history backend for durable retention, longer resource history or larger fleets.
