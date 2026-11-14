import { useTranslation } from "../i18n";
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
import { DebugOnly } from "../shell/debug";
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
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      bottomStickOnMobile
      open={open}
      onOpenChange={setOpen}
      size="xl"
      trigger={
        <Button
          variant="ghost"
          size="icon"
          title={t("Environment monitor")}
          aria-label={t("Environment monitor")}
        >
          <ActivityIcon />
        </Button>
      }
      title={t("Environment monitor")}
      closeLabel={t("Close environment monitor")}
      description={t("Device resources - one hour of temporary history")}
    >
      {open && <MonitorContent />}
    </ModalFrame>
  );
}

export function MonitorContent() {
  const { t } = useTranslation();
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
      {catalog.isPending && <p role="status">{t("Loading environments…")}</p>}
      {catalog.data && (
        <>
          <div className={styles.toolbar}>
            <label>
              {t("Device")}
              <select
                value={id ?? ""}
                onChange={(event) => select(event.target.value)}
                disabled={!environments.length}
              >
                {!environments.length && (
                  <option value="">{t("No monitored devices")}</option>
                )}
                {environments.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} - {item.transport} - {item.id.slice(-8)}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.refreshControl}>
              {t("Auto-refresh")}
              <select
                value={refreshInterval}
                onChange={(event) =>
                  setRefreshInterval(Number(event.target.value))
                }
              >
                <option value={60000}>{t("Every 1m")}</option>
                <option value={0}>{t("Off")}</option>
              </select>
            </label>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t("Refresh observations")}
              title={t("Refresh observations")}
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
              {t(
                "{{omitted}} Devices exceed the {{capacity}}-Device monitoring limit.",
                {
                  omitted: catalog.data.omitted,
                  capacity: catalog.data.capacity,
                },
              )}
            </p>
          )}
          {!environments.some((item) => item.transport === "stdio") && (
            <p className={styles.note}>
              {t(
                "Metrics are available for running envd runtimes. Opening this page won't start a runtime.",
              )}
            </p>
          )}
        </>
      )}
      {id && detail.isPending && (
        <p role="status">{t("Loading observations…")}</p>
      )}
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
        {t(
          "Last hour - History clears on restart. Gaps mean missing samples; — means unavailable.",
        )}
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
  const { t } = useTranslation();
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
          <summary>{t("Device")}</summary>
          <p>
            {env.name} - {env.transport} - {env.id}
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
                .join(" - ")}
            </p>
          )}
          <p>
            {t(
              "System and cgroup metrics cover the whole device, not a single run.",
            )}
          </p>
          <p>
            {t(
              "App collection: every {{collection}}s - native sampling: {{sampling}}",
              {
                collection: env.collection_interval_seconds ?? 60,
                sampling:
                  env.sample_interval_ms == null
                    ? t("unavailable")
                    : t("every {{seconds}}s", {
                        seconds: env.sample_interval_ms / 1000,
                      }),
              },
            )}
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
          <strong>{t(states[env.status])}</strong>
          <span>
            {env.age_ms == null
              ? t("No sample received")
              : t("Sample age {{seconds}}s", {
                  seconds: Math.floor(env.age_ms / 1000),
                })}
          </span>
        </div>
      </div>
      <div className={styles.gauges}>
        <div>
          <span>{t("System CPU")}</span>
          <strong>{percent(system?.cpu_busy_percent)}</strong>
          <small>
            {t("{{count}} logical CPUs", {
              count: system?.logical_cpu_count ?? "—",
            })}
          </small>
        </div>
        <div>
          <span>{t("System memory used")}</span>
          <strong>{bytes(memory)}</strong>
          <small>
            {t("of {{total}}", { total: bytes(system?.memory_total_bytes) })}
          </small>
        </div>
        {sample?.cgroup_v2 && (
          <div>
            <span>{t("Cgroup memory")}</span>
            <strong>{bytes(sample?.cgroup_v2?.memory_current_bytes)}</strong>
            <small>
              {sample?.cgroup_v2?.memory_max?.kind === "unlimited"
                ? t("Unlimited")
                : sample?.cgroup_v2?.memory_max?.kind === "limited"
                  ? t("Limit {{limit}}", {
                      limit: bytes(sample.cgroup_v2.memory_max.bytes),
                    })
                  : t("Limit unavailable")}
            </small>
          </div>
        )}
      </div>
      <SegmentedControl
        label={t("Resource view")}
        value={view}
        onValueChange={setView}
        className={styles.views}
        options={[
          { value: "overview", label: t("Overview") },
          { value: "network", label: t("Network") },
          { value: "storage", label: t("Storage") },
          { value: "daemon", label: t("Envd") },
        ]}
      />
      {view !== "daemon" && (
        <>
          <div className={styles.chartToolbar}>
            <div className={styles.rangeControls}>
              <select
                aria-label={t("Time range")}
                value={fixedRange ? "custom" : duration}
                onChange={(event) => {
                  setDuration(Number(event.target.value));
                  selectRange(null);
                }}
              >
                {fixedRange && (
                  <option value="custom">{t("Custom range")}</option>
                )}
                <option value={15}>{t("Last 15 minutes")}</option>
                <option value={60}>{t("Last 1 hour")}</option>
              </select>
              <Button
                variant="ghost"
                size="icon"
                aria-label={t("Zoom in")}
                title={t("Zoom in")}
                disabled={range.end - range.start <= 2}
                onClick={() => selectRange(zoomRange(range, 0.5, latest))}
              >
                <MagnifyingGlassPlusIcon />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={t("Zoom out")}
                title={t("Zoom out")}
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
                  {t("Back to live")}
                </Button>
              )}
            </div>
            <span className={styles.liveState}>
              {fixedRange
                ? t("Historical range")
                : refreshing
                  ? t("Live - auto-refreshing")
                  : t("Refresh paused")}
            </span>
          </div>
          <div className={styles.chartHint}>
            <span>
              {timeLabel(range.start)} – {timeLabel(range.end)} -{" "}
              {Intl.DateTimeFormat().resolvedOptions().timeZone}
            </span>
            <span>{t("Drag to zoom - observed minute averages / peaks")}</span>
          </div>
        </>
      )}
      {view === "overview" && (
        <>
          <div className={styles.chartGrid}>
            <Trend
              {...chartProps}
              title={t("System CPU")}
              metric="cpu_busy_percent"
              history={history}
              format={percent}
            />
            <Trend
              {...chartProps}
              title={t("System memory used")}
              metric="memory_used_bytes"
              history={history}
              format={bytes}
            />
            {history.some((point) => point.cgroup_memory_bytes != null) && (
              <Trend
                {...chartProps}
                title={t("Cgroup memory")}
                metric="cgroup_memory_bytes"
                history={history}
                format={bytes}
              />
            )}
          </div>
          <MetricList
            title={t("Runtime memory")}
            values={[
              [t("Wired"), bytes(system?.memory_wired_bytes)],
              [t("Compressed"), bytes(system?.memory_compressed_bytes)],
              [t("Swap used"), bytes(system?.swap_used_bytes)],
              [t("Swap capacity"), bytes(system?.swap_total_bytes)],
              [t("Committed"), bytes(system?.commit_total_bytes)],
              [t("Commit limit"), bytes(system?.commit_limit_bytes)],
              [t("System cache"), bytes(system?.memory_cache_bytes)],
            ].filter(([, value]) => value !== "—")}
          />
          <MetricList
            title={t("System counts")}
            values={[
              [t("Processes"), count(system?.process_count)],
              [t("Threads"), count(system?.thread_count)],
              [t("Handles"), count(system?.handle_count)],
              [t("Cgroup tasks"), count(sample?.cgroup_v2?.pids_current)],
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
            <summary>{t("Connections and totals")}</summary>
            <div className={styles.resourceSection}>
              <MetricList
                title={t("Sockets")}
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
                title={t("TCP sockets")}
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
            <summary>{t("Disk totals")}</summary>
            <CounterTable sample={sample} kind="disk" />
          </details>
          {filesystems.length > 0 && (
            <section>
              <h3>{t("Filesystems")}</h3>
              <p className={styles.note}>
                {t(
                  "Whole-filesystem capacity. Bind mounts and shared APFS space must not be summed.",
                )}
              </p>
              {sample?.local_filesystems?.truncated && (
                <p role="status">
                  {t("Local filesystem discovery reached its 16-volume limit.")}
                </p>
              )}
              <div className={styles.filesystems}>
                {filesystems.map(([name, fs]) => (
                  <div key={name}>
                    <div className={styles.filesystemHeading}>
                      <strong>{name}</strong>
                      <span>
                        {t("{{available}} available / {{total}}", {
                          available: bytes(fs.metrics?.available_bytes),
                          total: bytes(fs.metrics?.total_bytes),
                        })}
                      </span>
                    </div>
                    {fs.metrics && fs.metrics.total_bytes > 0 && (
                      <meter
                        aria-label={t("{{name}} used capacity", { name })}
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
            title={t("Runtime resources")}
            values={[
              [t("Sessions"), count(sample?.daemon?.sessions)],
              [t("Command groups"), count(sample?.daemon?.processes)],
              [t("File transfers"), count(sample?.daemon?.transfers)],
              [t("Connections"), count(sample?.daemon?.connections)],
              [
                t("Output buffer"),
                bytes(sample?.daemon?.retained_output_bytes),
              ],
            ]}
          />
          <p className={styles.note}>
            {t(
              "Running and detached sessions, command groups still running or cleaning up, active transfers, connections and saved output. Timed-out checks are shown as unavailable.",
            )}
          </p>
        </>
      )}
      {omittedRateSources > 0 && (view === "network" || view === "storage") && (
        <p role="status" className={styles.note}>
          {t(
            "{{count}} sources exceed the history limit of 64 interfaces and 64 disks per Device.",
            { count: omittedRateSources },
          )}
        </p>
      )}
      {sample && (
        <DebugOnly>
          <details>
            <summary>{t("Latest check")}</summary>
            <p className={styles.note}>
              {t(
                "Values use their original units and are shown separately for each device.",
              )}
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
        </DebugOnly>
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
  const { t } = useTranslation();
  const [selected, select] = useState("");
  const candidates = resources.filter((item) => item.kind === kind);
  const source =
    candidates.find((item) => item.name === selected) ?? candidates[0];
  const fields = rateFields[kind];
  return (
    <section className={styles.resourceSection}>
      <div className={styles.toolbar}>
        <label>
          {kind === "network" ? t("Interface") : t("Disk")}
          <select
            aria-label={
              kind === "network" ? t("History interface") : t("History disk")
            }
            value={source?.name ?? ""}
            onChange={(event) => select(event.target.value)}
            disabled={!source}
          >
            {!source && <option value="">{t("No observations")}</option>}
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
            title={t(label)}
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
        {t(
          "Rate between samples for the selected source. Other sources aren't added.",
        )}
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
  const { t } = useTranslation();
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
      <h3>{network ? t("Interface counters") : t("Disk counters")}</h3>
      <p className={styles.note}>{t("Running totals, not current rates.")}</p>
      {truncated && (
        <p role="status">{t("Collection reached its 64-source limit.")}</p>
      )}
      {rows.length ? (
        <div className={styles.counterTable}>
          <table>
            <thead>
              <tr>
                <th scope="col">{network ? t("Interface") : t("Disk")}</th>
                <th scope="col">{network ? t("Received") : t("Read")}</th>
                <th scope="col">{network ? t("Transmitted") : t("Written")}</th>
                <th scope="col">{network ? t("RX packets") : t("Read ops")}</th>
                <th scope="col">
                  {network ? t("TX packets") : t("Write ops")}
                </th>
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
          {network
            ? t("No interface counters available.")
            : t("No disk counters available.")}
        </p>
      )}
    </section>
  );
}
