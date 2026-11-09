import { i18n } from "../i18n";
import type { Schema } from "../transport/client";

export function windowLabel(seconds: number): string {
  if (seconds === 604800) return i18n.t("Weekly");
  if (seconds === 86400) return i18n.t("Daily");
  if (seconds === 2592000) return i18n.t("30-day");
  if (seconds % 86400 === 0)
    return i18n.t("{{count}}-day", { count: seconds / 86400 });
  if (seconds % 3600 === 0)
    return i18n.t("{{count}}h", { count: seconds / 3600 });
  if (seconds % 60 === 0) return i18n.t("{{count}}m", { count: seconds / 60 });
  return i18n.t("{{count}}s", { count: seconds });
}

export function availableVouchers(
  credits: Schema<"ResetCredits"> | null | undefined,
  now = Date.now(),
) {
  return (credits?.credits ?? [])
    .filter(
      (item) =>
        item.status === "available" &&
        item.reset_type === "codex_rate_limits" &&
        (!item.expires_at || Date.parse(item.expires_at) > now),
    )
    .sort(
      (a, b) =>
        (a.expires_at ? Date.parse(a.expires_at) : Infinity) -
        (b.expires_at ? Date.parse(b.expires_at) : Infinity),
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
  return new Date(timestamp).toLocaleString(i18n.language, {
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
      .map((window) =>
        i18n.t("{{label}} {{percent}}% left", {
          label: windowLabel(window.limit_window_seconds),
          percent: Math.round(
            Math.max(0, Math.min(100, 100 - window.used_percent)),
          ),
        }),
      )
      .join(" - ");
  }
  const value = grokAllowance(usage.config);
  return value.usedPercent == null
    ? ""
    : i18n.t("{{label}} {{percent}}% used", {
        label: i18n.t(value.label),
        percent: Math.floor(value.usedPercent),
      });
}
