import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ActivityIcon } from "@phosphor-icons/react";
import { Button, ModalFrame } from "a13n-ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { Trend, bytes, percent } from "./environment-trend";
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
      description="App-wide Device observations. System and cgroup usage belongs to the Device, not a directory or Run."
    >
      {open && <MonitorContent />}
    </ModalFrame>
  );
}

export function MonitorContent() {
  const { client } = useTransport();
  const [selected, select] = useState("");
  const catalog = useQuery({
    queryKey: ["environment-monitoring"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/environment-monitoring", { signal })),
    refetchInterval: 10000,
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
    refetchInterval: 10000,
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
            <span>Last 24 hours · minute averages / peaks</span>
          </div>
          {catalog.data.omitted > 0 && (
            <p role="status">
              {catalog.data.omitted} Devices exceed the {catalog.data.capacity}
              -Device monitoring limit.
            </p>
          )}
          {!environments.some((item) => item.transport === "stdio") && (
            <p className={styles.note}>
              Local Sandbox: not started. Monitoring attaches to existing
              daemons only. Full Control and non-Envd providers do not report
              these metrics.
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
        />
      )}
      <p className={styles.note}>
        History is kept in this App's memory and clears on restart. Missing
        observations remain gaps; unavailable values are shown as —.
      </p>
    </div>
  );
}

function Observation({
  observation: env,
  history,
}: {
  observation: Schema<"EnvironmentObservation">;
  history: Schema<"HistoryPoint">[];
}) {
  const sample = env.sample;
  const system = sample?.system;
  const info = env.system_info;
  const roots = env.roots ?? [];
  const filesystems = Object.entries(sample?.filesystems ?? {});
  const memory =
    system?.memory_total_bytes != null && system.memory_available_bytes != null
      ? system.memory_total_bytes - system.memory_available_bytes
      : null;
  return (
    <>
      <div className={styles.identity}>
        <div>
          <h3>{env.name}</h3>
          <span>
            {env.transport === "stdio" ? "Local Sandbox" : "Remote Device"} ·{" "}
            {env.id}
          </span>
        </div>
        <div className={styles.freshness}>
          <strong>{states[env.status]}</strong>
          <span>
            {env.age_ms == null
              ? "No sample received"
              : `Sample age ${Math.floor(env.age_ms / 1000)}s`}
          </span>
        </div>
      </div>
      {info && (
        <p className={styles.note}>
          {[info.os, info.os_version, info.architecture, info.cpu_model]
            .filter(Boolean)
            .join(" · ")}
          {info.kernel_version && ` · Kernel ${info.kernel_version}`}
        </p>
      )}
      {roots.length > 0 && (
        <details>
          <summary>Working roots ({roots.length})</summary>
          <ul>
            {roots.map((root) => (
              <li key={root}>{root}</li>
            ))}
          </ul>
        </details>
      )}
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
      </div>
      <Trend
        title="System CPU"
        metric="cpu_busy_percent"
        history={history}
        format={percent}
      />
      <Trend
        title="System memory used"
        metric="memory_used_bytes"
        history={history}
        format={bytes}
      />
      {history.some((point) => point.cgroup_memory_bytes != null) && (
        <Trend
          title="Cgroup memory"
          metric="cgroup_memory_bytes"
          history={history}
          format={bytes}
        />
      )}
      {filesystems.length > 0 && (
        <section>
          <h3>Filesystems</h3>
          <div className={styles.filesystems}>
            {filesystems.map(([name, fs]) => (
              <div key={name}>
                <strong>{name}</strong>
                <span>
                  {bytes(fs.metrics?.available_bytes)} available /{" "}
                  {bytes(fs.metrics?.total_bytes)}
                </span>
              </div>
            ))}
          </div>
        </section>
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
