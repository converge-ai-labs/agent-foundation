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
import { runExecution } from "../execution";
import { isOmitted } from "../display";
import { useRunHistory } from "../run-history";
import { EarlierContent } from "./earlier-content";
import { presentItems } from "../projection";
import { StoredContents } from "../stored-content";
import { runTimeline } from "../timeline";
import { FailureNotice } from "./failure-notice";
import { DebugRunSection } from "./debug/run-section";
import { useThreadRuns } from "./thread-runs";
import { RunBlock } from "./run-block";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

const CHAT_HISTORY_BATCH = 5;

/**
 * Keep the same loaded Runs across levels. Chat prepares a bounded batch on
 * upward scrolling; Debug reveals one full Run per explicit request.
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
    [limit, setLimit] = useState(0),
    [visible, setVisible] = useState(0);
  const lineage = useInfiniteQuery(queries.lineage(runId));
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
  const prepared = useQueries({
    queries: ancestors
      .slice(visible, limit)
      .flatMap((entry) => [
        { ...queries.run(entry.id), staleTime: 60_000 },
        queries.items(entry.id),
        ...(level === "debug"
          ? [{ ...queries.attempts(entry.id), staleTime: 60_000 }]
          : []),
        ...(entry.thread_id !== thread.id
          ? [{ ...queries.thread(entry.thread_id), staleTime: 60_000 }]
          : []),
      ]),
  });
  const loadError = prepared.find((result) => result.isError)?.error;
  const loaded = prepared.every((result) => result.isSuccess);
  const settled =
    !lineage.isPending &&
    !prepared.some((result) => result.isFetching) &&
    !beyond &&
    !isFetchingNextPage;
  const restore = useRef<(() => void) | null>(null);
  useLayoutEffect(() => {
    restore.current?.();
    restore.current = null;
  }, [visible]);
  useLayoutEffect(() => {
    if (
      limit === visible ||
      !settled ||
      !loaded ||
      (limit > ancestors.length && lineage.hasNextPage)
    )
      return;
    restore.current = preservePosition();
    setVisible(limit);
  }, [
    limit,
    visible,
    settled,
    loaded,
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
      !loaded ||
      !more ||
      !mark ||
      !(stage instanceof HTMLElement)
    )
      return;
    if (typeof IntersectionObserver === "undefined") return;
    let nearTop = false;
    let upward = false;
    let requested = false;
    let active = true;
    let lastTop = stage.scrollTop;
    const request = () => {
      if (!active || !nearTop || !upward || requested) return;
      requested = true;
      observer.disconnect();
      setLimit((value) => value + CHAT_HISTORY_BATCH);
    };
    const onScroll = () => {
      if (stage.scrollTop < lastTop) upward = true;
      lastTop = stage.scrollTop;
      request();
    };
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY < 0) upward = true;
      request();
    };
    const observer = new IntersectionObserver(
      (records) => {
        nearTop = records.some((record) => record.isIntersecting);
        request();
      },
      { root: stage, rootMargin: `${stage.clientHeight}px 0px 0px` },
    );
    observer.observe(mark);
    stage.addEventListener("scroll", onScroll, { passive: true });
    stage.addEventListener("wheel", onWheel, { passive: true });
    return () => {
      active = false;
      observer.disconnect();
      stage.removeEventListener("scroll", onScroll);
      stage.removeEventListener("wheel", onWheel);
    };
  }, [level, limit, visible, settled, loaded, more]);
  return (
    <>
      <ErrorNotice error={lineage.error} retry={() => void lineage.refetch()} />
      <ErrorNotice
        error={loadError}
        retry={() => {
          for (const result of prepared)
            if (result.isError) void result.refetch();
        }}
      />
      {more && (
        <div ref={sentinel} className={styles.earlierRuns} aria-busy={!settled}>
          <Button
            size="sm"
            variant="ghost"
            className={styles.earlierRunsAction}
            loading={!settled}
            disabled={!settled || !!loadError || !!lineage.error}
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
      {[...shown].reverse().map((entry) => (
        <HistoricalRun
          key={entry.id}
          runId={entry.id}
          thread={thread}
          level={level}
          jumpToDock={jumpToDock}
          preservePosition={preservePosition}
        />
      ))}
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

function HistoricalRun({
  runId,
  thread,
  level,
  jumpToDock,
  preservePosition,
}: {
  runId: string;
  thread: Schema["ThreadView"];
  level: ViewLevel;
  jumpToDock: () => void;
  preservePosition: () => () => void;
}) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    queries = conversationQueries(client, workspace.id);
  const runQuery = useQuery({ ...queries.run(runId), staleTime: 60_000 });
  const own = useOwnThread(runQuery.data, thread);
  const agent = useAgent(runQuery.data?.agent_id);
  const history = useRunHistory(runId);
  const retained = history.read;
  const attempts = useQuery({
    ...queries.attempts(runId),
    enabled: level === "debug",
    staleTime: 60_000,
  });
  const { number } = useThreadRuns(
    level === "debug" ? (runQuery.data?.thread_id ?? "") : "",
    runId,
  );
  const run = runQuery.data;
  const timeline = useMemo(() => {
    if (!run || !retained.data) return undefined;
    const items = retained.data.items;
    return runTimeline({
      run,
      attempts: attempts.data,
      items: presentItems(items),
      execution: runExecution(run, items),
      coverage: items.some(
        (item) => isOmitted(item.content) || item.content.incomplete === true,
      )
        ? "partial"
        : (items[0]?.ordinal ?? 1) > 1
          ? "window"
          : "complete",
    });
  }, [run, retained.data, attempts.data]);
  const reload = () => {
    void runQuery.refetch();
    void retained.refetch();
    void own.refetch();
    if (level === "debug") void attempts.refetch();
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
    <StoredContents runId={runId} items={retained.data.items}>
      <EarlierContent
        history={history}
        first={retained.data.items[0]?.ordinal ?? 1}
        preservePosition={preservePosition}
      />
      {level === "debug" ? (
        <DebugRunSection
          run={run}
          thread={own.thread}
          timeline={timeline}
          index={number(run.id)}
          jumpToDock={jumpToDock}
        />
      ) : (
        <div className={debug.runAnchor} data-run={run.id}>
          <RunBlock
            run={run}
            thread={own.thread}
            timeline={timeline}
            agentName={agent.data?.name}
            agentImageUrl={agent.data?.image_url}
            action={
              <Link
                className={styles.turnLink}
                to={runPath(basePath, { ...run, run_id: run.id })}
              >
                {t("View run")}
              </Link>
            }
          >
            {/* Only the latest Run offers to resubmit; ancestors retain their outcome. */}
            {(run.failure != null ||
              ["failed", "cancelled"].includes(run.status)) && (
              <FailureNotice
                failure={run.failure}
                cancelled={run.status === "cancelled"}
              />
            )}
          </RunBlock>
        </div>
      )}
    </StoredContents>
  );
}
