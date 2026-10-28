import { reasoningModeLabel } from "./reasoning-mode-picker";
import { speedLabel } from "./speed-label";
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
import { HelpTip } from "../shell/help-tip";
import { ErrorNotice } from "../shell/ui";
import {
  elapsedTime,
  usageSummary,
  useContextUsage,
  useThreadUsage,
} from "./usage";
import { ContextDetails, UsageDetails } from "./usage-details";
import styles from "./composer-status.module.css";
import { useDebug } from "../shell/debug";
import { ArrowClockwise } from "@phosphor-icons/react";

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
  const { enabled: debug } = useDebug();
  const activity = useThreads(threadId, undefined, true, { enabled: !receipt });
  const observed = useOperation(threadId, receipt);
  const row = activity.data?.pages
    .flatMap((page) => page.rows)
    .find((row) => row.thread.thread_id === threadId);
  const operation = observed.data ?? row?.latest_operation;
  const mainAccount = operation
    ? operation.main_account
    : row?.thread.last_execution?.main_account;
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
        {debug && mainAccount && (
          <span
            className={styles.metric}
            title={`${mainAccount.group_id} · ${mainAccount.reason.replaceAll("_", " ")}`}
          >
            Account <strong>{mainAccount.account_name}</strong>
          </span>
        )}
        {(
          [
            ["Tokens", summary.tokens, "Chat usage"],
            ["Context", summary.context, "Context usage"],
            ["Cost", summary.cost, "Chat usage"],
            ["Cache", summary.cache, "Chat usage"],
          ] as const
        )
          .filter(([label]) => debug || label !== "Tokens")
          .map(([label, value, title]) => (
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
                {label === "Cost" && !debug ? "Usage" : label}{" "}
                <strong>{value}</strong>
              </PopoverTrigger>
              <PopoverPopup side="top" align="end" className={styles.popup}>
                <div className={styles.popupHeader}>
                  <PopoverTitle>{title}</PopoverTitle>
                  {label === "Context" && (
                    <HelpTip label="About context">
                      Tokens in the latest request. Chat totals are in Usage.
                      The context limit depends on the model.
                    </HelpTip>
                  )}
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label="Refresh usage"
                    title="Refresh usage"
                    loading={usage.isFetching || context.isFetching}
                    onClick={() => {
                      void usage.refetch();
                      void context.refetch();
                      void inspection.refetch();
                      if (receipt) void observed.refetch();
                      else void activity.refetch();
                    }}
                  >
                    <ArrowClockwise />
                  </Button>
                </div>
                <div className={styles.popupBody}>
                  {label === "Context" ? (
                    <ContextDetails
                      used={tokens}
                      capacity={context.data?.context_window}
                    />
                  ) : (
                    <UsageDetails usage={usage.data} />
                  )}
                  <ErrorNotice
                    inline
                    error={
                      usage.error ||
                      context.error ||
                      inspection.error ||
                      observed.error ||
                      activity.error
                    }
                  />
                </div>
              </PopoverPopup>
            </Popover>
          ))}
        {(debug || active) && (
          <span
            className={styles.metric}
            title="Elapsed time for the current or latest operation."
          >
            {active ? "Working" : "Time"}{" "}
            <strong>{elapsedTime(operation, now)}</strong>
          </span>
        )}
        <span
          className={styles.metric}
          title="Speed requested for this run. Changes apply next time. Actual speed depends on the provider."
        >
          Speed <strong>{speedLabel(capturedAgent?.fast)}</strong>
        </span>
        {debug &&
          capturedAgent?.reasoning_mode &&
          capturedAgent.reasoning_mode !== "default" && (
            <span
              className={styles.metric}
              title="Reasoning mode requested for this run. Provider access may vary. Changes apply next time."
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
