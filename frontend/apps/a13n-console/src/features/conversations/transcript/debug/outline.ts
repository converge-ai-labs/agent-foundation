import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../../../layout/workspace";
import { useClient } from "../../../../auth/context";
import type { Schema } from "../../../../shared/api";
import { conversationQueries, runPath } from "../../api";
import { useThreadRuns } from "../thread-runs";
import { runSection } from "./view";

type Run = Schema["ThreadRunItem"];
type Thread = Schema["ThreadView"];
export interface OutlineRun {
  run: Run;
  label: string;
  cost: string | null;
  calls: number;
  unpriced: boolean;
}
export type RunSort = {
  field: "order" | "cost" | "calls" | "duration";
  direction: "asc" | "desc";
};
export interface RunOutline {
  nodes: OutlineRun[];
  sorted: OutlineRun[];
  sort: RunSort;
  setSort(sort: RunSort): void;
  inView: string;
  label: string;
  total: number;
  loading: boolean;
  error: unknown;
  retry(): void;
  hasOlder: boolean;
  hasNewer: boolean;
  loadOlder(): void;
  loadNewer(): void;
  rankingLoading: boolean;
  rankingError: unknown;
  retryRanking(): void;
  hasMore: boolean;
  loadMore(): void;
  open(run: Run): void;
}

/** Chronological navigation and the ranked list keep independent, bounded pages. */
export function useRunOutline(thread: Thread, runId: string): RunOutline {
  const { t } = useTranslation();
  const { basePath, workspace } = useWorkspace();
  const navigate = useNavigate();
  const client = useClient();
  const [search, setSearch] = useSearchParams();
  const field = search.get("run_sort");
  const sort: RunSort = {
    field:
      field === "cost" || field === "calls" || field === "duration"
        ? field
        : "order",
    direction: search.get("run_direction") === "desc" ? "desc" : "asc",
  };
  const pages = useThreadRuns(thread.id, runId);
  const ranking = useInfiniteQuery({
    ...conversationQueries(client, workspace.id).rankedRuns(
      thread.id,
      sort.field === "order" ? "cost" : sort.field,
      sort.direction,
    ),
    enabled: sort.field !== "order" && pages.total > 1,
  });
  const { runs, number } = pages;
  const visible = useVisibleRun(runId);
  const inView = runs.some((run) => run.id === visible) ? visible : runId;
  const position = number(inView);
  const node = (run: Run): OutlineRun => ({
    run,
    label: t("Run {{index}}", { index: run.number }),
    cost: run.cost,
    calls: run.model_calls,
    unpriced: run.unpriced_requests > 0,
  });
  const nodes = runs.map(node);
  const ranked = useMemo(() => {
    const unique = new Map<string, Run>();
    for (const page of ranking.data?.pages ?? [])
      for (const run of page.items) unique.set(run.id, run);
    return [...unique.values()];
  }, [ranking.data]);
  const sorted =
    sort.field === "order"
      ? sort.direction === "asc"
        ? nodes
        : [...nodes].reverse()
      : ranked.map(node);
  return {
    ...pages,
    nodes,
    sorted,
    sort,
    setSort(next) {
      setSearch(
        (previous) => {
          const params = new URLSearchParams(previous);
          params.set("run_sort", next.field);
          params.set("run_direction", next.direction);
          return params;
        },
        { replace: true },
      );
    },
    rankingLoading: ranking.isPending || ranking.isFetchingNextPage,
    rankingError: ranking.error,
    retryRanking: () => {
      void ranking.refetch();
    },
    hasMore: ranking.hasNextPage,
    loadMore: () => {
      void ranking.fetchNextPage();
    },
    inView,
    label:
      position === null
        ? t("Runs")
        : t("Run {{index}} of {{total}}", {
            index: position,
            total: pages.total,
          }),
    open(run) {
      const loaded = runSection(run.id);
      const params = new URLSearchParams(search);
      params.delete("follow");
      if (loaded && !loaded.closest("[inert]")) {
        setSearch(params, { replace: true });
        loaded.scrollIntoView({ block: "start", behavior: "smooth" });
      } else {
        params.set("view", "debug");
        navigate(
          `${runPath(basePath, { session_id: run.session_id, thread_id: thread.id, run_id: run.id })}?${params}`,
        );
      }
    },
  };
}

/** Which loaded Run section the reader is actually looking at. */
function useVisibleRun(fallback: string) {
  const [visible, setVisible] = useState<{
    selection: string;
    run: string;
  } | null>(null);
  useEffect(() => {
    const stage = document.querySelector("[data-session-stage]");
    if (!(stage instanceof HTMLElement)) return;
    if (typeof IntersectionObserver === "undefined") return;
    const seen = new Map<string, number>();
    const observer = new IntersectionObserver(
      (records) => {
        for (const record of records) {
          const id = (record.target as HTMLElement).dataset.run;
          if (id) seen.set(id, record.intersectionRatio);
        }
        const sections = [...stage.querySelectorAll<HTMLElement>("[data-run]")];
        const top = sections.find(
          (section) =>
            !section.closest("[inert]") &&
            (seen.get(section.dataset.run ?? "") ?? 0) > 0,
        );
        setVisible(
          top?.dataset.run
            ? { selection: fallback, run: top.dataset.run }
            : null,
        );
      },
      { root: stage, threshold: [0, 0.01, 0.5] },
    );
    const attach = () => {
      for (const section of stage.querySelectorAll("[data-run]"))
        observer.observe(section);
    };
    attach();
    const mutations = new MutationObserver(attach);
    mutations.observe(stage, { childList: true, subtree: true });
    return () => {
      observer.disconnect();
      mutations.disconnect();
    };
  }, [fallback]);
  return visible?.selection === fallback ? visible.run : fallback;
}
