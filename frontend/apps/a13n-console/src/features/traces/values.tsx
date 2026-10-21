import { StatusPill, type StatusPillVariant } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { StatePill } from "../../shared/feedback";
import { formatDuration } from "../../shared/time-range";
import { UNKNOWN } from "../../shared/unknown";
import styles from "./traces.module.css";

/** Label above value, the one fact anatomy the traces surfaces share. */
export function Fact({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className={styles.fact}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Presentation only: missing end time does not imply running execution. */
export function durationMs(observation: Schema["Span"]): number | null {
  if (!observation.ended_at) return null;
  const value =
    Date.parse(observation.ended_at) - Date.parse(observation.started_at);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function Duration({ observation }: { observation: Schema["Span"] }) {
  const { i18n } = useTranslation();
  const value = durationMs(observation);
  return (
    <>
      {value === null ? UNKNOWN : formatDuration(value, i18n.resolvedLanguage)}
    </>
  );
}

/** Span status is a lifecycle state and keeps the console's status pill. */
export function TelemetryStatus({
  observation,
}: {
  observation: Schema["Span"];
}) {
  return <StatePill state={observation.status} />;
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

/** Informational levels stay neutral; only warnings and errors are tinted. */
const levelVariants: Record<string, StatusPillVariant> = {
  critical: "danger",
  error: "danger",
  fatal: "danger",
  warn: "warning",
  warning: "warning",
};

/** A log level in the same pill as every other state. */
export function LevelPill({ level }: { level: string | null }) {
  const { t } = useTranslation();
  if (!level) return <>{UNKNOWN}</>;
  const normalized = level.toLowerCase();
  const label = levelLabels[normalized];
  return (
    <StatusPill variant={levelVariants[normalized] ?? "neutral"}>
      {label ? t(label) : level}
    </StatusPill>
  );
}
