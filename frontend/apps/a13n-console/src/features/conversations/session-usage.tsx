import { Button, ModalFrame } from "a13n-ui";
import { ChartBarIcon } from "@phosphor-icons/react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { exactCost, formatCost } from "../../shared/cost";
import { conversationQueries, runPath } from "./api";
import { formatTokens } from "./format";
import { threadName } from "./thread-branches";
import { chronological } from "./transcript/thread-runs";
import { percent } from "../usage/values";
import {
  Meter,
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
}: {
  sessionId: string;
  threads: readonly Thread[];
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
        "Model requests recorded across this session's conversations, including late reports. Cached input is part of input.",
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
  close,
}: {
  sessionId: string;
  summary: Summary;
  threads: readonly Thread[];
  close: () => void;
}) {
  const { t } = useTranslation();
  const client = useClient(),
    { workspace, basePath } = useWorkspace();
  const queries = conversationQueries(client, workspace.id);
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
  const lists = useQueries({
    queries: threadIds.map((id) => queries.runs(id)),
  });
  const numbers = new Map<string, number>();
  lists.forEach((list) =>
    chronological(list.data ?? []).forEach((run, index) =>
      numbers.set(run.id, index + 1),
    ),
  );
  const perModel = summary.models.length > 1;
  const sections: UsageSection[] = threadIds.map((threadId) => {
    const thread = known.get(threadId);
    return {
      key: threadId,
      title:
        threadIds.length > 1
          ? thread
            ? threadName(thread, threads, t)
            : t("Conversation")
          : undefined,
      groups: summary.runs
        .filter((run) => run.thread_id === threadId)
        .map((run) => {
          const number = numbers.get(run.run_id);
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
                {number ? t("Run {{index}}", { index: number }) : t("Run")}
              </Link>
            ),
          };
        }),
    };
  });
  return (
    <div className={styles.report}>
      <UsageStats totals={usageTotals(summary.models)} />
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

function UsageStats({ totals }: { totals: UsageTotals }) {
  const { t } = useTranslation();
  return (
    <dl className={styles.stats}>
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
        <dt>{t("Input tokens")}</dt>
        <dd>
          <strong>
            <Tokens value={totals.inputTokens} />
          </strong>
          <small>
            {t("{{tokens}} cached", {
              tokens: formatTokens(totals.cachedTokens),
            })}
          </small>
        </dd>
      </div>
      <div>
        <dt>{t("Cache hit rate")}</dt>
        <dd>
          <strong>{percent(totals.cacheRate)}</strong>
          <Meter rate={totals.cacheRate} wide />
        </dd>
      </div>
      <div>
        <dt>{t("Output tokens")}</dt>
        <dd>
          <strong>
            <Tokens value={totals.outputTokens} />
          </strong>
        </dd>
      </div>
    </dl>
  );
}
