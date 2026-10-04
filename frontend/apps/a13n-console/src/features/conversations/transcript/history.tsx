import { Button } from "a13n-ui";
import { useInfiniteQuery, useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import type { Schema } from "../../../shared/api";
import { useAgent } from "../../agents/queries";
import { conversationQueries, runPath, type ViewLevel } from "../api";
import { emptyExecution } from "../execution";
import { presentItems } from "../projection";
import { useEarlierItems } from "../earlier-items";
import { useRunDisplay } from "../run-display";
import { runTimeline } from "../timeline";
import { DebugRunSection } from "./debug/run-section";
import { EarlierItems } from "./earlier-items";
import { useThreadRuns } from "./thread-runs";
import { RunBlock } from "./run-block";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

const CHAT_HISTORY_BATCH = 5;

/**
 * Open Chat with recent context, then prepare another bounded batch before
 * the reader reaches its beginning. Debug reveals one full Run at a time.
 */
export function HistoryTranscript({
  runId,
  thread,
  level,
  preservePosition,
  jumpToDock,
}: {
  runId: string;
  thread: Schema["ThreadView"];
  level: ViewLevel;
  /** Capture immediately before a prepend; restore it in the same layout commit. */
  preservePosition: () => () => void;
  jumpToDock: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation(),
    queries = conversationQueries(client, workspace.id),
    [limit, setLimit] = useState(level === "chat" ? CHAT_HISTORY_BATCH : 0),
    [visible, setVisible] = useState(0);
  const lineage = useInfiniteQuery(queries.lineage(runId));
  useEffect(() => {
    if (level === "chat")
      setLimit((value) => Math.max(value, CHAT_HISTORY_BATCH));
  }, [level]);
  // The lineage reads nearest first and starts at the Run itself.
  const ancestors = (lineage.data?.pages ?? [])
    .flatMap((page) => page.items)
    .filter((entry) => entry.id !== runId);
  const shown = ancestors.slice(0, visible);
  // The reader asked for a Run beyond the pages read so far; a failed read
  // waits for the reader to retry it.
  const { fetchNextPage, isFetchingNextPage, isFetchNextPageError } = lineage;
  const beyond =
    limit > ancestors.length && lineage.hasNextPage && !isFetchNextPageError;
  useEffect(() => {
    if (beyond && !isFetchingNextPage) void fetchNextPage();
  }, [beyond, isFetchingNextPage, fetchNextPage]);
  // Prepare the next batch off screen. Existing messages never move
  // to make room for a skeleton that will have a different height.
  const loaded = useQueries({
    queries:
      level === "chat"
        ? ancestors
            .slice(visible, limit)
            .flatMap((entry) => [
              { ...queries.run(entry.id), staleTime: 60_000 },
              { ...queries.items(entry.id), staleTime: 60_000 },
              ...(entry.thread_id !== thread.id
                ? [{ ...queries.thread(entry.thread_id), staleTime: 60_000 }]
                : []),
            ])
        : [],
    combine: (results) => results.every((result) => !result.isPending),
  });
  const settled =
    !lineage.isPending && loaded && !beyond && !isFetchingNextPage;
  const restore = useRef<(() => void) | null>(null);
  useLayoutEffect(() => {
    restore.current?.();
    restore.current = null;
  }, [visible]);
  useLayoutEffect(() => {
    if (
      limit === visible ||
      !settled ||
      (limit > ancestors.length && lineage.hasNextPage)
    )
      return;
    restore.current = preservePosition();
    setVisible(limit);
  }, [
    limit,
    visible,
    settled,
    ancestors.length,
    lineage.hasNextPage,
    preservePosition,
  ]);
  const sentinel = useRef<HTMLDivElement>(null);
  const more =
    ancestors.length > visible || lineage.hasNextPage || limit !== visible;
  useEffect(() => {
    const mark = sentinel.current;
    const stage = mark?.closest("[data-session-stage]");
    if (
      level !== "chat" ||
      limit !== visible ||
      !settled ||
      !more ||
      !mark ||
      !(stage instanceof HTMLElement)
    )
      return;
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (records) => {
        if (!records.some((record) => record.isIntersecting)) return;
        // Reconnect only after the prepared batch has rendered.
        observer.disconnect();
        setLimit((value) => value + CHAT_HISTORY_BATCH);
      },
      { root: stage, rootMargin: `${stage.clientHeight}px 0px 0px` },
    );
    observer.observe(mark);
    return () => observer.disconnect();
  }, [level, limit, visible, settled, more]);
  return (
    <>
      <ErrorNotice error={lineage.error} retry={() => void lineage.refetch()} />
      {more && (
        <div ref={sentinel} className={styles.earlierRuns} aria-busy={!settled}>
          <Button
            size="sm"
            variant="ghost"
            className={styles.earlierRunsAction}
            loading={!settled}
            disabled={!settled}
            onClick={() =>
              setLimit(
                (value) => value + (level === "chat" ? CHAT_HISTORY_BATCH : 1),
              )
            }
            type="button"
          >
            {t(
              level === "chat" ? "Load earlier messages" : "Load earlier runs",
            )}
          </Button>
        </div>
      )}
      {[...shown]
        .reverse()
        .map((entry) =>
          level === "debug" ? (
            <DebugAncestor
              key={entry.id}
              runId={entry.id}
              thread={thread}
              jumpToDock={jumpToDock}
            />
          ) : (
            <HistoricalRun key={entry.id} runId={entry.id} thread={thread} />
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
  run: Schema["RunView"] | undefined,
  thread: Schema["ThreadView"],
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
    pending: foreign && own.isPending,
    error: own.error,
    refetch: own.refetch,
  };
}

/** An ancestor is read at the level the reader chose, with its own stream. */
function DebugAncestor({
  runId,
  thread,
  jumpToDock,
}: {
  runId: string;
  thread: Schema["ThreadView"];
  jumpToDock: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useQuery({ ...queries.run(runId), staleTime: 60_000 });
  const live = useRunDisplay(runId);
  const run = runQuery.data;
  const own = useOwnThread(run, thread);
  const { number } = useThreadRuns(run?.thread_id ?? "");
  const timeline = useMemo(
    () =>
      run &&
      runTimeline({
        run,
        attempts: live.attempts,
        items: live.items,
        execution: live.execution,
        coverage: live.execution.coverage,
      }),
    [run, live.attempts, live.items, live.execution],
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
    <>
      <EarlierItems earlier={live.earlier} />
      <DebugRunSection
        run={run}
        thread={own.thread}
        timeline={timeline}
        index={number(run.id)}
        jumpToDock={jumpToDock}
      />
    </>
  );
}

function HistoricalRun({
  runId,
  thread,
}: {
  runId: string;
  thread: Schema["ThreadView"];
}) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useQuery({ ...queries.run(runId), staleTime: 60_000 });
  const own = useOwnThread(runQuery.data, thread);
  const agent = useAgent(runQuery.data?.agent_id);
  const retained = useQuery({ ...queries.items(runId), staleTime: 60_000 });
  const earlier = useEarlierItems(runId, retained.data?.items[0]?.ordinal);
  const items = useMemo(
    () => presentItems([...earlier.items, ...(retained.data?.items ?? [])]),
    [earlier.items, retained.data],
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
        earlier={<EarlierItems earlier={earlier} />}
        separatorAction={
          <Link
            className={styles.separatorLink}
            to={runPath(basePath, { ...run, run_id: run.id })}
          >
            {t("View run")}
          </Link>
        }
      />
    </div>
  );
}
