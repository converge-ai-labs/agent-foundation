import { useState } from "react";
import { CaretRight } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import type { Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useThreads } from "./queries";
import { ThreadRow } from "./thread-row";
import { useResults } from "./results";
import styles from "./coordinator.module.css";

type Row = Pick<Schema<"ThreadActivityView">, "thread"> &
  Partial<Schema<"ThreadActivityView">>;

export function CoordinatorEntry({
  row,
  activeWorkerCount,
  enabled,
  selected,
  selectedUpdatedAt,
}: {
  row: Row;
  activeWorkerCount: number;
  enabled: boolean;
  selected?: Schema<"ThreadSummary">;
  selectedUpdatedAt: number;
}) {
  const results = useResults();
  const [expanded, setExpanded] = useState(false);
  const owner = row.thread.thread_id;
  const workers = useThreads(
    "",
    row.thread.configuration.project_id ?? undefined,
    false,
    {
      enabled: enabled && expanded,
      coordinatorThreadId: owner,
      limit: 5,
      includeActive: true,
    },
  );
  const rows = new Map<string, Row>(
    [
      ...(workers.data?.pages.flatMap((page) => page.rows) ?? []),
      ...(workers.data?.pages[0]?.active_rows ?? []),
    ]
      .filter((item) => item.thread.coordinator_thread_id === owner)
      .map((item) => [item.thread.thread_id, item]),
  );
  const pageObservedAt = (id: string) =>
    workers.data?.pages.find((page, index) =>
      [...page.rows, ...(index === 0 ? (page.active_rows ?? []) : [])].some(
        (item) => item.thread.thread_id === id,
      ),
    )?.observedAt ?? 0;
  for (const { thread, observedAt } of results.threads.values()) {
    if (observedAt <= pageObservedAt(thread.thread_id)) continue;
    if (
      thread.coordinator_thread_id === owner &&
      (rows.has(thread.thread_id) ||
        results.tracker?.isUnread(thread.thread_id))
    )
      rows.set(thread.thread_id, { ...rows.get(thread.thread_id), thread });
  }
  if (
    selected?.coordinator_thread_id === owner &&
    (!rows.has(selected.thread_id) ||
      selectedUpdatedAt >
        Math.max(
          pageObservedAt(selected.thread_id),
          results.threads.get(selected.thread_id)?.observedAt ?? 0,
        ))
  )
    rows.set(selected.thread_id, {
      ...rows.get(selected.thread_id),
      thread: selected,
    });
  const visibleRows = [...rows.values()]
    .filter((item) => !item.thread.archived)
    .sort((left, right) => {
      const active =
        Number(right.thread.root_activity.state !== "inactive") -
        Number(left.thread.root_activity.state !== "inactive");
      return (
        active ||
        (
          right.thread.touched_at ??
          right.thread.created_at ??
          ""
        ).localeCompare(left.thread.touched_at ?? left.thread.created_at ?? "")
      );
    });
  return (
    <>
      <div className={styles.heading}>
        <Button
          variant="ghost"
          size="icon-sm"
          className={styles.toggle}
          aria-label={`${expanded ? "Collapse" : "Expand"} workers for ${row.thread.title || "Coordinator"}`}
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          <CaretRight
            className={expanded ? styles.expandedChevron : undefined}
          />
        </Button>
        <div className={styles.conversation}>
          <ThreadRow
            row={row}
            activeWorkerCount={activeWorkerCount}
            showRestore="compact"
          />
        </div>
      </div>
      {expanded && (
        <div
          className={styles.workers}
          aria-label={`Workers for ${row.thread.title || "Coordinator"}`}
        >
          {visibleRows.map((item) => (
            <ThreadRow key={item.thread.thread_id} row={item} />
          ))}
          {workers.isPending && !workers.data && (
            <small role="status">Loading workers…</small>
          )}
          {workers.isSuccess && !visibleRows.length && (
            <small>No workers yet</small>
          )}
          <ErrorNotice
            error={workers.error}
            retry={() => void workers.refetch()}
          />
          {workers.hasNextPage && (
            <Button
              variant="ghost"
              size="sm"
              loading={workers.isFetchingNextPage}
              onClick={() => void workers.fetchNextPage()}
            >
              More workers
            </Button>
          )}
        </div>
      )}
    </>
  );
}
