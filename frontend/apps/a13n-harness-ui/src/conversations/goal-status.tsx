import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./composer-status.module.css";

const labels: Record<NonNullable<Schema<"GoalView">["status"]>, string> = {
  working: "Working",
  checking: "Checking",
  auditing: "Fresh audit",
  suspended: "Waiting for response",
  verified: "Agent-verified",
  max_iterations: "Iteration limit",
  cancelled: "Cancelled",
  error: "Error",
  unverified_stop: "Unverified stop",
};

export function GoalStatus({
  goal,
  onRetry,
}: {
  goal: Schema<"GoalView">;
  onRetry?: () => void;
}) {
  const status = goal.status ?? "working";
  const active = ["working", "checking", "auditing", "suspended"].includes(
    status,
  );
  const incomplete = !active && goal.status !== "verified";
  return (
    <Popover>
      <PopoverTrigger className={styles.metric} aria-label="Goal details">
        Goal{" "}
        <strong>
          {labels[status]} · {goal.iteration}/{goal.max_iterations}
        </strong>
        {goal.needs_restore_audit && " · Audit pending"}
      </PopoverTrigger>
      <PopoverPopup side="top" align="start" className={styles.popup}>
        <PopoverTitle>Goal · {labels[status]}</PopoverTitle>
        <p className={styles.goalObjective}>{goal.objective}</p>
        <p>
          {goal.iteration} of {goal.max_iterations} additional continuations
          used.
        </p>
        {goal.needs_restore_audit && (
          <p>A fresh audit is required after context restoration.</p>
        )}
        {goal.status === "auditing" && (
          <p>
            The Agent is checking current evidence after context restoration.
          </p>
        )}
        {goal.status === "suspended" && (
          <p>Answer the pending question or approval to continue this Goal.</p>
        )}
        {incomplete ? (
          <p>
            The task may be incomplete. Review the output before starting
            another Goal.
          </p>
        ) : (
          <p>
            The same Agent checks the objective; this is not independent
            verification.
          </p>
        )}
        <p>
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
  );
}
