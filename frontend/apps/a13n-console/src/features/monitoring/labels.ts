import type { TFunction } from "i18next";
import type { Panel } from "./api";

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
