import { Button } from "a13n-ui";
import { useQueries, useQuery } from "@tanstack/react-query";
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
import { useKeepPosition } from "./keep-position";
import { RunBlock } from "./run-block";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

/**
 * The Runs this one continues, above it. Chat reaches further back on its own
 * as the reader scrolls up; Debug asks first, because every ancestor section
 * replays that Run's stream.
 */
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
    queries = conversationQueries(client, workspace.id),
    [limit, setLimit] = useState(0);
  const lineage = useQuery(queries.lineage(runId));
  const ancestors = [...(lineage.data?.items ?? [])]
    .filter((entry) => entry.run_id !== runId)
    .sort((a, b) => a.depth_from_head - b.depth_from_head);
  const shown = ancestors.slice(0, limit);
  // Everything a Chat ancestor is read from. Reaching further back waits for
  // all of it, so one automatic load never cascades into the next and the
  // reader's position is restored only once the new Run has its own height.
  const loaded = useQueries({
    queries:
      level === "chat"
        ? shown.flatMap((entry) => [
            { ...queries.run(entry.run_id), staleTime: 60_000 },
            { ...queries.items(entry.run_id), staleTime: 60_000 },
            ...(entry.thread_id && entry.thread_id !== thread.id
              ? [{ ...queries.thread(entry.thread_id), staleTime: 60_000 }]
              : []),
          ])
        : [],
    combine: (results) => results.every((result) => !result.isPending),
  });
  const keepPosition = useKeepPosition(!loaded);
  const sentinel = useRef<HTMLDivElement>(null);
  const more = ancestors.length > limit;
  useEffect(() => {
    const mark = sentinel.current;
    const stage = mark?.closest("[data-session-stage]");
    if (!loaded || !mark || !(stage instanceof HTMLElement)) return;
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (records) => {
        if (!records.some((record) => record.isIntersecting)) return;
        // One Run at a time; this runs again once that Run has rendered.
        observer.disconnect();
        keepPosition(stage.querySelector("[data-run]"));
        setLimit((value) => value + 1);
      },
      { root: stage },
    );
    observer.observe(mark);
    return () => observer.disconnect();
  }, [keepPosition, loaded, more]);
  return (
    <>
      <ErrorNotice error={lineage.error} retry={() => void lineage.refetch()} />
      {more &&
        (level === "chat" ? (
          <div ref={sentinel} className={styles.earlierRunsMark} />
        ) : (
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
        ))}
      {[...shown]
        .reverse()
        .map((entry) =>
          level === "debug" ? (
            <DebugAncestor
              key={entry.run_id}
              runId={entry.run_id}
              thread={thread}
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

/**
 * A lineage ancestor may belong to the Thread this one branched from; it is
 * read, numbered and linked as that Thread's Run, not as one of this Thread.
 */
function useOwnThread(
  run: Schema["RunResource"] | undefined,
  thread: Schema["ThreadResource"],
) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const foreign = !!run && run.thread_id !== thread.id;
  const own = useQuery({
    ...conversationQueries(client, workspace.id).thread(run?.thread_id ?? ""),
    enabled: foreign,
    staleTime: 60_000,
  });
  return {
    thread: foreign ? own.data : thread,
    number: useThreadRuns(run?.thread_id ?? "").number,
    pending: foreign && own.isPending,
    error: own.error,
    refetch: own.refetch,
  };
}

/** An ancestor is read at the level the reader chose, with its own stream. */
function DebugAncestor({
  runId,
  thread,
}: {
  runId: string;
  thread: Schema["ThreadResource"];
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useQuery({ ...queries.run(runId), staleTime: 60_000 });
  const live = useRunStream(runId, { replay: true });
  const run = runQuery.data;
  const own = useOwnThread(run, thread);
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
  if (runQuery.isPending || own.pending)
    return <Loading variant="list" rows={3} />;
  if (!run || !timeline || !own.thread)
    return (
      <ErrorNotice
        error={runQuery.error ?? own.error}
        retry={() => {
          void runQuery.refetch();
          void own.refetch();
        }}
      />
    );
  return (
    <DebugRunSection
      run={run}
      thread={own.thread}
      timeline={timeline}
      index={own.number(run.id)}
      runNumber={own.number}
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
  const own = useOwnThread(runQuery.data, thread);
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
    void own.refetch();
  };
  if (runQuery.isPending || retained.isPending || own.pending)
    return <Loading variant="list" rows={2} />;
  if (!run || !timeline || !retained.data || !own.thread)
    return (
      <ErrorNotice
        error={runQuery.error ?? retained.error ?? own.error}
        retry={reload}
      />
    );
  return (
    <div className={debug.runAnchor} data-run={run.id}>
      <RunBlock
        run={run}
        thread={own.thread}
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
