import type { Schema } from "../transport/client";
import { useThreadOwners, type useThreads } from "./queries";
import { useResults } from "./results";

export type NavigationRow = Pick<Schema<"ThreadActivityView">, "thread"> &
  Partial<Schema<"ThreadActivityView">> & { observedAt?: number };

// Project and chronological navigation share observation freshness and ownership.
export function useNavigationObservations({
  list,
  enabled,
  belongs,
  selected,
  selectedUpdatedAt,
}: {
  list: Pick<ReturnType<typeof useThreads>, "data">;
  enabled: boolean;
  belongs: (thread: Schema<"ThreadSummary">) => boolean;
  selected?: Schema<"ThreadSummary">;
  selectedUpdatedAt: number;
}) {
  const results = useResults();
  const included = (thread: Schema<"ThreadSummary">) =>
    thread.thread_id !== "thread_bot" && belongs(thread);
  // The selected detail can arrive before a slower sidebar refresh. Use that
  // observation for this row, without replacing other Projects or page cursors.
  const observed = new Map<string, NavigationRow>();
  const observeRows = (rows: NavigationRow[], pageObservedAt: number) => {
    for (const row of rows)
      if (included(row.thread))
        observed.set(row.thread.thread_id, {
          ...row,
          observedAt: row.observedAt ?? pageObservedAt,
        });
  };
  const pages = list.data?.pages ?? [];
  for (const page of pages) observeRows(page.rows, page.observedAt);
  const firstPage = pages[0];
  if (firstPage) {
    observeRows(firstPage.active_rows ?? [], firstPage.observedAt);
    observeRows(firstPage.starred_rows ?? [], firstPage.observedAt);
  }
  for (const { thread, observedAt } of results.threads.values()) {
    const row = observed.get(thread.thread_id);
    if (row && observedAt <= (row.observedAt ?? 0)) continue;
    if (!included(thread)) {
      observed.delete(thread.thread_id);
      continue;
    }
    if (
      observed.has(thread.thread_id) ||
      (thread.role !== "worker" && results.tracker?.isUnread(thread.thread_id))
    )
      observed.set(thread.thread_id, { ...row, thread, observedAt });
  }
  if (
    selected &&
    included(selected) &&
    selectedUpdatedAt >
      Math.max(
        observed.get(selected.thread_id)?.observedAt ?? -1,
        results.threads.get(selected.thread_id)?.observedAt ?? -1,
      )
  ) {
    observed.set(selected.thread_id, {
      ...observed.get(selected.thread_id),
      thread: selected,
      observedAt: selectedUpdatedAt,
    });
  }
  // Metadata/activity responses can arrive after a read-state mutation. Their
  // request timestamp does not supersede that independent durable revision.
  for (const [id, row] of observed) {
    const reading = results.threads.get(id)?.thread;
    if (
      reading &&
      (reading.read_state_version ?? 0) > (row.thread.read_state_version ?? 0)
    )
      observed.set(id, {
        ...row,
        thread: {
          ...row.thread,
          unread: reading.unread,
          read_state_version: reading.read_state_version,
          completion: reading.completion,
        },
      });
  }
  const ownerIds = [
    ...new Set(
      [...observed.values()]
        .filter(
          (row) => !row.thread.archived && row.thread.coordinator_thread_id,
        )
        .map((row) => row.thread.coordinator_thread_id!),
    ),
  ];
  const owners = useThreadOwners(
    ownerIds.filter((id) => !observed.has(id)),
    enabled,
  );
  for (const thread of owners.data ?? []) {
    if (
      included(thread) &&
      ownerIds.includes(thread.thread_id) &&
      !observed.has(thread.thread_id)
    )
      observed.set(thread.thread_id, {
        thread,
        observedAt: owners.dataUpdatedAt,
      });
  }
  // Use the same reconciled observations for grouping and activity summaries.
  // Worker disclosure queries are lazy and must not reset these counts.
  const activeWorkerCounts = new Map<string, number>();
  const ownersWithObservedWorkers = new Set<string>();
  for (const { thread, observedAt } of observed.values()) {
    const owner = thread.coordinator_thread_id;
    if (thread.archived || thread.role !== "worker" || !owner) continue;
    if (thread.root_activity.state !== "inactive")
      activeWorkerCounts.set(owner, (activeWorkerCounts.get(owner) ?? 0) + 1);
    // A newer active or selected worker can precede its owner's refreshed
    // count. A fresh zero remains authoritative over earlier worker evidence.
    const ownerRow = observed.get(owner);
    if (
      ownerRow?.thread.unarchived_worker_count == null ||
      (observedAt ?? 0) > (ownerRow.observedAt ?? 0)
    )
      ownersWithObservedWorkers.add(owner);
  }
  return {
    observed,
    ownerIds,
    activeWorkerCounts,
    ownersWithObservedWorkers,
    results,
    owners,
  };
}
