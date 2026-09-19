import { StatusPill, type StatusPillVariant } from "a13n-ui";
import { useTranslation } from "react-i18next";

const variants: Record<string, StatusPillVariant> = {
  action_required: "warning",
  active: "success",
  check_failed: "danger",
  completed: "success",
  enabled: "success",
  error: "danger",
  failed: "danger",
  interrupted: "danger",
  needs_verification: "warning",
  pending: "warning",
  connecting: "warning",
  online: "success",
  paired: "success",
  revoked: "danger",
  queued: "warning",
  ready: "success",
  receiving: "success",
  reception_off: "neutral",
  running: "info",
  succeeded: "success",
  waiting: "warning",
};

/** Maps a domain state to a semantic pill; the label always names the state. */
export function StatePill({ state, label }: { state: string; label?: string }) {
  const { t } = useTranslation();
  return (
    <StatusPill variant={variants[state] ?? "neutral"} data-state={state}>
      {label ??
        t(`state.${state}`, { defaultValue: state.replaceAll("_", " ") })}
    </StatusPill>
  );
}
