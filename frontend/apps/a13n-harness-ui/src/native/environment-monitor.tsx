import { useState, type ComponentProps } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ActivityIcon,
  ArrowClockwiseIcon,
  MagnifyingGlassPlusIcon,
  MagnifyingGlassMinusIcon,
} from "@phosphor-icons/react";
import { Button, ModalFrame, SegmentedControl } from "a13n-ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import {
  Trend,
  bytes,
  percent,
  timeLabel,
  zoomRange,
  type TimeRange,
} from "./environment-trend";
import styles from "./environment-monitor.module.css";

const states: Record<Schema<"EnvironmentObservation">["status"], string> = {
  ready: "Sampling",
  waiting: "Waiting for a sample",
  not_started: "Not started",
  disabled: "Monitoring disabled",
  stale: "Stale sample",
  offline: "Offline",
  revoked: "Revoked",
};

export function EnvironmentMonitor() {
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      size="xl"
      trigger={
        <Button
          variant="ghost"
          size="icon"
          title="Environment monitor"
          aria-label="Environment monitor"
        >
          <ActivityIcon />
        </Button>
      }
      title="Environment monitor"
      closeLabel="Close environment monitor"
      description="Device resources · one hour of temporary history"
    >
      {open && <MonitorContent />}
    </ModalFrame>
  );
}

export function MonitorContent() {
  const { client } = useTransport();
  const [selected, select] = useState("");
  const [refreshInterval, setRefreshInterval] = useState(60000);
  const catalog = useQuery({
    queryKey: ["environment-monitoring"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/environment-monitoring", { signal })),
    refetchInterval: refreshInterval || false,
    refetchOnWindowFocus: refreshInterval > 0,
    retry: false,
  });
  const environments = catalog.data?.environments ?? [];
  const id =
    environments.find((item) => item.id === selected)?.id ??
    environments[0]?.id;
  const detail = useQuery({
    queryKey: ["environment-metrics", id],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/environment-monitoring/{target_id}", {
          params: { path: { target_id: id! } },
          signal,
        }),
      ),
    enabled: !!id,
    refetchInterval: refreshInterval || false,
    refetchOnWindowFocus: refreshInterval > 0,
    retry: false,
  });
  const observation = detail.data?.environment;
  return (
    <div className={styles.monitor}>
      <ErrorNotice error={catalog.error ?? detail.error} />
      {catalog.isPending && <p role="status">Loading environments…</p>}
      {catalog.data && (
        <>
          <div className={styles.toolbar}>
            <label>
              Device
              <select
                value={id ?? ""}
                onChange={(event) => select(event.target.value)}
                disabled={!environments.length}
              >
                {!environments.length && (
                  <option value="">No monitored Devices</option>
                )}
                {environments.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.transport} · {item.id.slice(-8)}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.refreshControl}>
              Auto-refresh
              <select
                value={refreshInterval}
                onChange={(event) =>
                  setRefreshInterval(Number(event.target.value))
                }
              >
                <option value={60000}>Every 1m</option>
                <option value={0}>Off</option>
              </select>
            </label>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Refresh observations"
              title="Refresh observations"
              disabled={catalog.isFetching || detail.isFetching}
              onClick={() => {
                void catalog.refetch();
                if (id) void detail.refetch();
              }}
            >
              <ArrowClockwiseIcon />
            </Button>
          </div>
          {catalog.data.omitted > 0 && (
            <p role="status">
              {catalog.data.omitted} Devices exceed the {catalog.data.capacity}
              -Device monitoring limit.
            </p>
          )}
          {!environments.some((item) => item.transport === "stdio") && (
            <p className={styles.note}>
              Monitoring attaches to running Envd daemons only; it never starts
              a local runtime. Non-Envd providers do not report these metrics.
            </p>
          )}
        </>
      )}
      {id && detail.isPending && <p role="status">Loading observations…</p>}
      {observation && detail.data && (
        <Observation
          key={id}
          observation={observation}
          history={detail.data.history}
          resources={detail.data.resources ?? []}
          omittedRateSources={detail.data.omitted_rate_sources ?? 0}
          updatedAt={detail.dataUpdatedAt}
          refreshing={refreshInterval > 0}
        />
      )}
      <p className={styles.note}>
        Last hour · App memory only · clears on restart. Missing observations
        remain gaps; unavailable values are shown as —.
      </p>
    </div>
  );
}

