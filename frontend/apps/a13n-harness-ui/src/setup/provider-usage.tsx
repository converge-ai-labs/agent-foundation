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
  const billing = usage?.config;
  const allowance = grokAllowance(billing);
  return (
    <>
      <div className={styles.quotaWindows}>
        <div className={styles.meter}>
          {allowance.usedPercent === null ? (
            <span>Allowance unavailable</span>
          ) : (
            <>
              <div className={styles.meterHeading}>
                <span>{allowance.label}</span>
                <span>
                  <strong>{Math.floor(allowance.usedPercent)}%</strong> used
                </span>
              </div>
              <meter
                min={0}
                max={100}
                value={allowance.usedPercent}
                aria-label={`${allowance.label} used`}
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
              ? `Resets ${quotaTime(Date.parse(allowance.resetAt), true)}`
              : "Reset time unavailable"}
          </span>
        </div>
      </div>
      <div className={styles.balanceSummary}>
        {billing?.is_unified_billing_user === true && (
          <div className={styles.balanceCell}>
            <span>Billing</span>
            <strong className={styles.balanceValue}>Shared usage pool</strong>
          </div>
        )}
        {billing?.prepaid_balance && (
          <div className={styles.balanceCell}>
            <span className={styles.summaryItem}>
              <WalletIcon size={15} aria-hidden />
              Purchased credits
            </span>
            <strong className={styles.balanceValue}>
              {billing.prepaid_balance.val == null
                ? "Unavailable"
                : dollars(Math.abs(billing.prepaid_balance.val))}
            </strong>
          </div>
        )}
        {billing?.is_unified_billing_user === false &&
          billing.on_demand_cap && (
            <div className={styles.balanceCell}>
              <span>Pay as you go</span>
              <strong className={styles.balanceValue}>
                {billing.on_demand_cap.val
                  ? `${billing.on_demand_used ? dollars(billing.on_demand_used.val ?? 0) : "Usage unknown"} / ${dollars(billing.on_demand_cap.val)} cap`
                  : "Disabled"}
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
        Copilot allowance
        {quota.data?.copilot_plan ? ` · ${quota.data.copilot_plan}` : ""}
      </span>
      <ErrorNotice error={quota.error} />
      {!ready ? (
        <span>Sign in to check allowance</span>
      ) : !buckets.length ? (
        <span>
          {quota.isPending ? "Checking allowance…" : "Allowance unavailable"}
        </span>
      ) : (
        <div className={styles.quotaWindows}>
          {buckets.map(([id, bucket]) => (
            <div key={id} className={styles.meter}>
              <span>
                {copilotBucketLabel(id)} ·{" "}
                {bucket.unlimited
                  ? "Unlimited"
                  : bucket.percent_remaining != null
                    ? `${Math.round(Math.max(0, Math.min(100, bucket.percent_remaining)))}% left`
                    : "Remaining allowance unavailable"}
              </span>
              {!bucket.unlimited && bucket.percent_remaining != null && (
                <meter
                  min={0}
                  max={100}
                  value={Math.max(0, Math.min(100, bucket.percent_remaining))}
                  aria-label={`${copilotBucketLabel(id)} remaining`}
                />
              )}
              {!bucket.unlimited &&
                bucket.remaining != null &&
                bucket.entitlement != null && (
                  <span>
                    {bucket.remaining} / {bucket.entitlement} remaining
                  </span>
                )}
              {bucket.overage_permitted != null && (
                <span className={styles.quotaDetail}>
                  Additional usage{" "}
                  {bucket.overage_permitted ? "enabled" : "disabled"}
                  {bucket.overage_count != null
                    ? ` · ${bucket.overage_count} used`
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
            ? `Resets ${quotaTime(Date.parse(quota.data.quota_reset_date))}`
            : "Reset time unavailable"}
        </span>
      )}
      {ready && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Refresh allowance"
          loading={quota.isFetching}
          onClick={() => void quota.refetch()}
        >
          <ArrowsClockwiseIcon />
        </Button>
      )}
    </div>
  );
}

function copilotBucketLabel(id: string) {
  return (
    (
      {
        chat: "Chat",
        completions: "Code completions",
        premium_interactions: "Premium requests",
        premium_models: "Premium models",
      } as Record<string, string>
    )[id] ?? id
  );
}

export function ChatGPTAllowance() {
  return (
    <div className={styles.usage}>
      <span className={styles.quotaLabel}>ChatGPT plan allowance</span>
      <span>Numeric allowance is unavailable for this connection.</span>
      <a
        href="https://chatgpt.com/settings/usage"
        target="_blank"
        rel="noreferrer"
      >
        View usage in ChatGPT ↗
      </a>
    </div>
  );
}
