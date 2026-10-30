import { Button } from "a13n-ui";
import type { Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useThreads } from "./queries";
import { CoordinatorEntry } from "./coordinator-entry";
import { ThreadRow } from "./thread-row";
import styles from "./conversation.module.css";

import {
  useNavigationObservations,
  type NavigationRow,
} from "./navigation-observations";

export function RecentNavigation({
  enabled,
  projectId,
  selected,
  selectedUpdatedAt,
}: {
  enabled: boolean;
  projectId?: string;
  selected?: Schema<"ThreadSummary">;
  selectedUpdatedAt: number;
}) {
  const list = useThreads("", projectId, false, {
    enabled,
    includeActive: true,
  });
  const { observed, ownerIds, activeWorkerCounts, owners } =
    useNavigationObservations({
      list,
      enabled,
      belongs: (thread) =>
        !projectId || thread.configuration.project_id === projectId,
      selected,
      selectedUpdatedAt,
    });
  const rows = [...observed.values()]
    .filter(
      ({ thread }) =>
        thread.role !== "worker" &&
        (!thread.archived || ownerIds.includes(thread.thread_id)),
    )
    .sort((left, right) => {
      const a = left.thread.touched_at ?? left.thread.created_at ?? "";
      const b = right.thread.touched_at ?? right.thread.created_at ?? "";
      return (
        b.localeCompare(a) ||
        right.thread.thread_id.localeCompare(left.thread.thread_id)
      );
    });
  const renderRow = (row: NavigationRow) =>
    row.thread.role === "coordinator" ? (
      <CoordinatorEntry
        key={row.thread.thread_id}
        row={row}
        activeWorkerCount={activeWorkerCounts.get(row.thread.thread_id) ?? 0}
        enabled={enabled}
        selected={selected}
        selectedUpdatedAt={selectedUpdatedAt}
      />
    ) : (
      <ThreadRow key={row.thread.thread_id} row={row} />
    );
  return (
    <section aria-label="Recent conversations">
      {rows.map(renderRow)}
      {list.isPending && !list.data && enabled && (
        <div role="status" className={styles.initialLoading}>
          <span>Loading conversations…</span>
        </div>
      )}
      {list.isSuccess && list.data?.pages[0]?.total === 0 && !rows.length && (
        <small className={styles.emptyGroup}>No conversations yet</small>
      )}
      <ErrorNotice error={owners.error} retry={() => void owners.refetch()} />
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.hasNextPage && (
        <Button
          variant="ghost"
          size="xs"
          className={styles.showMore}
          loading={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
          aria-label="Show more recent conversations"
        >
          Show more
        </Button>
      )}
    </section>
  );
}
