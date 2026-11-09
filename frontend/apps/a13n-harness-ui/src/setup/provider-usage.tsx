import { useTranslation } from "../i18n";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { ArrowsClockwiseIcon, WalletIcon } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { grokAllowance, quotaTime } from "./subscription-usage";
import styles from "./subscriptions.module.css";

function dollars(cents: number) {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
  }).format(cents / 100);
}

export function GrokAllowance({
  usage,
}: {
  usage: Schema<"GrokUsage"> | null | undefined;
}) {
  const { t } = useTranslation();
  const billing = usage?.config;
  const label = allowanceLabel(usage?.config?.current_period?.type, t);
  const allowance = grokAllowance(billing);
  return (
    <>
      <div className={styles.quotaWindows}>
        <div className={styles.meter}>
          {allowance.usedPercent === null ? (
            <span>{t("Allowance unavailable")}</span>
          ) : (
            <>
              <div className={styles.meterHeading}>
                <span>{label}</span>
                <span>
                  {t("{{percent}}% used", {
                    percent: Math.floor(allowance.usedPercent),
                  })}
                </span>
              </div>
              <meter
                min={0}
                max={100}
                value={allowance.usedPercent}
                aria-label={t("{{label}} used", { label })}
              />
            </>
          )}
          <span
            className={styles.quotaDetail}
            title={
              allowance.resetAt
                ? quotaTime(Date.parse(allowance.resetAt))
                : undefined
            }
          >
            {allowance.resetAt
              ? t("Resets {{time}}", {
                  time: quotaTime(Date.parse(allowance.resetAt), true),
                })
              : t("Reset time unavailable")}
          </span>
        </div>
      </div>
      <div className={styles.balanceSummary}>
        {billing?.is_unified_billing_user === true && (
          <div className={styles.balanceCell}>
            <span>{t("Billing")}</span>
            <strong className={styles.balanceValue}>
              {" "}
              {t("Shared usage pool")}
            </strong>
          </div>
        )}
        {billing?.prepaid_balance && (
          <div className={styles.balanceCell}>
            <span className={styles.summaryItem}>
              <WalletIcon size={15} aria-hidden />
              {t("Purchased credits")}
            </span>
            <strong className={styles.balanceValue}>
              {billing.prepaid_balance.val == null
                ? t("Unavailable")
                : dollars(Math.abs(billing.prepaid_balance.val))}
            </strong>
          </div>
        )}
        {billing?.is_unified_billing_user === false &&
          billing.on_demand_cap && (
            <div className={styles.balanceCell}>
              <span>{t("Pay as you go")}</span>
              <strong className={styles.balanceValue}>
                {billing.on_demand_cap.val
                  ? t("{{used}} / {{cap}} cap", {
                      used: billing.on_demand_used
                        ? dollars(billing.on_demand_used.val ?? 0)
                        : t("Usage unknown"),
                      cap: dollars(billing.on_demand_cap.val),
                    })
                  : t("Disabled")}
              </strong>
            </div>
          )}
      </div>
    </>
  );
}

export function CopilotAllowance({
  identity,
  ready,
}: {
  identity: string;
  ready: boolean;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const quota = useQuery({
    queryKey: ["copilot-usage", identity],
    enabled: ready,
    queryFn: ({ signal }) =>
      result(client.GET("/api/subscriptions/copilot/usage", { signal })),
    staleTime: 60_000,
    refetchInterval: 600_000,
    retry: false,
  });
  const buckets = Object.entries(quota.data?.quota_snapshots ?? {});
  return (
    <div className={styles.usage}>
      <span className={styles.quotaLabel}>
        {t("Copilot allowance")}
        {quota.data?.copilot_plan ? ` - ${quota.data.copilot_plan}` : ""}
      </span>
      <ErrorNotice error={quota.error} />
      {!ready ? (
        <span>{t("Sign in to check allowance")}</span>
      ) : !buckets.length ? (
        <span>
          {quota.isPending
            ? t("Checking allowance…")
            : t("Allowance unavailable")}
        </span>
      ) : (
        <div className={styles.quotaWindows}>
          {buckets.map(([id, bucket]) => (
            <div key={id} className={styles.meter}>
              <span>
                {copilotBucketLabel(id, t)} -{" "}
                {bucket.unlimited
                  ? t("Unlimited")
                  : bucket.percent_remaining != null
                    ? t("{{percent}}% left", {
                        percent: Math.round(
                          Math.max(0, Math.min(100, bucket.percent_remaining)),
                        ),
                      })
                    : t("Remaining allowance unavailable")}
              </span>
              {!bucket.unlimited && bucket.percent_remaining != null && (
                <meter
                  min={0}
                  max={100}
                  value={Math.max(0, Math.min(100, bucket.percent_remaining))}
                  aria-label={t("{{label}} remaining", {
                    label: copilotBucketLabel(id, t),
                  })}
                />
              )}
              {!bucket.unlimited &&
                bucket.remaining != null &&
                bucket.entitlement != null && (
                  <span>
                    {t("{{remaining}} / {{entitlement}} remaining", {
                      remaining: bucket.remaining,
                      entitlement: bucket.entitlement,
                    })}
                  </span>
                )}
              {bucket.overage_permitted != null && (
                <span className={styles.quotaDetail}>
                  {bucket.overage_permitted
                    ? t("Additional usage enabled")
                    : t("Additional usage disabled")}
                  {bucket.overage_count != null
                    ? ` - ${t("{{count}} used", { count: bucket.overage_count })}`
                    : ""}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      {quota.data && (
        <span className={styles.quotaDetail}>
          {quota.data.quota_reset_date
            ? t("Resets {{time}}", {
                time: quotaTime(Date.parse(quota.data.quota_reset_date)),
              })
            : t("Reset time unavailable")}
        </span>
      )}
      {ready && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Refresh allowance")}
          loading={quota.isFetching}
          onClick={() => void quota.refetch()}
        >
          <ArrowsClockwiseIcon />
        </Button>
      )}
    </div>
  );
}

function copilotBucketLabel(
  id: string,
  t: ReturnType<typeof useTranslation>["t"],
) {
  return (
    (
      {
        chat: t("Chat"),
        completions: t("Code completions"),
        premium_interactions: t("Premium requests"),
        premium_models: t("Premium models"),
      } as Record<string, string>
    )[id] ?? id
  );
}

export function ChatGPTAllowance() {
  const { t } = useTranslation();
  return (
    <div className={styles.usage}>
      <span className={styles.quotaLabel}>{t("ChatGPT plan allowance")}</span>
      <span>{t("Numeric allowance is unavailable for this connection.")}</span>
      <a
        href="https://chatgpt.com/settings/usage"
        target="_blank"
        rel="noreferrer"
      >
        {t("View usage in ChatGPT ↗")}
      </a>
    </div>
  );
}

function allowanceLabel(
  type: string | null | undefined,
  t: ReturnType<typeof useTranslation>["t"],
) {
  return type === "USAGE_PERIOD_TYPE_WEEKLY"
    ? t("Weekly")
    : type === "USAGE_PERIOD_TYPE_MONTHLY"
      ? t("Monthly")
      : t("Current period");
}
