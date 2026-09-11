import { Badge } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { StateBadge } from "../../shared/feedback";

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

export function TelemetryStatus({
  observation,
}: {
  observation: Schema["Observation"];
}) {
  return observation.status === null ? (
    <>-</>
  ) : (
    <StateBadge state={observation.status} />
  );
}

export function Level({ level }: { level: string | null }) {
  const { t } = useTranslation();
  if (!level) return <>-</>;
  const normalized = level.toLowerCase();
  const labels: Record<string, string> = {
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
  const variant = ["error", "fatal", "critical"].includes(normalized)
    ? "error"
    : ["warn", "warning"].includes(normalized)
      ? "warning"
      : "secondary";
  return (
    <span title={level}>
      <Badge variant={variant}>
        {labels[normalized] ? t(labels[normalized]) : level}
      </Badge>
    </span>
  );
}
