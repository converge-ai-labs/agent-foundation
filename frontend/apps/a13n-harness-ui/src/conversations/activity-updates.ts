import type { InfiniteData, Query, QueryClient } from "@tanstack/react-query";
import { result, type Schema, type Transport } from "../transport/client";
import { scheduleRefresh } from "./refresh";

type Page = Schema<"ThreadActivityPage"> & { observedAt: number };
type Row = Schema<"ThreadActivityView">;
type Pending = {
  ids: Set<string>;
  timer?: ReturnType<typeof setTimeout>;
  running: boolean;
};
const pending = new WeakMap<QueryClient, Pending>();

function belongs(query: Query, row: Row, client: QueryClient) {
  const [, , projectId, archived, scope, , archivedOnly] = query.queryKey;
  const thread = row.thread;
  if (archivedOnly ? !thread.archived : !archived && thread.archived)
    return false;
  const project = thread.configuration.project_id;
  if (projectId && project !== projectId) return false;
  if (scope === "projectless" && project != null) return false;
  if (scope === "unavailable") {
    const projects = client.getQueryData<Schema<"ProjectSummary">[]>([
      "projects",
    ]);
    if (
      project == null ||
      projects?.some((item) => item.project_id === project)
    )
      return false;
  }
  // Search membership is server-owned (Unicode folding and excerpt matching).
  return true;
}

export function applyActivityUpdates(
  client: QueryClient,
  rows: Row[],
  ids: string[],
  observed?: ReadonlyMap<Query, unknown>,
) {
  const updates = new Map(rows.map((row) => [row.thread.thread_id, row]));
  for (const query of client
    .getQueryCache()
    .findAll({ queryKey: ["threads"] })) {
    const data = query.state.data as InfiniteData<Page> | undefined;
    if (!data?.pages) continue;
    const existing = new Map(
      data.pages
        .flatMap((page) => [...page.rows, ...(page.active_rows ?? [])])
        .map((row) => [row.thread.thread_id, row]),
    );
    let reconcile = false;
    let affected = false;
    for (const id of ids) {
      const old = existing.get(id);
      const next = updates.get(id);
      if (!old && (!next || !belongs(query, next, client))) continue;
      affected = true;
      // Content-only checkpoints replace one normalized row. Membership, order,
      // search and active/inactive transitions reconcile only affected lists.
      if (
        !old ||
        !next ||
        !belongs(query, next, client) ||
        query.queryKey[1] ||
        old.thread.touched_at !== next.thread.touched_at ||
        old.thread.archived !== next.thread.archived ||
        old.thread.configuration.project_id !==
          next.thread.configuration.project_id ||
        (old.thread.root_activity.state === "inactive") !==
          (next.thread.root_activity.state === "inactive")
      ) {
        reconcile = true;
      }
    }
    if (!affected) continue;
    if (
      query.state.fetchStatus === "fetching" ||
      (observed && observed.get(query) !== data)
    ) {
      scheduleRefresh(client, (candidate) => candidate === query);
      continue;
    }
    client.setQueryData<InfiniteData<Page>>(query.queryKey, {
      ...data,
      pages: data.pages.map((page) => ({
        ...page,
        rows: page.rows.flatMap((row) => {
          const id = row.thread.thread_id;
          if (!ids.includes(id)) return [row];
          const next = updates.get(id);
          return next && belongs(query, next, client) ? [next] : [];
        }),
        active_rows: (page.active_rows ?? []).flatMap((row) => {
          const id = row.thread.thread_id;
          if (!ids.includes(id)) return [row];
          const next = updates.get(id);
          return next && belongs(query, next, client) ? [next] : [];
        }),
      })),
    });
    if (reconcile) scheduleRefresh(client, (candidate) => candidate === query);
  }
}

export function refreshActivity(
  client: QueryClient,
  transport: Transport,
  threadId: string,
) {
  let work = pending.get(client);
  if (!work) {
    work = { ids: new Set(), running: false };
    pending.set(client, work);
  }
  work.ids.add(threadId);
  if (work.timer || work.running) return;
  const batch = work;
  batch.timer = setTimeout(() => {
    batch.timer = undefined;
    batch.running = true;
    void (async () => {
      while (batch.ids.size) {
        const ids = [...batch.ids].slice(0, 100);
        ids.forEach((id) => batch.ids.delete(id));
        const queries = client
          .getQueryCache()
          .findAll({ queryKey: ["threads"] });
        if (!queries.length) continue;
        // Pagination may have begun before this hint. Observe after it, never
        // cancel it or let its older response overwrite the incremental result.
        await Promise.allSettled(queries.map((query) => query.promise));
        const observed = new Map(
          queries.map((query) => [query, query.state.data]),
        );
        try {
          const rows = await result(
            transport.client.POST("/api/threads/activity/lookup", {
              body: { thread_ids: ids },
            }),
          );
          if (
            !queries.some(
              (query) => client.getQueryCache().get(query.queryHash) === query,
            )
          )
            return;
          applyActivityUpdates(client, rows, ids, observed);
        } catch {
          // A failed lookup cannot establish membership: the hint may introduce
          // a new row. Reconcile captured collections once, without spinning or
          // relying on valid resume (which deliberately skips full refreshes).
          scheduleRefresh(client, (query) => queries.includes(query));
        }
      }
    })().finally(() => {
      batch.running = false;
    });
  }, 100);
}
