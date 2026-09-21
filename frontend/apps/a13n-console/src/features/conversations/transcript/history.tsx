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
import { conversationQueries, runPath, type ViewLevel } from "../api";
import { useEarlierMessages } from "../earlier";
import { emptyExecution } from "../execution";
import { compareCursors, mergeRetainedItems } from "../projection";
import { useRunStream } from "../run-stream";
import { runTimeline } from "../timeline";
import { DebugRunSection } from "./debug/run-section";
import { useThreadRuns } from "./thread-runs";
import { EarlierMessages } from "./earlier-messages";
import { RunBlock } from "./run-block";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

/** Ancestor runs stay collapsed until the reader asks for more context. */
export function HistoryTranscript({
  runId,
  thread,
  level,
}: {
  runId: string;
  thread: Schema["ThreadResource"];
  level: ViewLevel;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    [limit, setLimit] = useState(0);
  const lineage = useQuery(
    conversationQueries(client, workspace.id).lineage(runId),
  );
  const { number } = useThreadRuns(level === "debug" ? thread.id : "");
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
            variant="ghost"
            className={styles.earlierRunsAction}
            onClick={() => setLimit((value) => value + 1)}
            type="button"
          >
            {t("Load earlier runs")}
          </Button>
        </div>
      )}
      {ancestors
        .slice(0, limit)
        .reverse()
        .map((entry) =>
          level === "debug" ? (
            <DebugAncestor
              key={entry.run_id}
              runId={entry.run_id}
              thread={thread}
              index={number(entry.run_id)}
              runNumber={number}
            />
          ) : (
            <HistoricalRun
              key={entry.run_id}
              runId={entry.run_id}
              thread={thread}
            />
          ),
        )}
    </>
  );
}

/** An ancestor is read at the level the reader chose, with its own stream. */
function DebugAncestor({
  runId,
  thread,
  index,
  runNumber,
}: {
  runId: string;
  thread: Schema["ThreadResource"];
  index: number | null;
  runNumber: (runId: string) => number | null;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useQuery({ ...queries.run(runId), staleTime: 60_000 });
  const live = useRunStream(runId, { replay: true });
  const run = runQuery.data;
  const timeline = useMemo(
    () =>
      run &&
      runTimeline({
        run,
        items: live.items,
        execution: live.execution,
        coverage: live.execution.coverage,
      }),
    [run, live.items, live.execution],
  );
  if (runQuery.isPending) return <Loading variant="list" rows={3} />;
  if (!run || !timeline)
    return (
      <ErrorNotice
        error={runQuery.error}
        retry={() => void runQuery.refetch()}
      />
    );
  return (
    <DebugRunSection
      run={run}
      thread={thread}
      timeline={timeline}
      index={index}
      runNumber={runNumber}
    />
  );
}

function HistoricalRun({
  runId,
  thread,
}: {
  runId: string;
  thread: Schema["ThreadResource"];
}) {
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
  const run = runQuery.data;
  // A historical run is read from its retained Items alone: the timeline
  // reports the calls they recorded without inferring a complete history.
  const timeline = useMemo(
    () =>
      run &&
      runTimeline({
        run,
        items,
        execution: emptyExecution(),
        coverage: "unavailable",
      }),
    [run, items],
  );
  const reload = () => {
    void runQuery.refetch();
    void retained.refetch();
  };
  if (runQuery.isPending || retained.isPending)
    return <Loading variant="list" rows={2} />;
  if (!run || !timeline || !retained.data)
    return (
      <ErrorNotice error={runQuery.error ?? retained.error} retry={reload} />
    );
  return (
    <div className={debug.runAnchor} data-run={run.id}>
      <RunBlock
        run={run}
        thread={thread}
        timeline={timeline}
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
    </div>
  );
}
