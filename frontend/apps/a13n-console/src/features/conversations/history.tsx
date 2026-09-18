import { useEarlierMessages } from "./earlier";
import { EarlierMessages } from "./earlier-messages";
import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { useAgent } from "../agents/queries";
import { conversationQueries, runPath } from "./api";
import styles from "./conversations.module.css";
import { InputContent, PresentedItems } from "./items";
import { MarkdownContent } from "../../shared/markdown";
import { compareCursors, mergeRetainedItems } from "./projection";
import type { Schema } from "../../shared/api";

export function HistoryTranscript({ runId }: { runId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    [limit, setLimit] = useState(0);
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
          onClick={() => setLimit((value) => value + 1)}
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
  const agent = useAgent(runQuery.data?.agent_id);
  const retained = useQuery({ ...queries.items(runId), staleTime: 60_000 });
  const [older, setOlder] = useState<Schema["ItemResource"][]>([]);
  const initialized = useRef(false);
  const earlier = useEarlierMessages(runId, (page) =>
    setOlder((items) => [...page.items, ...items]),
  );
  const { resetEarlier } = earlier;
  useEffect(() => {
    if (retained.data?.available && !initialized.current) {
      initialized.current = true;
      resetEarlier(retained.data.next_cursor);
    }
  }, [retained.data, resetEarlier]);
  const items = useMemo(
    () =>
      [
        ...mergeRetainedItems(
          mergeRetainedItems(new Map(), older),
          retained.data?.items ?? [],
        ).values(),
      ].sort((a, b) => compareCursors(a.firstCursor, b.firstCursor)),
    [retained.data, older],
  );
  const reload = () => {
    void runQuery.refetch();
    void retained.refetch();
  };
  if (runQuery.isPending || retained.isPending)
    return <Loading variant="list" rows={2} />;
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
        <StatePill state={run.status} />
      </Link>
      <article className={styles.inputMessage}>
        <strong>
          {t(run.input_kind === "feedback" ? "Feedback" : "Input")}
        </strong>
        <InputContent input={run.input} fallback={run.input_text} />
      </article>
      <EarlierMessages {...earlier} />
      <PresentedItems
        items={items}
        runState={run.status}
        agentName={agent.data?.name}
        agentId={run.agent_id}
        agentImageUrl={agent.data?.image_url}
      >
        {!items.some(
          (item) =>
            item.kind === "text_message" &&
            item.role === "assistant" &&
            item.text,
        ) &&
          run.output_text && <MarkdownContent text={run.output_text} />}
      </PresentedItems>
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
