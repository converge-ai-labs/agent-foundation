import { useId, useState } from "react";
import {
  ArrowElbowDownLeft,
  CaretDown,
  Check,
  CircleNotch,
  Target,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  Button,
  Popover,
  PopoverClose,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./goal-status.module.css";

type GoalStatusName = NonNullable<Schema<"GoalView">["status"]>;
const phases: Record<
  GoalStatusName,
  { label: string; title: string; detail: string }
> = {
  working: {
    label: "Working",
    title: "Working on the objective",
    detail: "The Agent is carrying out the requested work.",
  },
  checking: {
    label: "Checking",
    title: "Checking completion",
    detail: "The Agent is checking the result against the objective.",
  },
  auditing: {
    label: "Reviewing",
    title: "Reviewing restored context",
    detail: "The Agent is checking current evidence after context restoration.",
  },
  suspended: {
    label: "Needs input",
    title: "Your response is needed",
    detail:
      "Respond to the pending request in the conversation to continue this Goal.",
  },
  verified: {
    label: "Verified",
    title: "Verified by the Agent",
    detail:
      "The Agent reported that the objective was met after its checks. This is same-Agent verification.",
  },
  blocked: {
    label: "Blocked",
    title: "Blocked before verification",
    detail:
      "The Agent ended Goal mode because further progress requires an external event, access, or your decision. A registered notification can continue the conversation in normal mode.",
  },
  max_iterations: {
    label: "Not verified",
    title: "Completion not verified",
    detail: "The follow-up check limit was reached.",
  },
  cancelled: {
    label: "Not verified",
    title: "Stopped before verification",
    detail: "Execution was cancelled before completion was confirmed.",
  },
  error: {
    label: "Not verified",
    title: "Completion not verified",
    detail:
      "Execution ended with an error. Review the error in the conversation.",
  },
  unverified_stop: {
    label: "Not verified",
    title: "Stopped without verification",
    detail: "Execution stopped without confirming that the objective was met.",
  },
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
  open,
  onOpenChange,
  onUseObjective,
}: {
  goal: Schema<"GoalView">;
  activity?: Schema<"RootActivityView">;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onUseObjective?: () => void;
}) {
  const status = goal.status ?? "working";
  const iteration = goal.iteration ?? 0;
  const limit = goal.max_iterations ?? 10;
  const executing = ["working", "checking", "auditing"].includes(status);
  const preparing = executing && activity?.state === "preparing";
  const moving = executing && (preparing || activity?.state === "running");
  const incomplete =
    !executing && status !== "suspended" && status !== "verified";
  const phase = phases[status];
  const label = preparing ? "Preparing" : phase.label;
  const Icon = moving
    ? CircleNotch
    : status === "verified"
      ? Check
      : incomplete || status === "suspended"
        ? WarningCircle
        : Target;
  const tone =
    status === "error"
      ? "danger"
      : incomplete || status === "suspended"
        ? "warning"
        : "neutral";
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger
        render={<Button variant="ghost" size="sm" />}
        className={styles.trigger}
        aria-label={`Inspect Goal: ${label}${status === "checking" && !preparing && iteration > 0 ? ` ${iteration}` : ""}`}
        data-tone={tone}
      >
        <Icon
          className={moving ? styles.running : undefined}
          aria-hidden="true"
        />
        <span className={styles.name}>Goal</span>
        <span className={styles.phase} role="status" aria-live="polite">
          <span aria-hidden="true">· </span>
          {label}
          {status === "checking" && !preparing && iteration > 0 && (
            <> {iteration}</>
          )}
        </span>
      </PopoverTrigger>
      <PopoverPopup
        side="top"
        align="start"
        sideOffset={10}
        className={styles.popup}
      >
        <div className={styles.heading}>
          <PopoverTitle>Goal</PopoverTitle>
          <PopoverClose
            render={<Button variant="ghost" size="icon-sm" />}
            aria-label="Close Goal"
          >
            <X />
          </PopoverClose>
        </div>
        <div className={styles.outcome} data-tone={tone}>
          <h3>{preparing ? "Preparing execution" : phase.title}</h3>
          <p>
            {preparing
              ? "The Goal is being prepared for execution."
              : status === "max_iterations" && limit <= 0
                ? "Automatic follow-up checks are disabled."
                : phase.detail}
          </p>
          {goal.needs_restore_audit && status !== "auditing" && (
            <p>
              A fresh review is required before completion can be confirmed.
            </p>
          )}
        </div>
        <Objective key={goal.objective} text={goal.objective} />
        <dl className={styles.metrics}>
          <div>
            <dt>Follow-up checks</dt>
            <dd>{limit > 0 ? `${iteration} / ${limit}` : "Disabled"}</dd>
          </div>
          <div>
            <dt>Tokens</dt>
            <dd>
              {(goal.input_tokens ?? 0).toLocaleString()} in ·{" "}
              {(goal.output_tokens ?? 0).toLocaleString()} out
            </dd>
          </div>
        </dl>
        {incomplete && onUseObjective && (
          <PopoverClose
            render={<Button variant="ghost" size="sm" />}
            className={styles.reuse}
            onClick={onUseObjective}
          >
            <ArrowElbowDownLeft />
            Use objective in composer
          </PopoverClose>
        )}
      </PopoverPopup>
    </Popover>
  );
}

function Objective({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  return (
    <div className={styles.objective}>
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={id}
        onClick={() => setExpanded(!expanded)}
      >
        Objective <CaretDown aria-hidden="true" />
      </button>
      <p id={id} data-expanded={expanded}>
        {text}
      </p>
    </div>
  );
}
