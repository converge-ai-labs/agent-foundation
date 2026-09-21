import { useQuery } from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";

export function useThreadUsage(threadId: string) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["thread", threadId, "usage"],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/usage", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
}
export function useContextUsage(threadId: string) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["thread", threadId, "context-usage"],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/context-usage", {
          params: { path: { thread_id: threadId } },
          signal,
        }),
      ),
  });
}
export type ThreadUsage = NonNullable<
  ReturnType<typeof useThreadUsage>["data"]
>;
export function tokenCounts(totals?: ThreadUsage["root"]) {
  return new Map(
    (totals?.tokens ?? []).flatMap(([name, count]) =>
      typeof name === "string" && typeof count === "number"
        ? [[name, count] as const]
        : [],
    ),
  );
}
export function modelCost(totals?: ThreadUsage["root"]) {
  const cost =
    totals && totals.model_requests > totals.unknown_model_costs
      ? Number(totals.model_cost_usd)
      : undefined;
  return cost !== undefined && Number.isFinite(cost)
    ? `${cost > 0 && cost < 0.0001 ? "<$0.0001" : `$${cost.toFixed(cost < 1 ? 4 : 2)}`}${totals?.unknown_model_costs ? "+" : ""}`
    : "—";
}
export function usageSummary(
  usage?: ThreadUsage,
  context?: Schema<"ContextUsageView">,
) {
  const tokens = tokenCounts(usage?.combined);
  const total =
    (tokens.get("input_tokens") ?? 0) + (tokens.get("output_tokens") ?? 0);
  const totalTokens =
    usage?.first_observed_at && usage.combined.model_requests > 0
      ? total
      : undefined;
  const cache = tokens.get("cache_read_tokens");
  const used = context?.latest_request_tokens;
  const window = context?.context_window;
  return {
    totalTokens,
    tokens:
      totalTokens === undefined
        ? "—"
        : totalTokens >= 1_000_000
          ? `${(totalTokens / 1_000_000).toFixed(1)}M`
          : totalTokens >= 1000
            ? `${(totalTokens / 1000).toFixed(1)}K`
            : String(totalTokens),
    context:
      used != null && window != null && window > 0
        ? `${Math.round((100 * used) / window)}%`
        : "—",
    cost: modelCost(usage?.first_observed_at ? usage.combined : undefined),
    cache:
      usage?.first_observed_at && total > 0 && cache != null
        ? `${((100 * cache) / total).toFixed(1)}%`
        : "—",
  };
}
export function elapsedTime(
  operation: Schema<"RootOperationView"> | null | undefined,
  now: number,
) {
  if (!operation) return "—";
  const active =
    operation.status === "preparing" || operation.status === "running";
  const end = operation.completed_at
    ? Date.parse(operation.completed_at)
    : active
      ? now
      : NaN;
  const duration =
    end - Date.parse(operation.started_at ?? operation.receipt.submitted_at);
  if (!Number.isFinite(duration)) return "—";
  const seconds = Math.max(0, Math.floor(duration / 1000));
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
