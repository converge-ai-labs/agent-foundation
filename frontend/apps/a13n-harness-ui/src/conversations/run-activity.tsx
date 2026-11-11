import { useTranslation } from "../i18n";
import { LogoAnimation } from "a13n-ui";
import type { Schema } from "../transport/client";
import type { Submission } from "./draft";
import type { FocusDisplay } from "./stream";
import styles from "./conversation.module.css";

export function RecoveryText({
  recovery,
}: {
  recovery: NonNullable<FocusDisplay["recovery"]>;
}) {
  const { t } = useTranslation();
  const label =
    recovery.state === "retrying"
      ? t("Reconnecting to model…")
      : recovery.state === "resumed"
        ? t("Model connection restored")
        : t("Model reconnection ended");
  return `${label} - ${t("Retry count", { count: recovery.retries })}`;
}

export function RunActivity({
  activity,
  submission,
  recovery,
  operation,
}: {
  activity?: Schema<"RootActivityView">;
  submission: Submission;
  recovery?: FocusDisplay["recovery"];
  operation?: Schema<"RootOperationView">;
}) {
  const { t } = useTranslation();
  const working =
    recovery && recovery.state !== "resumed" ? (
      <RecoveryText recovery={recovery} />
    ) : (
      t("Working…")
    );
  let label: React.ReactNode;
  if (submission.kind === "pending")
    label =
      submission.action === "steer" ? t("Sending instruction…") : t("Sending…");
  else if (
    operation &&
    operation.status !== "preparing" &&
    operation.status !== "running" &&
    operation.receipt.receipt_id === activity?.receipt_id
  )
    return null;
  else if (activity?.state === "preparing") label = t("Preparing…");
  else if (activity?.state === "running") label = working;
  else if (
    submission.kind === "accepted" &&
    submission.action !== "steer" &&
    (!operation || operation.receipt.receipt_id !== submission.receipt)
  )
    label = t("Preparing…");
  else if (operation?.status === "preparing") label = t("Preparing…");
  else if (operation?.status === "running") label = working;
  if (!label) return null;
  return (
    <div className={styles.runActivity} role="status" aria-live="polite">
      <LogoAnimation size={18} />
      <span>{label}</span>
    </div>
  );
}
