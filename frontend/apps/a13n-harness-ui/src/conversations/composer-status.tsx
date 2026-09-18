import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
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
import { ContextDetails, CostDetails, TokenDetails } from "./usage-details";
import styles from "./composer-status.module.css";

export function ComposerStatus({
  threadId,
  receipt,
  busy = false,
  liveTokens,
}: {
  threadId: string;
  receipt?: string | null;
  busy?: boolean;
  liveTokens?: number;
}) {
  const activity = useThreads(threadId, undefined, true, { enabled: !receipt });
  const observed = useOperation(threadId, receipt);
  const operation =
    observed.data ??
    activity.data?.pages
      .flatMap((page) => page.rows)
      .find((row) => row.thread.thread_id === threadId)?.latest_operation;
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
  const fast =
    active &&
    (captured?.capture_source !== "active_operation" ||
      captured.receipt_id !== operation?.receipt.receipt_id)
      ? undefined
      : captured?.captured?.agent.fast;
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
        {(
          [
            ["Tokens", summary.tokens, "Token usage"],
            ["Context", summary.context, "Context usage"],
            ["Cost", summary.cost, "Model costs"],
            ["Cache", summary.cache, "Token usage"],
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
              ) : label === "Cost" ? (
                <CostDetails usage={usage.data} />
              ) : (
                <TokenDetails usage={usage.data} />
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
        {stale && <span className={styles.stale}>Update unavailable</span>}
      </div>
    </div>
  );
}
