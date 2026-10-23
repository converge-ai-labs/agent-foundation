import type { TFunction } from "i18next";
import type { Panel, PanelQuery } from "./api";

export type Metric = Extract<PanelQuery, { kind: "metric" }>["metric"];
// Exhaustive against the generated API union: a new backend metric needs a
// `metric.<name>` translation.
const metricCatalog: Record<Metric, null> = {
  system_cpu_busy_ratio: null,
  system_memory_used_bytes: null,
  system_memory_total_bytes: null,
  system_memory_available_bytes: null,
  system_memory_cache_bytes: null,
  system_memory_wired_bytes: null,
  system_memory_compressed_bytes: null,
  system_swap_used_bytes: null,
  system_swap_total_bytes: null,
  system_commit_total_bytes: null,
  system_commit_limit_bytes: null,
  system_processes: null,
  system_threads: null,
  system_handles: null,
  cgroup_memory_current_bytes: null,
  cgroup_pids_current: null,
  tcp_connections: null,
  udp_sockets: null,
  daemon_sessions: null,
  daemon_processes: null,
  daemon_connections: null,
  daemon_transfers: null,
  daemon_retained_output_bytes: null,
  network_received_bytes_per_second: null,
  network_transmitted_bytes_per_second: null,
  disk_read_bytes_per_second: null,
  disk_written_bytes_per_second: null,
  disk_read_operations_per_second: null,
  disk_write_operations_per_second: null,
  cgroup_io_read_bytes_per_second: null,
  cgroup_io_written_bytes_per_second: null,
  cgroup_io_read_operations_per_second: null,
  cgroup_io_write_operations_per_second: null,
};
export const metrics = Object.keys(metricCatalog) as Metric[];

export const metricName = (t: TFunction, metric: string) =>
  t(`metric.${metric}`, { defaultValue: metric });

/** The built-in dashboard reads in the console language; names people type never change. */
export const builtinName = (t: TFunction, name: string) =>
  t(`dashboard.${name}`, { defaultValue: name });

export const unitOptions = (
  t: TFunction,
): { value: NonNullable<Panel["unit"]>; label: string }[] => [
  { value: "auto", label: t("Automatic") },
  { value: "ratio", label: t("Ratio") },
  { value: "bytes", label: t("Bytes") },
  { value: "bytes_per_second", label: t("Bytes per second") },
  { value: "operations_per_second", label: t("Operations per second") },
  { value: "count", label: t("Count") },
  { value: "seconds", label: t("Seconds") },
];

export const statusLabels = (t: TFunction): Record<string, string> =>
  Object.assign(Object.create(null) as Record<string, string>, {
    waiting: t("Waiting"),
    ready: t("Ready"),
    stale: t("Stale"),
    offline: t("Offline"),
    enabled: t("Enabled"),
    disabled: t("Disabled"),
    unsupported: t("Unsupported"),
    not_ready: t("Not ready"),
    capacity: t("At capacity"),
    reserved: t("Reserved"),
    creating: t("Creating"),
    starting: t("Starting"),
    stopped: t("Stopped"),
    stopping: t("Stopping"),
    degraded: t("Degraded"),
    failed: t("Failed"),
    deleting: t("Deleting"),
    deleted: t("Deleted"),
    pending: t("Pending"),
    not_configured: t("Not configured"),
    reload_requested: t("Reload requested"),
    discovery_failed: t("Discovery failed"),
    changed: t("Changed"),
  });
