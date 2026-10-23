import { Button, ModalFrame } from "a13n-ui";
import { ChartBarIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { exactCost, formatCost } from "../../shared/cost";
import { conversationQueries, runPath } from "./api";
import { ContextWindow } from "./context-window";
import { formatTokens } from "./format";
import { threadName } from "./thread-branches";
import { chronological } from "./transcript/thread-runs";
import { percent } from "../usage/values";
import {
  Tokens,
  UsageTable,
  usageTotals,
  type UsageSection,
  type UsageTotals,
} from "./usage-table";
import styles from "./usage.module.css";

type Thread = Schema["ThreadView"];
type Summary = Schema["UsageSummary"];

/**
 * What the Session has spent so far, in the header; the dialog breaks it down
 * by model and by Run. Nothing shows before a model request is recorded.
 */
export function SessionUsage({
  sessionId,
  threads,
  thread,
  runId,
}: {
  sessionId: string;
  threads: readonly Thread[];
  thread?: Thread;
  runId?: string;
}) {
  const { t } = useTranslation();
  const client = useClient(),
    { workspace } = useWorkspace();
  const usage = useQuery(
    conversationQueries(client, workspace.id).usage({ session_id: sessionId }),
  );
  const [open, setOpen] = useState(false);
  if (!usage.data?.models.length) return null;
  const totals = usageTotals(usage.data.models);
  // Unpriced usage still has a size worth showing.
  const glance =
    totals.cost === null
      ? t("{{tokens}} tokens", {
          tokens: formatTokens(totals.inputTokens + totals.outputTokens),
        })
      : formatCost(totals.cost);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      size="xl"
      title={t("Session usage")}
      description={t(
        "Model requests across this session's threads. Cached tokens are included in input.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={styles.trigger}
          aria-label={t("Session usage: {{value}}", { value: glance })}
          title={t("Session usage")}
        >
          <ChartBarIcon size={14} aria-hidden="true" />
          {glance}
        </Button>
      }
    >
      <SessionUsageReport
        sessionId={sessionId}
        thread={open ? thread : undefined}
        runId={runId}
        summary={usage.data}
        threads={threads}
        close={() => setOpen(false)}
      />
    </ModalFrame>
  );
}

function SessionUsageReport({
  sessionId,
  summary,
  threads,
  thread,
  runId,
  close,
}: {
  sessionId: string;
  summary: Summary;
  threads: readonly Thread[];
  thread?: Thread;
  runId?: string;
  close: () => void;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const known = new Map(threads.map((thread) => [thread.id, thread]));
  // Conversations in creation order; Run numbers count within each one, as
  // its Run list does.
  const used = new Set(summary.runs.map((run) => run.thread_id));
  const threadIds = [
    ...chronological(threads)
      .map((thread) => thread.id)
      .filter((id) => used.has(id)),
    ...[...used].filter((id) => !known.has(id)),
  ];
  const perModel = summary.models.length > 1;
  const sections: UsageSection[] = threadIds.map((threadId) => {
    const thread = known.get(threadId);
    return {
      key: threadId,
      title:
        threadIds.length > 1
          ? thread
            ? threadName(thread, threads, t)
            : t("Thread")
          : undefined,
      groups: summary.runs
        .filter((run) => run.thread_id === threadId)
        .map((run) => {
          return {
            key: run.run_id,
            models: run.models,
            label: (
              <Link
                className={styles.runLink}
                to={runPath(
                  basePath,
                  {
                    session_id: sessionId,
                    thread_id: threadId,
                    run_id: run.run_id,
                  },
                  "debug",
                )}
                onClick={close}
              >
                {t("Run {{index}}", { index: run.run_number })}
              </Link>
            ),
          };
        }),
    };
  });
  return (
    <div className={styles.report}>
      <UsageStats
        totals={usageTotals(summary.models)}
        context={
          thread && (
            <ContextWindow
              scope={runId ? { run_id: runId } : { thread_id: thread.id }}
              live={!!thread.current_run_id}
              statLabel={threadName(thread, threads, t)}
            />
          )
        }
      />
      <section className={styles.section}>
        <h3>{t("By model")}</h3>
        <UsageTable
          caption={t("Usage by model")}
          sections={[
            {
              key: "models",
              groups: [{ key: "models", models: summary.models }],
            },
          ]}
        />
      </section>
      <section className={styles.section}>
        <h3>{t("By run")}</h3>
        <UsageTable
          caption={t("Usage by run")}
          leading={t("Run")}
          model={perModel}
          requests={false}
          sections={sections}
        />
      </section>
    </div>
  );
}

function UsageStats({
  totals,
  context,
}: {
  totals: UsageTotals;
  context?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <dl className={styles.stats} data-context={context ? "" : undefined}>
      <div>
        <dt>{t("Spend")}</dt>
        <dd>
          <strong title={exactCost(totals.cost)}>
            {formatCost(totals.cost)}
          </strong>
          <small>{t("{{count}} requests", { count: totals.requests })}</small>
          {totals.unpriced && <small>{t("partly unpriced")}</small>}
        </dd>
      </div>
      <div>
        <dt>{t("Tokens")}</dt>
        <dd>
          <div className={styles.tokenPair}>
            <span>
              <small>{t("Input")}</small>
              <strong>
                <Tokens value={totals.inputTokens} />
              </strong>
            </span>
            <span>
              <small>{t("Output")}</small>
              <strong>
                <Tokens value={totals.outputTokens} />
              </strong>
            </span>
          </div>
          <small>
            {t("{{tokens}} cached", {
              tokens: formatTokens(totals.cachedTokens),
            })}
            {" · "}
            {t("{{rate}} cache hit", { rate: percent(totals.cacheRate) })}
          </small>
        </dd>
      </div>
      {context}
    </dl>
  );
}