function Observation({
  observation: env,
  history,
  resources,
  omittedRateSources,
  updatedAt,
  refreshing,
}: {
  observation: Schema<"EnvironmentObservation">;
  history: Schema<"HistoryPoint">[];
  resources: Schema<"ResourceHistory">[];
  omittedRateSources: number;
  updatedAt: number;
  refreshing: boolean;
}) {
  const [view, setView] = useState("overview");
  const [duration, setDuration] = useState(15);
  const [fixedRange, setFixedRange] = useState<TimeRange | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const latest = updatedAt / 60000;
  const range = fixedRange ?? { start: latest - duration, end: latest };
  const selectRange = (next: TimeRange | null) => {
    setFixedRange(next);
    setCursor(null);
  };
  const chartProps = {
    range,
    cursor,
    onCursor: setCursor,
    onZoom: selectRange,
  };
  const sample = env.sample;
  const system = sample?.system;
  const info = env.system_info;
  const roots = env.roots ?? [];
  const filesystems = [
    ...Object.entries(sample?.filesystems ?? {}),
    ...Object.entries(sample?.local_filesystems?.filesystems ?? {}).map(
      ([name, value]) => [`Filesystem ${name}`, value] as const,
    ),
  ];
  const memory =
    system?.memory_total_bytes != null && system.memory_available_bytes != null
      ? system.memory_total_bytes - system.memory_available_bytes
      : null;
  return (
    <>
      <div className={styles.identity}>
        <details className={styles.deviceInfo}>
          <summary>Device information</summary>
          <p>
            {env.name} · {env.transport} · {env.id}
          </p>
          {info && (
            <p>
              {[
                info.os,
                info.os_version,
                info.architecture,
                info.cpu_model,
                info.kernel_version,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
          <p>
            System and cgroup metrics describe the Device, not an individual
            Run.
          </p>
          <p>
            App collection: every {env.collection_interval_seconds ?? 60}s ·
            native sampling:{" "}
            {env.sample_interval_ms == null
              ? "unavailable"
              : `every ${env.sample_interval_ms / 1000}s`}
          </p>
          {roots.length > 0 && (
            <ul>
              {roots.map((root) => (
                <li key={root}>{root}</li>
              ))}
            </ul>
          )}
        </details>
        <div className={styles.freshness}>
          <strong>{states[env.status]}</strong>
          <span>
            {env.age_ms == null
              ? "No sample received"
              : `Sample age ${Math.floor(env.age_ms / 1000)}s`}
          </span>
        </div>
      </div>
      <div className={styles.gauges}>
        <div>
          <span>System CPU</span>
          <strong>{percent(system?.cpu_busy_percent)}</strong>
          <small>{system?.logical_cpu_count ?? "—"} logical CPUs</small>
        </div>
        <div>
          <span>System memory used</span>
          <strong>{bytes(memory)}</strong>
          <small>of {bytes(system?.memory_total_bytes)}</small>
        </div>
        {sample?.cgroup_v2 && (
          <div>
            <span>Cgroup memory</span>
            <strong>{bytes(sample?.cgroup_v2?.memory_current_bytes)}</strong>
            <small>
              {sample?.cgroup_v2?.memory_max?.kind === "unlimited"
                ? "Unlimited"
                : sample?.cgroup_v2?.memory_max?.kind === "limited"
                  ? `Limit ${bytes(sample.cgroup_v2.memory_max.bytes)}`
                  : "Limit unavailable"}
            </small>
          </div>
        )}
      </div>
      <SegmentedControl
        label="Resource view"
        value={view}
        onValueChange={setView}
        className={styles.views}
        options={[
          { value: "overview", label: "Overview" },
          { value: "network", label: "Network" },
          { value: "storage", label: "Storage" },
          { value: "daemon", label: "Envd" },
        ]}
      />
      {view !== "daemon" && (
        <>
          <div className={styles.chartToolbar}>
            <div className={styles.rangeControls}>
              <select
                aria-label="Time range"
                value={fixedRange ? "custom" : duration}
                onChange={(event) => {
                  setDuration(Number(event.target.value));
                  selectRange(null);
                }}
              >
                {fixedRange && <option value="custom">Custom range</option>}
                <option value={15}>Last 15 minutes</option>
                <option value={60}>Last 1 hour</option>
              </select>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Zoom in"
                title="Zoom in"
                disabled={range.end - range.start <= 2}
                onClick={() => selectRange(zoomRange(range, 0.5, latest))}
              >
                <MagnifyingGlassPlusIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Zoom out"
                title="Zoom out"
                disabled={range.end - range.start >= 60}
                onClick={() => selectRange(zoomRange(range, 2, latest))}
              >
                <MagnifyingGlassMinusIcon />
              </Button>
              {fixedRange && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => selectRange(null)}
                >
                  Back to live
                </Button>
              )}
            </div>
            <span className={styles.liveState}>
              {fixedRange
                ? "Historical range"
                : refreshing
                  ? "Live · auto-refreshing"
                  : "Refresh paused"}
            </span>
          </div>
          <div className={styles.chartHint}>
            <span>
              {timeLabel(range.start)} – {timeLabel(range.end)} ·{" "}
              {Intl.DateTimeFormat().resolvedOptions().timeZone}
            </span>
            <span>Drag to zoom · observed minute averages / peaks</span>
          </div>
        </>
      )}
      {view === "overview" && (
        <>
          <div className={styles.chartGrid}>
            <Trend
              {...chartProps}
              title="System CPU"
              metric="cpu_busy_percent"
              history={history}
              format={percent}
            />
            <Trend
              {...chartProps}
              title="System memory used"
              metric="memory_used_bytes"
              history={history}
              format={bytes}
            />
            {history.some((point) => point.cgroup_memory_bytes != null) && (
              <Trend
                {...chartProps}
                title="Cgroup memory"
                metric="cgroup_memory_bytes"
                history={history}
                format={bytes}
              />
            )}
          </div>
          <MetricList
            title="Native memory"
            values={[
              ["Wired", bytes(system?.memory_wired_bytes)],
              ["Compressed", bytes(system?.memory_compressed_bytes)],
              ["Swap used", bytes(system?.swap_used_bytes)],
              ["Swap capacity", bytes(system?.swap_total_bytes)],
              ["Committed", bytes(system?.commit_total_bytes)],
              ["Commit limit", bytes(system?.commit_limit_bytes)],
              ["System cache", bytes(system?.memory_cache_bytes)],
            ].filter(([, value]) => value !== "—")}
          />
          <MetricList
            title="System counts"
            values={[
              ["Processes", count(system?.process_count)],
              ["Threads", count(system?.thread_count)],
              ["Handles", count(system?.handle_count)],
              ["Cgroup tasks", count(sample?.cgroup_v2?.pids_current)],
            ].filter(([, value]) => value !== "—")}
          />
        </>
      )}
      {view === "network" && (
        <>
          <ResourceRates
            kind="network"
            resources={resources}
            chartProps={chartProps}
          />
          <details>
            <summary>Connections and cumulative counters</summary>
            <div className={styles.resourceSection}>
              <MetricList
                title="Sockets"
                values={[
                  ["TCP", count(sample?.connections?.tcp_total)],
                  ["UDP", count(sample?.connections?.udp_sockets)],
                  ...Object.entries(sample?.connections?.tcp_states ?? {}).map(
                    ([state, value]) => [
                      state.replaceAll("_", " "),
                      count(value),
                    ],
                  ),
                ]}
              />
              <Trend
                {...chartProps}
                title="TCP sockets"
                metric="tcp_connections"
                history={history}
                format={count}
              />
              <CounterTable sample={sample} kind="network" />
            </div>
          </details>
        </>
      )}
      {view === "storage" && (
        <>
          <ResourceRates
            kind="disk"
            resources={resources}
            chartProps={chartProps}
          />
          <details>
            <summary>Cumulative disk counters</summary>
            <CounterTable sample={sample} kind="disk" />
          </details>
          {filesystems.length > 0 && (
            <section>
              <h3>Filesystems</h3>
              <p className={styles.note}>
                Whole-filesystem capacity. Bind mounts and shared APFS space
                must not be summed.
              </p>
              {sample?.local_filesystems?.truncated && (
                <p role="status">
                  Local filesystem discovery reached its 16-volume limit.
                </p>
              )}
              <div className={styles.filesystems}>
                {filesystems.map(([name, fs]) => (
                  <div key={name}>
                    <div className={styles.filesystemHeading}>
                      <strong>{name}</strong>
                      <span>
                        {bytes(fs.metrics?.available_bytes)} available /{" "}
                        {bytes(fs.metrics?.total_bytes)}
                      </span>
                    </div>
                    {fs.metrics && fs.metrics.total_bytes > 0 && (
                      <meter
                        aria-label={`${name} used capacity`}
                        min={0}
                        max={fs.metrics.total_bytes}
                        value={Math.max(
                          0,
                          fs.metrics.total_bytes - fs.metrics.free_bytes,
                        )}
                      />
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
      {view === "daemon" && (
        <>
          <MetricList
            title="Envd-owned resources"
            values={[
              ["Sessions", count(sample?.daemon?.sessions)],
              ["Command groups", count(sample?.daemon?.processes)],
              ["File transfers", count(sample?.daemon?.transfers)],
              ["Carrier connections", count(sample?.daemon?.connections)],
              ["Retained output", bytes(sample?.daemon?.retained_output_bytes)],
            ]}
          />
          <p className={styles.note}>
            Live and detached Sessions, started command groups including
            residual cleanup, nonterminal transfers, public carrier connections,
            and actual retained output bytes. Worker observations that time out
            remain unavailable.
          </p>
        </>
      )}
      {omittedRateSources > 0 && (view === "network" || view === "storage") && (
        <p role="status" className={styles.note}>
          {omittedRateSources} sources exceed the history limit of 64 interfaces
          and 64 disks per Device.
        </p>
      )}
      {sample && (
        <details>
          <summary>Latest observation details</summary>
          <p className={styles.note}>
            Pressure, limits and cumulative counters retain their native units.
            Counters are not averaged or summed across Devices.
          </p>
          <pre>
            {JSON.stringify(
              {
                system: sample.system,
                cgroup_v2: sample.cgroup_v2,
                network: sample.network,
                disks: sample.disks,
                connections: sample.connections,
                daemon: sample.daemon,
                collection_duration_ms: sample.collection_duration_ms,
              },
              null,
              2,
            )}
          </pre>
        </details>
      )}
    </>
  );
}

const count = (value: number | null | undefined) =>
  value == null
    ? "—"
    : new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(
        value,
      );

function MetricList({ title, values }: { title: string; values: string[][] }) {
  if (!values.length) return null;
  return (
    <section className={styles.resourceSection}>
      <h3>{title}</h3>
      <dl className={styles.metricList}>
        {values.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

type ChartControls = Pick<
  ComponentProps<typeof Trend>,
  "range" | "cursor" | "onCursor" | "onZoom"
>;
const rateFields = {
  network: [
    ["received_bytes_per_second", "Receive"],
    ["transmitted_bytes_per_second", "Transmit"],
  ],
  disk: [
    ["read_bytes_per_second", "Read throughput"],
    ["written_bytes_per_second", "Write throughput"],
    ["read_operations_per_second", "Read operations"],
    ["write_operations_per_second", "Write operations"],
  ],
} as const;

function ResourceRates({
  kind,
  resources,
  chartProps,
}: {
  kind: "network" | "disk";
  resources: Schema<"ResourceHistory">[];
  chartProps: ChartControls;
}) {
  const [selected, select] = useState("");
  const candidates = resources.filter((item) => item.kind === kind);
  const source =
    candidates.find((item) => item.name === selected) ?? candidates[0];
  const fields = rateFields[kind];
  return (
    <section className={styles.resourceSection}>
      <div className={styles.toolbar}>
        <label>
          {kind === "network" ? "Interface" : "Disk"}
          <select
            aria-label={
              kind === "network" ? "History interface" : "History disk"
            }
            value={source?.name ?? ""}
            onChange={(event) => select(event.target.value)}
            disabled={!source}
          >
            {!source && <option value="">No observations</option>}
            {candidates.map((item) => (
              <option key={item.name} value={item.name}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className={styles.chartGrid}>
        {fields.map(([key, label]) => (
          <Trend
            key={key}
            {...chartProps}
            title={label}
            metric="value"
            history={(source?.history ?? []).map((point) => ({
              minute: point.minute,
              segment: point.segment,
              value: point.rates[key],
            }))}
            format={(value) =>
              value == null
                ? "—"
                : `${key.includes("operations") ? count(value) : bytes(value)}/s`
            }
          />
        ))}
      </div>
      <p className={styles.note}>
        Rates for the selected source between App observations; sources are
        never summed.
      </p>
    </section>
  );
}

function CounterTable({
  sample,
  kind,
}: {
  sample: Schema<"MetricsSample"> | null | undefined;
  kind: "network" | "disk";
}) {
  const network = kind === "network";
  const rows = network
    ? Object.entries(sample?.network?.interfaces ?? {}).map(
        ([name, value]) => ({
          name,
          first: value.received_bytes,
          second: value.transmitted_bytes,
          count1: value.received_packets,
          count2: value.transmitted_packets,
        }),
      )
    : Object.entries(sample?.disks?.devices ?? {}).map(([name, value]) => ({
        name,
        first: value.read_bytes,
        second: value.written_bytes,
        count1: value.read_operations,
        count2: value.write_operations,
      }));
  const truncated = network
    ? sample?.network?.truncated
    : sample?.disks?.truncated;
  return (
    <section className={styles.resourceSection}>
      <h3>{network ? "Interface counters" : "Disk counters"}</h3>
      <p className={styles.note}>
        Cumulative native counters, not current rates.
      </p>
      {truncated && (
        <p role="status">Collection reached its 64-source limit.</p>
      )}
      {rows.length ? (
        <div className={styles.counterTable}>
          <table>
            <thead>
              <tr>
                <th scope="col">{network ? "Interface" : "Disk"}</th>
                <th scope="col">{network ? "Received" : "Read"}</th>
                <th scope="col">{network ? "Transmitted" : "Written"}</th>
                <th scope="col">{network ? "RX packets" : "Read ops"}</th>
                <th scope="col">{network ? "TX packets" : "Write ops"}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name}>
                  <th scope="row">{row.name}</th>
                  <td>{bytes(row.first)}</td>
                  <td>{bytes(row.second)}</td>
                  <td>{count(row.count1)}</td>
                  <td>{count(row.count2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={styles.note}>
          No {network ? "interface" : "disk"} counters available.
        </p>
      )}
    </section>
  );
}
