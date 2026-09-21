import { useQueries, useQuery } from "@tanstack/react-query";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { conversationQueries, runPath } from "../api";

type Run = Schema["RunResource"];
type Thread = Schema["ThreadResource"];

export const chronological = <T extends { created_at: string; id: string }>(
  entries: readonly T[],
) =>
  [...entries].sort(
    (a, b) =>
      a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );

/** Every Run of one Thread in the order it happened; the Run number counts here. */
export function useThreadRuns(threadId: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const query = useQuery({
    ...conversationQueries(client, workspace.id).runs(threadId),
    enabled: !!threadId,
  });
  const runs = query.data ? chronological(query.data) : [];
  return {
    runs,
    query,
    /** 1-based position, or null while the Thread's Runs are unknown. */
    number(runId: string) {
      const index = runs.findIndex((run) => run.id === runId);
      return index < 0 ? null : index + 1;
    },
  };
}

/**
 * The child Threads this Thread delegated to, with their own Runs: what the
 * navigator lists beneath the Run that dispatched them.
 */
export function useChildThreads(sessionId: string, threadId: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const queries = conversationQueries(client, workspace.id);
  const threads = useQuery({
    ...queries.threads(sessionId),
    enabled: !!sessionId,
  });
  const children = chronological(
    (threads.data ?? []).filter(
      (thread) =>
        thread.role === "child" && thread.origin_thread_id === threadId,
    ),
  );
  const runs = useQueries({
    queries: children.map((thread) => queries.runs(thread.id)),
  });
  return children.map((thread, index) => ({
    thread,
    runs: chronological<Run>(runs[index]?.data ?? []),
  }));
}

/**
 * The Threads this Run's lineage passes through before its own Thread — a
 * fork or child Thread reads on from them — oldest first, with their Runs.
 */
export function useLineageThreads(runId: string, threadId: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const queries = conversationQueries(client, workspace.id);
  const lineage = useQuery({ ...queries.lineage(runId), enabled: !!runId });
  const ids = [...(lineage.data?.items ?? [])]
    .sort((a, b) => b.depth_from_head - a.depth_from_head)
    .map((entry) => entry.thread_id)
    .filter((id, index, all) => id !== threadId && all.indexOf(id) === index);
  const threads = useQueries({ queries: ids.map((id) => queries.thread(id)) });
  const runs = useQueries({ queries: ids.map((id) => queries.runs(id)) });
  return ids.flatMap((id, index): ThreadRuns[] => {
    const thread = threads[index]?.data;
    return thread
      ? [{ thread, runs: chronological<Run>(runs[index]?.data ?? []) }]
      : [];
  });
}

/**
 * The child Thread an asynchronous delegation started. Threads carry the Run
 * they branched from, which is the only correlation the resource exposes.
 */
export function childThreadOf(
  children: readonly ThreadRuns[],
  runId: string,
  offset = 0,
) {
  const matches = children.filter(
    (child) => child.thread.origin_run_id === runId,
  );
  return matches[offset] ?? matches[0] ?? null;
}

export interface ThreadRuns {
  thread: Thread;
  runs: readonly Run[];
}

/** Where a child Thread opens: its latest Run, which is inspected in Debug. */
export function childThreadPath(basePath: string, child: ThreadRuns) {
  return runPath(
    basePath,
    {
      session_id: child.thread.session_id,
      thread_id: child.thread.id,
      run_id: child.runs.at(-1)?.id ?? child.thread.current_run_id ?? "",
    },
    "debug",
  );
}
