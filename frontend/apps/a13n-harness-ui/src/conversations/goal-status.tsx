import {
  Check,
  CircleNotch,
  Target,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./goal-status.module.css";

const labels: Record<NonNullable<Schema<"GoalView">["status"]>, string> = {
  working: "Working",
  checking: "Checking",
  auditing: "Reviewing after context restoration",
  suspended: "Waiting for your response",
  verified: "Verified by the Agent",
  max_iterations: "Check limit reached · Not verified",
  cancelled: "Stopped · Not verified",
  error: "Error · Not verified",
  unverified_stop: "Stopped without verification",
};

export function currentGoal(
  thread: Pick<Schema<"ThreadSummary">, "goal" | "root_activity"> | undefined,
  operation?: Schema<"RootOperationView">,
) {
  // Terminal receipts may outlive a cleared Goal. A matching live receipt is
  // authoritative, including a normal submission with no Goal.
  return thread?.root_activity.state !== "inactive" &&
    thread?.root_activity.receipt_id &&
    operation?.receipt.receipt_id === thread.root_activity.receipt_id
    ? operation.goal
    : thread?.goal;
}

export function GoalStatus({
  goal,
  activity,
  onRetry,
}: {
  goal: Schema<"GoalView">;
  activity?: Schema<"RootActivityView">;
  onRetry?: () => void;
}) {
  const status = goal.status ?? "working";
  const iteration = goal.iteration ?? 0;
  const limit = goal.max_iterations ?? 10;
  const executing = ["working", "checking", "auditing"].includes(status);
  const running = executing && activity?.state === "running";
  const preparing = executing && activity?.state === "preparing";
  const incomplete =
    !executing && status !== "suspended" && status !== "verified";
  const label = preparing ? "Preparing" : labels[status];
  const Icon =
    running || preparing
      ? CircleNotch
      : status === "verified"
        ? Check
        : incomplete || status === "suspended"
          ? WarningCircle
          : Target;
  return (
    <div
      className={styles.bar}
      data-tone={
        status === "error"
          ? "danger"
          : incomplete || status === "suspended"
            ? "warning"
            : "neutral"
      }
    >
      <div className={styles.summary} role="status" aria-live="polite">
        <Icon
          size={16}
          className={running || preparing ? styles.running : undefined}
          aria-hidden="true"
        />
        <span className={styles.label}>
          Goal <span aria-hidden="true">·</span> {label}
        </span>
        {iteration > 0 && (
          <span className={styles.count}>
            {iteration} follow-up {iteration === 1 ? "check" : "checks"}
          </span>
        )}
      </div>
      <Popover>
        <PopoverTrigger className={styles.details} aria-label="Goal details">
          Details
        </PopoverTrigger>
        <PopoverPopup side="top" align="end" className={styles.popup}>
          <PopoverTitle>Goal · {label}</PopoverTitle>
          <p className={styles.objective}>{goal.objective}</p>
          <p>
            {limit > 0
              ? `Follow-up checks used: ${iteration} of ${limit}.`
              : "Automatic follow-up checks are disabled."}
          </p>
          {goal.needs_restore_audit && (
            <p>
              A fresh review is required before completion can be confirmed.
            </p>
          )}
          {status === "auditing" && (
            <p>
              The Agent is checking current evidence after context restoration.
            </p>
          )}
          {status === "suspended" && (
            <p>Respond to the pending request to continue this Goal.</p>
          )}
          {incomplete && (
            <p>
              Completion has not been confirmed. Review the output before
              starting another Goal.
            </p>
          )}
          {status === "verified" && (
            <p>
              The same Agent checked the objective; this is not independent
              verification.
            </p>
          )}
          <p className={styles.usage}>
            Goal tokens: {(goal.input_tokens ?? 0).toLocaleString()} input ·{" "}
            {(goal.output_tokens ?? 0).toLocaleString()} output
          </p>
          {incomplete && onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry}>
              Prepare new Goal
            </Button>
          )}
        </PopoverPopup>
      </Popover>
    </div>
  );
}
