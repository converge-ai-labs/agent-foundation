import { reasoningModeLabel } from "./reasoning-mode-picker";
import { speedLabel } from "./speed-label";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
  ScrollArea,
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
import {
  ArrowClockwise,
  CircleHalf,
  CurrencyDollar,
} from "@phosphor-icons/react";

export function ComposerStatus({
  threadId,
  receipt,
  busy = false,
  liveTokens,
  liveRecordId,
}: {
  threadId: string;
  receipt?: string | null;
  busy?: boolean;
  liveTokens?: number;
  liveRecordId?: string;
}) {
  const { t, i18n } = useTranslation();
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
  const contextBody = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(Date.now);
  const [detail, setDetail] = useState<string | null>(null);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, operation?.receipt.receipt_id]);
  const { refetch: refreshContext } = context;
  useEffect(() => {
    if (detail === "Context" && liveRecordId) void refreshContext();
  }, [detail, liveRecordId, liveTokens, refreshContext]);
  const tokens = liveTokens ?? context.data?.latest_request_tokens;
  const summary = usageSummary(
    usage.data,
    context.data
      ? { ...context.data, latest_request_tokens: tokens }
      : undefined,
  );
  const percent = summary.contextPercent;
  const contextLabel = percent == null ? "—" : `${Math.round(percent)}%`;
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
            title={`${mainAccount.group_id} - ${mainAccount.reason.replaceAll("_", " ")}`}
          >
            {t("Account")} <strong>{mainAccount.account_name}</strong>
          </span>
        )}
        {(
          [
            // The first element identifies the metric; the last is its label.
            ["Tokens", summary.tokens, t("Chat usage"), t("Tokens")],
            ["Context", contextLabel, t("Context usage"), t("Context")],
            ["Cost", summary.cost, t("Chat usage"), t("Cost")],
          ] as const
        )
          .filter(([label, value]) => {
            if (label === "Context") return tokens != null;
            if (label === "Tokens" && !debug) return false;
            return value !== "—";
          })
          .map(([label, value, title, name]) => (
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
                aria-label={t("{{metric}} details", { metric: name })}
                title={t("{{metric}}: {{value}}", { metric: name, value })}
              >
                {debug ? (
                  name
                ) : label === "Context" ? (
                  percent == null ? (
                    <CircleHalf aria-hidden />
                  ) : (
                    <svg
                      aria-hidden="true"
                      className={styles.contextRing}
                      viewBox="0 0 16 16"
                    >
                      <circle cx="8" cy="8" r="6" />
                      <circle
                        cx="8"
                        cy="8"
                        r="6"
                        pathLength="100"
                        strokeDasharray="100"
                        strokeDashoffset={
                          100 - Math.min(100, Math.max(0, percent))
                        }
                        transform="rotate(-90 8 8)"
                      />
                    </svg>
                  )
                ) : (
                  <CurrencyDollar aria-hidden />
                )}{" "}
                {label === "Context" && !debug && tokens != null ? (
                  <span>
                    {Intl.NumberFormat(i18n.resolvedLanguage, {
                      minimumFractionDigits: 1,
                      maximumFractionDigits: 2,
                    }).format(tokens / 1000)}
                    k
                  </span>
                ) : (
                  <strong>
                    {label === "Cost" && !debug
                      ? value.replace(/^\$/, "")
                      : value}
                  </strong>
                )}
              </PopoverTrigger>
              <PopoverPopup
                side="top"
                align="end"
                className={`${styles.popup} ${label === "Context" ? styles.contextPopup : ""}`}
              >
                <div className={styles.popupHeader}>
                  <PopoverTitle>{title}</PopoverTitle>
                  {label === "Context" && (
                    <HelpTip label={t("About context")}>
                      {t(
                        "Tokens in the latest main model request with reported usage. Chat totals are in Usage. The context limit depends on the model.",
                      )}
                      {context.data?.context_breakdown &&
                        ` ${t("Prompt estimates divide input tokens in proportion to the measured characters in each category.")}`}
                      {!context.data?.context_window &&
                        ` ${t("This model has not provided a context limit, so its used percentage is unavailable.")}`}
                    </HelpTip>
                  )}
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("Refresh usage")}
                    title={t("Refresh usage")}
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
                <ScrollArea
                  className={styles.popupScroll}
                  overscrollContain
                  ref={label === "Context" ? contextBody : undefined}
                >
                  <div className={styles.popupBody}>
                    {label === "Context" ? (
                      <div className={styles.contextStats}>
                        <ContextDetails
                          request={
                            liveTokens == null ||
                            (liveRecordId != null &&
                              liveRecordId === context.data?.record_id &&
                              liveTokens ===
                                context.data?.latest_request_tokens)
                              ? context.data
                              : undefined
                          }
                          used={tokens}
                          capacity={context.data?.context_window}
                          onGroupOpen={() => {
                            // Keep this upward-opening popover at its natural resting height.
                            const body = contextBody.current;
                            if (body && !body.style.height) {
                              body.style.height = getComputedStyle(body).height;
                            }
                          }}
                          compact
                          cacheRate={summary.cache}
                        />
                      </div>
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
                </ScrollArea>
              </PopoverPopup>
            </Popover>
          ))}
        {debug && (
          <span
            className={styles.metric}
            title={t("Elapsed time for the current or latest operation.")}
          >
            {active ? t("Working") : t("Time")}{" "}
            <strong>{elapsedTime(operation, now)}</strong>
          </span>
        )}
        {debug && (
          <span
            className={styles.metric}
            title={t(
              "Speed requested for this run. Changes apply next time. Actual speed depends on the provider.",
            )}
          >
            {t("Speed")}{" "}
            <strong>
              {modelControlMessage(speedLabel(capturedAgent?.fast), t)}
            </strong>
          </span>
        )}
        {debug &&
          capturedAgent?.reasoning_mode &&
          capturedAgent.reasoning_mode !== "default" && (
            <span
              className={styles.metric}
              title={t(
                "Reasoning mode requested for this run. Provider access may vary. Changes apply next time.",
              )}
            >
              {t("Mode")}{" "}
              <strong>
                {modelControlMessage(
                  reasoningModeLabel(capturedAgent.reasoning_mode),
                  t,
                )}
              </strong>
            </span>
          )}
        {stale && (
          <span className={styles.stale}>{t("Update unavailable")}</span>
        )}
      </div>
    </div>
  );
}
