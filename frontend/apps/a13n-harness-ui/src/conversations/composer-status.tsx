import { reasoningModeLabel } from "./reasoning-mode-picker";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { GoalStatus } from "./goal-status";
import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
} from "a13n-ui";
import { useOperation, useThreads } from "./queries";
import { ErrorNotice } from "../shell/ui";
import {
  elapsedTime,
  usageSummary,
  useContextUsage,
  useThreadUsage,
} from "./usage";
import { ContextDetails, UsageDetails } from "./usage-details";
import styles from "./composer-status.module.css";

export function ComposerStatus({
  threadId,
  receipt,
  busy = false,
  liveTokens,
  savedGoal,
  onRetryGoal,
}: {
  threadId: string;
  receipt?: string | null;
  busy?: boolean;
  liveTokens?: number;
  savedGoal?: Schema<"GoalView"> | null;
  onRetryGoal?: (objective: string) => void;
}) {
  const activity = useThreads(threadId, undefined, true, { enabled: !receipt });
  const observed = useOperation(threadId, receipt);
  const operation =
    observed.data ??
    activity.data?.pages
      .flatMap((page) => page.rows)
      .find((row) => row.thread.thread_id === threadId)?.latest_operation;
  // Terminal receipts remain inspectable even after their working state is reset.
  const goal = busy ? (operation?.goal ?? savedGoal) : savedGoal;
  const active =
    busy ||
    operation?.status === "running" ||
    operation?.status === "preparing";
  const { client } = useTransport();
  const inspection = useQuery({
    queryKey: ["thread", threadId, "configuration"],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/configuration", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
  const captured = inspection.data;
  const capturedAgent =
    active &&
    (captured?.capture_source !== "active_operation" ||
      captured.receipt_id !== operation?.receipt.receipt_id)
      ? undefined
      : captured?.captured?.agent;
  const fast = capturedAgent?.fast;
  const fastLabel =
    fast === "on"
      ? "On"
      : fast === "off"
        ? "Off"
        : fast === "default"
          ? "Default"
          : "—";
  const usage = useThreadUsage(threadId);
  const context = useContextUsage(threadId);
  const [now, setNow] = useState(Date.now);
  const [detail, setDetail] = useState<string | null>(null);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, operation?.receipt.receipt_id]);
  const tokens = liveTokens ?? context.data?.latest_request_tokens;
  const summary = usageSummary(
    usage.data,
    context.data
      ? { ...context.data, latest_request_tokens: tokens }
      : undefined,
  );
  const stale = !!(
    usage.error ||
    inspection.error ||
    context.error ||
    observed.error ||
    activity.error
  );
  return (
    <div className={styles.bar}>
      <div className={styles.metrics}>
        {goal && (
          <GoalStatus
            goal={goal}
            onRetry={
              onRetryGoal ? () => onRetryGoal(goal.objective) : undefined
            }
          />
        )}
        {(
          [
            ["Tokens", summary.tokens, "Conversation usage"],
            ["Context", summary.context, "Context usage"],
            ["Cost", summary.cost, "Conversation usage"],
            ["Cache", summary.cache, "Conversation usage"],
          ] as const
        ).map(([label, value, title]) => (
          <Popover
            key={label}
            open={detail === label}
            onOpenChange={(open) =>
              setDetail((current) =>
                open ? label : current === label ? null : current,
              )
            }
          >
            <PopoverTrigger
              className={styles.metric}
              aria-label={`${label} details`}
            >
              {label} <strong>{value}</strong>
            </PopoverTrigger>
            <PopoverPopup side="top" align="start" className={styles.popup}>
              <PopoverTitle>{title}</PopoverTitle>
              {label === "Context" ? (
                <ContextDetails
                  used={tokens}
                  capacity={context.data?.context_window}
                />
              ) : (
                <UsageDetails usage={usage.data} />
              )}
              <ErrorNotice
                error={
                  usage.error ||
                  context.error ||
                  inspection.error ||
                  observed.error ||
                  activity.error
                }
              />
              <Button
                variant="ghost"
                size="sm"
                loading={usage.isFetching || context.isFetching}
                onClick={() => {
                  void usage.refetch();
                  void context.refetch();
                  void inspection.refetch();
                  if (receipt) void observed.refetch();
                  else void activity.refetch();
                }}
              >
                Refresh usage
              </Button>
            </PopoverPopup>
          </Popover>
        ))}
        <span
          className={styles.metric}
          title="Elapsed time for the current or latest operation."
        >
          Time <strong>{elapsedTime(operation, now)}</strong>
        </span>
        <span
          className={styles.metric}
          title="Captured Run request setting, not guaranteed provider speed. Changing the toggle only affects the next run."
        >
          Fast <strong>{fastLabel}</strong>
        </span>
        {capturedAgent?.reasoning_mode &&
          capturedAgent.reasoning_mode !== "default" && (
            <span
              className={styles.metric}
              title="Captured reasoning mode request, not the next-run draft or a guarantee of provider access."
            >
              Mode{" "}
              <strong>
                {reasoningModeLabel(capturedAgent.reasoning_mode)}
              </strong>
            </span>
          )}
        {stale && <span className={styles.stale}>Update unavailable</span>}
      </div>
    </div>
  );
}
