import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useParams } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { conversationQueries, runPath } from "../api";

type Thread = Schema["ThreadView"];

export const chronological = <T extends { created_at: string; id: string }>(
  entries: readonly T[],
) =>
  [...entries].sort(
    (a, b) =>
      a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );

/** Independently cached pages keep streaming changes from refetching all loaded history. */
export function useThreadRuns(threadId: string, selectedRunId?: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const route = useParams();
  const anchor =
    selectedRunId ?? (route.threadId === threadId ? route.runId : undefined);
  const scope = `${workspace.id}:${threadId}`;
  const [loaded, setLoaded] = useState<{
    scope: string;
    anchor: string | undefined;
    cursors: string[];
  }>({ scope, anchor, cursors: [] });
  const current =
    loaded.scope === scope ? loaded : { scope, anchor, cursors: [] };
  const { cursors } = current;
  const options = conversationQueries(client, workspace.id);
  const pages = useQueries({
    queries: [
      {
        ...options.runs(threadId, { anchor: current.anchor }),
        enabled: !!threadId,
      },
      ...cursors.map((cursor) => options.runs(threadId, { cursor })),
    ],
  });
  const numbered = new Map<
    string,
    { run: Schema["RunListItem"]; number: number }
  >();
  for (const page of pages)
    page.data?.items.forEach((run, index) => {
      const held = numbered.get(run.id);
      if (!held || held.run.version < run.version)
        numbered.set(run.id, { run, number: page.data!.first_number - index });
    });
  const runs = chronological([...numbered.values()].map(({ run }) => run));
  const ordered = pages
    .flatMap((page) => (page.data ? [page.data] : []))
    .sort((a, b) => a.first_number - b.first_number);
  // Refreshes can shorten a page at the character budget. Repair only uncovered
  // intervals; retaining fixed cursor pages must not silently hide boundary Runs.
  const gaps: string[] = [];
  let edge: Schema["ThreadRunPage"] | undefined;
  for (const page of [...ordered].reverse()) {
    if (!page.items.length) continue;
    if (
      edge &&
      page.first_number < edge.first_number - edge.items.length &&
      edge.next_cursor &&
      !cursors.includes(edge.next_cursor)
    )
      gaps.push(edge.next_cursor);
    if (
      !edge ||
      page.first_number - page.items.length <
        edge.first_number - edge.items.length
    )
      edge = page;
  }
  useEffect(() => {
    if (gaps.length)
      setLoaded({ ...current, cursors: [...new Set([...cursors, ...gaps])] });
    // A selected row retains the current anchor and all loaded pages. Only an
    // explicit link outside this window establishes a new bounded window.
    else if (
      loaded.scope !== scope ||
      (anchor !== current.anchor &&
        anchor &&
        !numbered.has(anchor) &&
        pages.every((page) => !page.isFetching && !page.isPending))
    )
      setLoaded({ scope, anchor, cursors: [] });
  }, [current, cursors, gaps, loaded.scope, scope, anchor, numbered, pages]);
  const older = edge?.next_cursor;
  const newer = ordered.at(-1)?.previous_cursor;
  const load = (cursor: string | null | undefined) => {
    if (cursor)
      setLoaded({ ...current, cursors: [...new Set([...cursors, cursor])] });
  };
  return {
    runs,
    total: Math.max(0, ...ordered.map((page) => page.total)),
    query: pages[0]!,
    loading: pages.some((page) => page.isFetching),
    error: pages.find((page) => page.error)?.error,
    retry: () => {
      for (const page of pages) if (page.error) void page.refetch();
    },
    hasOlder: !!older,
    hasNewer: !!newer,
    loadOlder: () => load(older),
    loadNewer: () => load(newer),
    number: (runId: string) => numbered.get(runId)?.number ?? null,
  };
}

/**
 * The Threads that branched from this one. Delegation
 * links select their matching child with childThreadOf.
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
        thread.origin !== "new" && thread.origin_thread_id === threadId,
    ),
  );
  return children;
}

/**
 * The child Thread an asynchronous delegation started. Threads carry the Run
 * they branched from, which is the only correlation the resource exposes.
 */
export function childThreadOf(
  children: readonly Thread[],
  runId: string,
  offset = 0,
) {
  const matches = children.filter(
    (child) => child.origin === "child" && child.origin_run_id === runId,
  );
  return matches[offset] ?? matches[0] ?? null;
}

/** Where a child Thread opens: its latest Run, which is inspected in Debug. */
export function childThreadPath(basePath: string, child: Thread) {
  return runPath(
    basePath,
    {
      session_id: child.session_id,
      thread_id: child.id,
      run_id: child.current_run_id ?? child.last_run_id ?? "",
    },
    "debug",
  );
}
