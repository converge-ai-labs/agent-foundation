import { useQueries } from "@tanstack/react-query";
import pLimit from "p-limit";
import { useEffect, useMemo, useRef } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  monitoringApi,
  queryStep,
  type Panel,
  type Target,
  type TimeRange,
} from "./api";
import { normalizeResult, type ChartData } from "./series";

// Shared by charts and previews; React Query deduplicates identical keys before admission.
const limit = pLimit(4);
export function usePanelData(
  panel: Panel,
  target: Target | null,
  range: TimeRange,
  revision: number,
) {
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const api = useMemo(
    () => monitoringApi(client, workspace.id),
    [client, workspace.id],
  );
  const results = useQueries({
    queries: panel.queries.map((query) => ({
      queryKey: [
        "metric-series",
        workspace.id,
        query,
        query.kind === "metric" ? target : null,
        range,
        revision,
      ],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        limit(async () => {
          signal.throwIfAborted();
          return normalizeResult(
            await api.query(query, target, range, signal),
            query,
            queryStep(range),
          );
        }),
      enabled: query.kind === "promql" ? can("admin") : !!target,
      retry: false,
      staleTime: Infinity,
      // Retain completed results through responsive grid remounts, not whole refresh cycles.
      gcTime: 1000,
      refetchOnWindowFocus: false,
    })),
  });
  // Keep only the last result for each current query, never another target or scope.
  // useQueries does not provide previous data when a range key changes.
  const identities = panel.queries.map((query) =>
    JSON.stringify([
      workspace.id,
      query,
      query.kind === "metric" ? target : null,
      can("admin"),
    ]),
  );
  const previous = useRef(new Map<string, ChartData>());
  const retained = results.map(
    (result, index) => result.data ?? previous.current.get(identities[index]),
  );
  useEffect(() => {
    previous.current = new Map(
      identities.flatMap((identity, index) =>
        retained[index] ? [[identity, retained[index]] as const] : [],
      ),
    );
  });
  const stale = results.some(
    (result, index) => !result.data && !!retained[index],
  );
  const data: ChartData = { series: [], notices: [] };
  for (const [index, resultData] of retained.entries()) {
    if (resultData) {
      data.series.push(
        ...resultData.series.map((series) => ({
          ...series,
          id: `${index}:${series.id}`,
        })),
      );
      data.notices.push(...resultData.notices);
    }
  }
  if (panel.queries.some((query) => query.kind === "promql") && !can("admin"))
    data.notices.push(
      "Workspace administrator access is required for PromQL queries.",
    );
  if (panel.queries.some((query) => query.kind === "metric") && !target)
    data.notices.push("Select an Environment or Device to query metrics.");
  // Bound Canvas/legend work even when an external query returns many series.
  if (data.series.length > 32)
    data.notices.push(
      "Showing the first 32 series. Narrow the query to see others.",
    );
  data.series = data.series.slice(0, 32);
  data.notices = [
    ...new Map(
      data.notices.map((notice) => [JSON.stringify(notice), notice]),
    ).values(),
  ];
  return {
    data,
    stale,
    loading: results.some((result) => result.isFetching),
    errors: results.flatMap((result) => (result.error ? [result.error] : [])),
  };
}
