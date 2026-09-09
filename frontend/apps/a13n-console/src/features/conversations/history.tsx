import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { conversationQueries, runPath } from "./api";
import styles from "./conversations.module.css";
import { InputContent, PresentedItems } from "./items";
import { MessageMarkdown } from "./markdown";
import { mergeRetainedItems } from "./projection";

export function HistoryTranscript({ runId }: { runId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    [limit, setLimit] = useState(10);
  const lineage = useQuery(
    conversationQueries(client, workspace.id).lineage(runId),
  );
  const ancestors = [...(lineage.data?.items ?? [])]
    .filter((entry) => entry.run_id !== runId)
    .sort((a, b) => a.depth_from_head - b.depth_from_head);
  return (
    <>
      <ErrorNotice error={lineage.error} retry={() => void lineage.refetch()} />
      {ancestors.length > limit && (
        <Button
          size="sm"
          variant="outline"
          onClick={() => setLimit((value) => value + 10)}
          type="button"
        >
          {t("Load earlier messages")}
        </Button>
      )}
      {ancestors
        .slice(0, limit)
        .reverse()
        .map((entry) => (
          <HistoricalRun key={entry.run_id} runId={entry.run_id} />
        ))}
    </>
  );
}
function HistoricalRun({ runId }: { runId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useQuery({ ...queries.run(runId), staleTime: 60_000 });
  const retained = useQuery({ ...queries.items(runId), staleTime: 60_000 });
  const items = useMemo(
    () => [
      ...mergeRetainedItems(new Map(), retained.data?.items ?? []).values(),
    ],
    [retained.data],
  );
  const reload = () => {
    void runQuery.refetch();
    void retained.refetch();
  };
  if (runQuery.isPending || retained.isPending) return <Loading />;
  if (!runQuery.data || !retained.data)
    return (
      <ErrorNotice error={runQuery.error ?? retained.error} retry={reload} />
    );
  const run = runQuery.data;
  return (
    <section className={styles.historyRun}>
      <Link
        className={styles.historyLink}
        to={runPath(basePath, { ...run, run_id: run.id })}
      >
        {t("View run")} · <Timestamp value={run.created_at} />
      </Link>
      <article className={styles.inputMessage}>
        <strong>{t("You")}</strong>
        <InputContent input={run.input} fallback={run.input_text} />
      </article>
      <PresentedItems items={items} runState={run.status} />
      {!items.some(
        (item) =>
          item.kind === "text_message" &&
          item.role === "assistant" &&
          item.text,
      ) &&
        run.output_text && <MessageMarkdown text={run.output_text} />}
      {!retained.data.available && (
        <p className={styles.notice}>
          {t("Detailed items are currently unavailable for this run.")}
          <Button size="sm" variant="outline" type="button" onClick={reload}>
            {t("Reload")}
          </Button>
        </p>
      )}
    </section>
  );
}
