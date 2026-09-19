import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import type { Schema } from "../../../shared/api";
import { useAgent } from "../../agents/queries";
import { conversationQueries, runPath } from "../api";
import { useEarlierMessages } from "../earlier";
import { compareCursors, mergeRetainedItems } from "../projection";
import { EarlierMessages } from "./earlier-messages";
import { RunBlock } from "./run-block";
import styles from "./transcript.module.css";

/** Ancestor runs stay collapsed until the reader asks for more context. */
export function HistoryTranscript({ runId }: { runId: string }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
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
        <div className={styles.earlierRuns}>
          <Button
            size="sm"
            variant="outline"
            onClick={() => setLimit((value) => value + 1)}
            type="button"
          >
            {t("Load earlier messages")}
          </Button>
        </div>
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
    <>
      <RunBlock
        run={run}
        items={items}
        agentName={agent.data?.name}
        agentImageUrl={agent.data?.image_url}
        earlier={<EarlierMessages {...earlier} />}
        separatorAction={
          <Link
            className={styles.separatorLink}
            to={runPath(basePath, { ...run, run_id: run.id })}
          >
            {t("View run")}
          </Link>
        }
      />
      {!retained.data.available && (
        <p className={styles.notice}>
          {t("Detailed items are currently unavailable for this run.")}
          <Button size="sm" variant="outline" type="button" onClick={reload}>
            {t("Reload")}
          </Button>
        </p>
      )}
    </>
  );
}
