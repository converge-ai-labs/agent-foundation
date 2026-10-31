import type { Schema } from "../transport/client";
import { useThreadOwners, type useThreads } from "./queries";
import { useResults } from "./results";

export type NavigationRow = Pick<Schema<"ThreadActivityView">, "thread"> &
  Partial<Schema<"ThreadActivityView">>;

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
  // The selected detail can arrive before a slower sidebar refresh. Use that
  // observation for this row, without replacing other Projects or page cursors.
  const observed = new Map<string, NavigationRow>(
    [
      ...(list.data?.pages.flatMap((page) => page.rows) ?? []),
      ...(list.data?.pages[0]?.active_rows ?? []),
      ...(list.data?.pages[0]?.starred_rows ?? []),
    ]
      .filter((row) => belongs(row.thread))
      .map((row) => [row.thread.thread_id, row]),
  );
  for (const { thread, observedAt } of results.threads.values()) {
    const page = list.data?.pages.find((page, index) =>
      [
        ...page.rows,
        ...(index === 0 ? (page.active_rows ?? []) : []),
        ...(index === 0 ? (page.starred_rows ?? []) : []),
      ].some((row) => row.thread.thread_id === thread.thread_id),
    );
    if (observed.has(thread.thread_id) && observedAt <= (page?.observedAt ?? 0))
      continue;
    if (!belongs(thread)) {
      observed.delete(thread.thread_id);
      continue;
    }
    if (
      observed.has(thread.thread_id) ||
      results.tracker?.isUnread(thread.thread_id)
    )
      observed.set(thread.thread_id, {
        ...observed.get(thread.thread_id),
        thread,
      });
  }
  const selectedPage = selected
    ? list.data?.pages.find((page, index) =>
        (index === 0
          ? [
              ...page.rows,
              ...(page.active_rows ?? []),
              ...(page.starred_rows ?? []),
            ]
          : page.rows
        ).some((row) => row.thread.thread_id === selected.thread_id),
      )
    : undefined;
  if (
    selected &&
    belongs(selected) &&
    (!observed.has(selected.thread_id) ||
      selectedUpdatedAt >
        Math.max(
          selectedPage?.observedAt ?? 0,
          results.threads.get(selected.thread_id)?.observedAt ?? 0,
        ))
  ) {
    observed.set(selected.thread_id, {
      ...observed.get(selected.thread_id),
      thread: selected,
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
      belongs(thread) &&
      ownerIds.includes(thread.thread_id) &&
      !observed.has(thread.thread_id)
    )
      observed.set(thread.thread_id, { thread });
  }
  // Use the same reconciled observations for grouping and activity summaries.
  // Worker disclosure queries are lazy and must not reset these counts.
  const activeWorkerCounts = new Map<string, number>();
  for (const { thread } of observed.values()) {
    if (
      !thread.archived &&
      thread.role === "worker" &&
      thread.coordinator_thread_id &&
      thread.root_activity.state !== "inactive"
    ) {
      const owner = thread.coordinator_thread_id;
      activeWorkerCounts.set(owner, (activeWorkerCounts.get(owner) ?? 0) + 1);
    }
  }
  return { observed, ownerIds, activeWorkerCounts, results, owners };
}
