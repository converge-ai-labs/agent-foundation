import { useState } from "react";
import { CaretRight } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { Button } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { refreshThreadLists, useThread, useThreads } from "./queries";
import { ThreadRow } from "./thread-row";
import { useResults } from "./results";
import styles from "./project-lead.module.css";

import { LeadIcon } from "./lead-icon";

export function useProjectLeadMode(projectId?: string) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      result(
        client.PATCH("/api/projects/{project_id}/lead", {
          params: { path: { project_id: projectId! } },
          body: { enabled },
        }),
      ),
    onSuccess: (thread, enabled) => {
      if (enabled) navigate(`/threads/${encodeURIComponent(thread.thread_id)}`);
    },
    onSettled: () => {
      void queries.invalidateQueries({ queryKey: ["projects"] });
      void refreshThreadLists(queries);
    },
    retry: false,
  });
}

export function ProjectLeadEntry({
  project,
  presence,
  enabled,
  mode,
  selected,
  selectedUpdatedAt,
}: {
  project: Schema<"ProjectSummary">;
  presence: Schema<"PresenceFrame"> | null;
  enabled: boolean;
  mode: ReturnType<typeof useProjectLeadMode>;
  selected?: Schema<"ThreadSummary">;
  selectedUpdatedAt: number;
}) {
  const navigate = useNavigate();
  const results = useResults();
  const lead = useThread(enabled ? (project.lead_thread_id ?? "") : "");
  const [expanded, setExpanded] = useState(false);
  const workers = useThreads("", project.project_id, false, {
    enabled: enabled && expanded && !!project.lead_thread_id,
    leadThreadId: project.lead_thread_id ?? undefined,
    limit: 5,
    includeActive: true,
  });
  type Row = Pick<Schema<"ThreadActivityView">, "thread"> &
    Partial<Schema<"ThreadActivityView">>;
  const rows = new Map<string, Row>(
    [
      ...(workers.data?.pages.flatMap((page) => page.rows) ?? []),
      ...(workers.data?.pages[0]?.active_rows ?? []),
    ]
      .filter((row) => row.thread.lead_thread_id === project.lead_thread_id)
      .map((row) => [row.thread.thread_id, row]),
  );
  const pageObservedAt = (id: string) =>
    workers.data?.pages.find((page, index) =>
      [...page.rows, ...(index === 0 ? (page.active_rows ?? []) : [])].some(
        (row) => row.thread.thread_id === id,
      ),
    )?.observedAt ?? 0;
  for (const { thread, observedAt } of results.threads.values()) {
    if (observedAt <= pageObservedAt(thread.thread_id)) continue;
    if (
      thread.lead_thread_id === project.lead_thread_id &&
      (rows.has(thread.thread_id) ||
        results.tracker?.isUnread(thread.thread_id))
    )
      rows.set(thread.thread_id, { ...rows.get(thread.thread_id), thread });
  }
  if (
    selected?.lead_thread_id === project.lead_thread_id &&
    selected &&
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
    .filter((row) => !row.thread.archived)
    .sort((left, right) => {
      const active =
        Number(right.thread.root_activity.state !== "inactive") -
        Number(left.thread.root_activity.state !== "inactive");
      if (active) return active;
      return (
        right.thread.touched_at ??
        right.thread.created_at ??
        ""
      ).localeCompare(left.thread.touched_at ?? left.thread.created_at ?? "");
    });
  if (project.lead_thread_id)
    return (
      <>
        <div className={styles.heading}>
          <Button
            variant="ghost"
            size="icon-sm"
            className={styles.toggle}
            aria-label={
              expanded
                ? "Collapse Coordinator workers"
                : "Expand Coordinator workers"
            }
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            <CaretRight
              className={expanded ? styles.expandedChevron : undefined}
            />
          </Button>
          <div className={styles.conversation}>
            {lead.data ? (
              <ThreadRow
                row={{
                  thread: lead.data.thread,
                  pending_decision: lead.data.deferred_requests?.length
                    ? {
                        kind: "mixed",
                        count: lead.data.deferred_requests.length,
                      }
                    : null,
                }}
                presence={presence}
                projectLead={project.lead_enabled}
                showRestore
              />
            ) : (
              <Button
                variant="ghost"
                className={styles.entry}
                onClick={() =>
                  navigate(
                    `/threads/${encodeURIComponent(project.lead_thread_id!)}`,
                  )
                }
              >
                <LeadIcon />
                <span>Coordinator</span>
              </Button>
            )}
          </div>
        </div>
        {expanded && (
          <div className={styles.workers} aria-label="Coordinator workers">
            {visibleRows.map((row) => (
              <ThreadRow
                key={row.thread.thread_id}
                row={row}
                presence={presence}
              />
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
        <ErrorNotice error={lead.error} retry={() => void lead.refetch()} />
      </>
    );
  return (
    <>
      <Button
        variant="ghost"
        className={styles.entry}
        title="Plan work, coordinate workers, and bring results back"
        loading={mode.isPending}
        onClick={() => mode.mutate(true)}
      >
        <LeadIcon />
        <span>Coordinator</span>
      </Button>
    </>
  );
}
