import { CircleNotch } from "@phosphor-icons/react";
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
  let label: string | undefined;
  if (submission.kind === "pending")
    label = submission.action === "steer" ? "Sending instruction…" : "Sending…";
  else if (
    operation &&
    operation.status !== "preparing" &&
    operation.status !== "running" &&
    operation.receipt.receipt_id === activity?.receipt_id
  )
    return null;
  else if (activity?.state === "preparing") label = "Preparing…";
  else if (activity?.state === "running") label = "Working…";
  else if (
    submission.kind === "accepted" &&
    submission.action !== "steer" &&
    (!operation || operation.receipt.receipt_id !== submission.receipt)
  )
    label = "Preparing…";
  else if (operation?.status === "preparing") label = "Preparing…";
  else if (operation?.status === "running") label = "Working…";
  if (!label) return null;
  return (
    <div className={styles.runActivity} role="status" aria-live="polite">
      <CircleNotch
        size={14}
        className={styles.threadRunning}
        aria-hidden="true"
      />
      <span>{label}</span>
    </div>
  );
}
