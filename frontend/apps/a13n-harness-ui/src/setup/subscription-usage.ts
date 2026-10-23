import type { Schema } from "../transport/client";

export function windowLabel(seconds: number): string {
  if (seconds === 604800) return "Weekly";
  if (seconds === 86400) return "Daily";
  if (seconds === 2592000) return "30-day";
  if (seconds % 86400 === 0) return `${seconds / 86400}-day`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

export function availableVouchers(
  credits: Schema<"ResetCredits"> | null | undefined,
  now = Date.now(),
) {
  return (credits?.credits ?? []).filter(
    (item) =>
      item.status === "available" &&
      item.reset_type === "codex_rate_limits" &&
      (!item.expires_at || Date.parse(item.expires_at) > now),
  );
}

export function voucherSummary(
  usage: Schema<"CodexUsage"> | null | undefined,
  now = Date.now(),
) {
  const available = availableVouchers(usage?.reset_credits, now);
  const expiries = available.flatMap((item) =>
    item.expires_at ? [Date.parse(item.expires_at)] : [],
  );
  return {
    count: usage?.reset_credits
      ? available.length
      : (usage?.rate_limit_reset_credits?.available_count ?? null),
    expiresAt: expiries.length ? Math.min(...expiries) : null,
  };
}

export function quotaTime(timestamp: number, compact = false) {
  return new Date(timestamp).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: compact ? undefined : "short",
    ...(compact ? { hourCycle: "h23" as const } : {}),
  });
}

export function grokAllowance(
  billing: Schema<"GrokBilling"> | null | undefined,
) {
  const used =
    billing?.credit_usage_percent ??
    (billing?.monthly_limit?.val &&
    billing.monthly_limit.val > 0 &&
    billing.used != null
      ? ((billing.used.val ?? 0) / billing.monthly_limit.val) * 100
      : null);
  const period = billing?.current_period;
  return {
    usedPercent: used == null ? null : Math.max(0, Math.min(100, used)),
    label:
      period?.type === "USAGE_PERIOD_TYPE_WEEKLY"
        ? "Weekly"
        : period?.type === "USAGE_PERIOD_TYPE_MONTHLY"
          ? "Monthly"
          : "Current period",
    resetAt: period?.end ?? billing?.billing_period_end ?? null,
  };
}

export function compactAllowance(usage: Schema<"UsageSnapshot">["usage"]) {
  if (!usage) return "";
  if ("account_id" in usage) {
    return [
      usage.rate_limit?.primary_window,
      usage.rate_limit?.secondary_window,
    ]
      .filter((window) => window != null)
      .map(
        (window) =>
          `${windowLabel(window.limit_window_seconds)} ${Math.round(Math.max(0, Math.min(100, 100 - window.used_percent)))}% left`,
      )
      .join(" · ");
  }
  const value = grokAllowance(usage.config);
  return value.usedPercent == null
    ? ""
    : `${value.label} ${Math.floor(value.usedPercent)}% used`;
}
