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
  const { t } = useTranslation();
  const value = durationMs(observation);
  return (
    <>
      {value === null
        ? t("Unavailable")
        : `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })} ms`}
    </>
  );
}

export function TelemetryStatus({
  observation,
}: {
  observation: Schema["Observation"];
}) {
  return <StateBadge state={observation.status ?? "unavailable"} />;
}

export function Severity({ level }: { level: string | null }) {
  const { t } = useTranslation();
  if (level === null) return <>{t("Unavailable")}</>;
  const variant = ["error", "fatal", "critical"].includes(level)
    ? "error"
    : ["warn", "warning"].includes(level)
      ? "warning"
      : "secondary";
  return <Badge variant={variant}>{level}</Badge>;
}
