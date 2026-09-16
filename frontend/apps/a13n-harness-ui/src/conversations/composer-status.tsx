import { useEffect, useState } from "react";
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
  const usage = useThreadUsage(threadId);
  const context = useContextUsage(threadId);
  const [now, setNow] = useState(Date.now);
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
    context.error ||
    observed.error ||
    activity.error
  );
  return (
    <div className={styles.bar}>
      <Popover>
        <PopoverTrigger
          render={<button type="button" className={styles.metrics} />}
          aria-label="Conversation usage details"
        >
          <span>
            Context <strong>{summary.context}</strong>
          </span>
          <span>
            Cost <strong>{summary.cost}</strong>
          </span>
          <span>
            Cache <strong>{summary.cache}</strong>
          </span>
          <span>
            Time <strong>{elapsedTime(operation, now)}</strong>
          </span>
          {stale && <span className={styles.stale}>Update unavailable</span>}
        </PopoverTrigger>
        <PopoverPopup side="top" align="start" className={styles.popup}>
          <PopoverTitle>Conversation usage</PopoverTitle>
          <dl>
            <dt>Context</dt>
            <dd>
              {tokens?.toLocaleString() ?? "Unknown"} /{" "}
              {context.data?.context_window?.toLocaleString() ?? "unknown"}{" "}
              tokens reported by the latest root request. Updates during
              execution, not cumulative usage; live edits are not included.
            </dd>
            <dt>Model cost</dt>
            <dd>
              Recorded root-agent model cost for this conversation, matching the
              CLI status bar.{" "}
              {usage.data?.root.unknown_model_costs
                ? `${usage.data.root.unknown_model_costs} responses have unknown cost; + marks a partial subtotal.`
                : "Provider charges are separate; this is not an invoice."}
            </dd>
            <dt>Cache</dt>
            <dd>
              Cache-read tokens / (input + output tokens), matching the CLI
              status bar. Root agent only.
            </dd>
            <dt>Time</dt>
            <dd>
              Elapsed time for the current or latest operation. Unavailable
              timings are not estimated.
            </dd>
          </dl>
          <ErrorNotice
            error={
              usage.error || context.error || observed.error || activity.error
            }
          />
          <Button
            variant="ghost"
            size="sm"
            loading={usage.isFetching || context.isFetching}
            onClick={() => {
              void usage.refetch();
              void context.refetch();
              if (receipt) void observed.refetch();
              else void activity.refetch();
            }}
          >
            Refresh usage
          </Button>
        </PopoverPopup>
      </Popover>
    </div>
  );
}
