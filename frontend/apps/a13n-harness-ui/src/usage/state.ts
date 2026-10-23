import type { paths } from "../api.generated";
import type { Schema } from "../transport/client";

export type UsageFilters = NonNullable<
  paths["/api/usage"]["get"]["parameters"]["query"]
>;
export const pageSize = 25;
const dayMs = 86_400_000;
export function shiftDay(day: string, count: number): string {
  const value = Date.parse(`${day}T00:00:00Z`);
  return Number.isFinite(value)
    ? new Date(value + count * dayMs).toISOString().slice(0, 10)
    : "";
}
export function presetRange(days: number, now = new Date()) {
  const end = shiftDay(now.toISOString().slice(0, 10), 1);
  return { start: shiftDay(end, -days), end };
}
export function usageFilters(
  params: URLSearchParams,
  now = new Date(),
): UsageFilters {
  const days = ["7", "30", "90"].includes(params.get("range") ?? "")
    ? Number(params.get("range"))
    : 30;
  const dates =
    params.get("range") === "custom"
      ? { start: params.get("start") ?? "", end: params.get("end") ?? "" }
      : presetRange(days, now);
  const offset = Number(params.get("offset") ?? 0);
  return {
    ...dates,
    project_id: params.get("project_id") || undefined,
    projectless: params.get("projectless") === "true",
    thread_id: params.get("thread_id") || undefined,
    model: params.get("model") || undefined,
    group_by:
      params.get("group_by") === "thread"
        ? "thread"
        : params.get("group_by") === "model"
          ? "model"
          : "project",
    order_by: params.get("order_by") === "cost" ? "cost" : "tokens",
    offset: Number.isSafeInteger(offset) && offset >= 0 ? offset : 0,
    limit: pageSize,
  };
}
export function rangeError(filters: UsageFilters): string | undefined {
  const valid = (day: string) =>
    /^\d{4}-\d{2}-\d{2}$/.test(day) && shiftDay(day, 0) === day;
  const days = (Date.parse(filters.end) - Date.parse(filters.start)) / dayMs;
  if (
    !valid(filters.start) ||
    !valid(filters.end) ||
    !(days >= 1 && days <= 366)
  )
    return "Choose a date range of 1 to 366 UTC days.";
  if (filters.project_id && filters.projectless)
    return "Choose either a project or No project.";
}
const compact = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 2,
});
export function compactNumber(value: number): string {
  return compact.format(value);
}
export function estimatedCost(
  totals: Schema<"ConsumptionTotals">,
  abbreviated = true,
): string {
  if (totals.model_cost_usd === null) return "—";
  const cost = Number(totals.model_cost_usd);
  const value =
    cost > 0 && cost < 0.0001
      ? "<$0.0001"
      : abbreviated && cost >= 1000
        ? `$${compactNumber(cost)}`
        : `$${cost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: cost < 1 ? 4 : 2 })}`;
  return `${value}${totals.unknown_model_costs ? "+" : ""}`;
}
export function usageLink(scope: {
  projectId?: string | null;
  threadId?: string;
}) {
  const params = new URLSearchParams();
  if (scope.threadId) params.set("thread_id", scope.threadId);
  else if (scope.projectId) params.set("project_id", scope.projectId);
  else if (scope.projectId === null) params.set("projectless", "true");
  return `/usage${params.size ? `?${params}` : ""}`;
}
