import { useTranslation } from "../i18n";
import { LogoAnimation } from "a13n-ui";
import type { Schema } from "../transport/client";
import type { Submission } from "./draft";
import styles from "./conversation.module.css";

export function RunActivity({
  activity,
  submission,
  operation,
}: {
  activity?: Schema<"RootActivityView">;
  submission: Submission;
  operation?: Schema<"RootOperationView">;
}) {
  const { t } = useTranslation();
  let label: string | undefined;
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
  else if (activity?.state === "running") label = t("Working…");
  else if (
    submission.kind === "accepted" &&
    submission.action !== "steer" &&
    (!operation || operation.receipt.receipt_id !== submission.receipt)
  )
    label = t("Preparing…");
  else if (operation?.status === "preparing") label = t("Preparing…");
  else if (operation?.status === "running") label = t("Working…");
  if (!label) return null;
  return (
    <div className={styles.runActivity} role="status" aria-live="polite">
      <LogoAnimation size={18} />
      <span>{label}</span>
    </div>
  );
}
