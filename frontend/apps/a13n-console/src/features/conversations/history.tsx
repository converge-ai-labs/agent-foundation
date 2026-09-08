import { useState } from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { conversationApi, runPath } from "./api";
import { InputContent, PresentedItems } from "./items";
import { mergeRetainedItems } from "./projection";
import { MessageMarkdown } from "./markdown";
import styles from "./conversations.module.css";

export function HistoryTranscript({ runId }: { runId: string }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    [limit, setLimit] = useState(10);
  const lineage = useQuery({
    queryKey: ["run-lineage", workspace.id, runId],
    queryFn: ({ signal }) =>
      conversationApi(client, workspace.id).lineage(runId, signal),
  });
  const ancestors = [...(lineage.data?.items ?? [])]
    .filter((entry) => entry.run_id !== runId)
    .sort((a, b) => a.depth_from_head - b.depth_from_head);
  return (
    <>
      <ErrorNotice error={lineage.error} retry={() => void lineage.refetch()} />
      {ancestors.length > limit && (
        <Button size="sm" onClick={() => setLimit((value) => value + 10)}>
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
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    api = conversationApi(client, workspace.id);
  const query = useQuery({
    queryKey: ["retained-run", workspace.id, runId],
    staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const [run, retained] = await Promise.all([
        api.run(runId, signal),
        api.retainedItems(runId, signal),
      ]);
      return {
        run,
        retained,
        items: [...mergeRetainedItems(new Map(), retained.items).values()],
      };
    },
  });
  if (query.isPending) return <Loading />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const { run, items } = query.data;
  return (
    <section className={styles.historyRun}>
      <Link
        className={styles.historyLink}
        to={runPath(workspace.id, { ...run, run_id: run.id })}
      >
        {t("View run")} · {run.id}
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
      {!query.data.retained.available && (
        <p className={styles.notice}>
          {t("Detailed items are currently unavailable for this run.")}
          <Button size="sm" onClick={() => void query.refetch()}>
            {t("Reload")}
          </Button>
        </p>
      )}
    </section>
  );
}
