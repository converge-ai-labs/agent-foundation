import { StatusPill, type StatusPillVariant } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { StatePill } from "../../shared/feedback";
import styles from "./traces.module.css";

/** Presentation only: missing end time does not imply running execution. */
export function durationMs(observation: Schema["Observation"]): number | null {
  if (!observation.ended_at) return null;
  const value =
    Date.parse(observation.ended_at) - Date.parse(observation.started_at);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function Duration({
  observation,
}: {
  observation: Schema["Observation"];
}) {
  const value = durationMs(observation);
  return (
    <>
      {value === null
        ? "-"
        : `${(value / 1000).toLocaleString(undefined, { maximumFractionDigits: 6 })} s`}
    </>
  );
}

/**
 * Span status is a lifecycle state, so it keeps the console's one status
 * affordance. Log level is a severity scale and reads as a `TracePill`.
 */
export function TelemetryStatus({
  observation,
}: {
  observation: Schema["Observation"];
}) {
  return observation.status === null ? (
    <>-</>
  ) : (
    <StatePill state={observation.status} />
  );
}

const levelLabels: Record<string, string> = {
  trace: "Trace level",
  debug: "Debug",
  default: "Info",
  info: "Info",
  information: "Info",
  notice: "Notice",
  warn: "Warning",
  warning: "Warning",
  error: "Error",
  critical: "Critical",
  fatal: "Fatal",
};

const levelVariants: Record<string, StatusPillVariant> = {
  critical: "danger",
  debug: "neutral",
  default: "info",
  error: "danger",
  fatal: "danger",
  info: "info",
  information: "info",
  notice: "info",
  trace: "neutral",
  warn: "warning",
  warning: "warning",
};

/**
 * Severity tag for a log level. It carries the severity hue and drops the
 * status dot so a level never reads as a span's lifecycle state.
 */
export function TracePill({ level }: { level: string | null }) {
  const { t } = useTranslation();
  if (!level) return <>-</>;
  const normalized = level.toLowerCase();
  const label = levelLabels[normalized];
  return (
    <span title={level} className={styles.levelPill}>
      <StatusPill variant={levelVariants[normalized] ?? "neutral"}>
        {label ? t(label) : level}
      </StatusPill>
    </span>
  );
}
