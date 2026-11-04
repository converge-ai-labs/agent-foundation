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
  Tooltip,
  TooltipTrigger,
  TooltipPopup,
} from "a13n-ui";
import { useOperation, useThreads } from "./queries";
import { HelpTip } from "../shell/help-tip";
import { ErrorNotice } from "../shell/ui";
import { useTranslation } from "../i18n";
import { modelControlMessage } from "./model-control-messages";
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
  const { t } = useTranslation();
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
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!active || !open || !debug) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, open, debug, operation?.receipt.receipt_id]);
  const { refetch: refreshContext } = context;
  useEffect(() => {
    if (liveTokens !== undefined) void refreshContext();
  }, [liveTokens, refreshContext]);
  // Keep the request footprint and its captured Model limit from one response.
  const tokens = context.data?.latest_request_tokens;
  const capacity = context.data?.context_window;
  const summary = usageSummary(
    usage.data,
    context.data
      ? { ...context.data, latest_request_tokens: tokens }
      : undefined,
  );
  const occupancy =
    tokens != null && capacity != null && capacity > 0
      ? Math.max(0, Math.min(1, tokens / capacity))
      : undefined;
  const error =
    usage.error ||
    inspection.error ||
    context.error ||
    observed.error ||
    activity.error;
  const hint = `${t("Context")}: ${tokens?.toLocaleString() ?? "—"} / ${capacity?.toLocaleString() ?? "—"} ${t("tokens")} · ${summary.context}${error ? ` · ${t("Update unavailable")}` : ""}`;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger
          render={
            <PopoverTrigger
              render={<Button variant="ghost" size="icon-sm" />}
              className={styles.ringTrigger}
              aria-label={t("Context details")}
            />
          }
        >
          <svg
            viewBox="0 0 24 24"
            className={styles.ring}
            data-unknown={occupancy === undefined || undefined}
            data-full={
              (occupancy !== undefined && occupancy >= 0.9) || undefined
            }
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="8" className={styles.ringTrack} />
            {occupancy !== undefined ? (
              <circle
                cx="12"
                cy="12"
                r="8"
                pathLength="100"
                strokeDasharray={`${occupancy * 100} 100`}
                className={styles.ringFill}
              />
            ) : (
              <text x="12" y="16" textAnchor="middle">
                ?
              </text>
            )}
          </svg>
        </TooltipTrigger>
        <TooltipPopup>{hint}</TooltipPopup>
      </Tooltip>
      <PopoverPopup side="top" align="end" className={styles.popup}>
        <div className={styles.popupHeader}>
          <PopoverTitle>{t("Context usage")}</PopoverTitle>
          <HelpTip label={t("About context")}>
            {t(
              "Tokens in the latest request, using that request's model limit. The model selector applies to the next run.",
            )}
            {!capacity &&
              ` ${t("This model has not provided a context limit, so its used percentage is unavailable.")}`}
          </HelpTip>
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={t("Refresh usage")}
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
          <ContextDetails
            used={tokens}
            capacity={capacity}
            compact
            cacheRate={summary.cache}
          />
          <h3 className={styles.sectionTitle}>{t("Chat usage")}</h3>
          <UsageDetails usage={usage.data} />
          {debug && (
            <dl className={styles.numbers}>
              {mainAccount && (
                <>
                  <dt>{t("Account")}</dt>
                  <dd>{mainAccount.account_name}</dd>
                </>
              )}
              <dt>{active ? t("Working") : t("Time")}</dt>
              <dd>{elapsedTime(operation, now)}</dd>
              <dt>{t("Speed")}</dt>
              <dd>{modelControlMessage(speedLabel(capturedAgent?.fast), t)}</dd>
              {capturedAgent?.reasoning_mode &&
                capturedAgent.reasoning_mode !== "default" && (
                  <>
                    <dt>{t("Mode")}</dt>
                    <dd>
                      {modelControlMessage(
                        reasoningModeLabel(capturedAgent.reasoning_mode),
                        t,
                      )}
                    </dd>
                  </>
                )}
            </dl>
          )}
          <ErrorNotice inline error={error} />
        </div>
      </PopoverPopup>
    </Popover>
  );
}
