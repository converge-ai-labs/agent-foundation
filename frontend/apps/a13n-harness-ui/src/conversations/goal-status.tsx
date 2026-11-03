import { useTranslation } from "../i18n";
import { useId, useState } from "react";
import {
  ArrowElbowDownLeft,
  CaretDown,
  Check,
  Target,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  Button,
  LogoAnimation,
  Popover,
  PopoverClose,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./goal-status.module.css";

type GoalStatusName = NonNullable<Schema<"GoalView">["status"]>;

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
  const { t } = useTranslation();
  const phases: Record<
    GoalStatusName,
    { label: string; title: string; detail: string }
  > = {
    working: {
      label: t("Working"),
      title: t("Working on the objective"),
      detail: t("The Agent is carrying out the requested work."),
    },
    checking: {
      label: t("Checking"),
      title: t("Checking completion"),
      detail: t("The Agent is checking the result against the objective."),
    },
    auditing: {
      label: t("Reviewing"),
      title: t("Reviewing restored context"),
      detail: t(
        "The Agent is checking current evidence after context restoration.",
      ),
    },
    suspended: {
      label: t("Needs input"),
      title: t("Your response is needed"),
      detail: t(
        "Respond to the pending request in the chat to continue this Goal.",
      ),
    },
    verified: {
      label: t("Verified"),
      title: t("Verified by the Agent"),
      detail: t(
        "The Agent reported that the objective was met after its checks. This is same-Agent verification.",
      ),
    },
    blocked: {
      label: t("Blocked"),
      title: t("Blocked before verification"),
      detail: t(
        "The Agent ended Goal mode because further progress requires an external event, access, or your decision. A registered notification can continue the conversation in normal mode.",
      ),
    },
    max_iterations: {
      label: t("Not verified"),
      title: t("Completion not verified"),
      detail: t("The follow-up check limit was reached."),
    },
    cancelled: {
      label: t("Not verified"),
      title: t("Stopped before verification"),
      detail: t("Execution was cancelled before completion was confirmed."),
    },
    error: {
      label: t("Not verified"),
      title: t("Completion not verified"),
      detail: t("Execution ended with an error. Review the error in the chat."),
    },
    unverified_stop: {
      label: t("Not verified"),
      title: t("Stopped without verification"),
      detail: t(
        "Execution stopped without confirming that the objective was met.",
      ),
    },
  };
  const status = goal.status ?? "working";
  const iteration = goal.iteration ?? 0;
  const limit = goal.max_iterations ?? 10;
  const executing = ["working", "checking", "auditing"].includes(status);
  const preparing = executing && activity?.state === "preparing";
  const moving = executing && (preparing || activity?.state === "running");
  const incomplete =
    !executing && status !== "suspended" && status !== "verified";
  const phase = phases[status];
  const label = preparing ? t("Preparing") : phase.label;
  const Icon =
    status === "verified"
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
        aria-label={t("Inspect Goal: {{label}}", {
          label: `${label}${status === "checking" && !preparing && iteration > 0 ? ` ${iteration}` : ""}`,
        })}
        data-tone={tone}
      >
        {moving ? <LogoAnimation size={16} /> : <Icon aria-hidden="true" />}
        <span className={styles.name}>{t("Goal")}</span>
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
          <PopoverTitle>{t("Goal")}</PopoverTitle>
          <PopoverClose
            render={<Button variant="ghost" size="icon-sm" />}
            aria-label={t("Close Goal")}
          >
            <X />
          </PopoverClose>
        </div>
        <div className={styles.outcome} data-tone={tone}>
          <h3>{preparing ? t("Preparing execution") : phase.title}</h3>
          <p>
            {preparing
              ? t("The Goal is being prepared for execution.")
              : status === "max_iterations" && limit <= 0
                ? t("Automatic follow-up checks are disabled.")
                : phase.detail}
          </p>
          {goal.needs_restore_audit && status !== "auditing" && (
            <p>
              {t(
                "A fresh review is required before completion can be confirmed.",
              )}
            </p>
          )}
        </div>
        <Objective key={goal.objective} text={goal.objective} />
        <dl className={styles.metrics}>
          <div>
            <dt>{t("Follow-up checks")}</dt>
            <dd>{limit > 0 ? `${iteration} / ${limit}` : t("Disabled")}</dd>
          </div>
          <div>
            <dt>{t("Tokens")}</dt>
            <dd>
              {t("{{input}} in · {{output}} out", {
                input: (goal.input_tokens ?? 0).toLocaleString(),
                output: (goal.output_tokens ?? 0).toLocaleString(),
              })}
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
            {t("Use objective in composer")}
          </PopoverClose>
        )}
      </PopoverPopup>
    </Popover>
  );
}

function Objective({ text }: { text: string }) {
  const { t } = useTranslation();
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
        {t("Objective")} <CaretDown aria-hidden="true" />
      </button>
      <p id={id} data-expanded={expanded}>
        {text}
      </p>
    </div>
  );
}
