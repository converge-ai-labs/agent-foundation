import { HelpTip } from "../shell/help-tip";
import { ModelIdentity } from "../shell/model-identity";
import { Fragment, useState } from "react";
import { Button } from "a13n-ui";
import { ArrowRight } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { modelCost, tokenCounts, type ThreadUsage } from "./usage";
import styles from "./composer-status.module.css";
import { DebugOnly } from "../shell/debug";

/** Format observed counts for the resolved display language. */
function useCount() {
  const { i18n } = useTranslation();
  return (value: number) => value.toLocaleString(i18n.resolvedLanguage);
}

export function ContextDetails({
  used,
  capacity,
  compact = false,
  cacheRate,
}: {
  used?: number | null;
  capacity?: number | null;
  compact?: boolean;
  cacheRate?: string;
}) {
  const { t } = useTranslation();
  const count = useCount();
  const limit = capacity != null && capacity > 0 ? capacity : undefined;
  const remaining =
    used != null && limit != null ? Math.max(0, limit - used) : undefined;
  const percent =
    used != null && limit != null ? (used / limit) * 100 : undefined;
  return (
    <>
      {compact && (
        <div className={styles.contextHero}>
          <div>
            <strong>{used != null ? count(used) : "—"}</strong>
            <span>{t("tokens used")}</span>
          </div>
          {percent != null && (
            <span className={styles.contextPercent}>
              {Math.round(percent)}%
            </span>
          )}
        </div>
      )}
      {percent != null && (
        <div className={styles.contextCapacity}>
          {!compact && (
            <span>
              {t("{{count, number}}% used", { count: Math.round(percent) })}
            </span>
          )}
          <div
            className={styles.contextMeter}
            role="meter"
            aria-label={t("Context used")}
            aria-valuemin={0}
            aria-valuemax={limit}
            aria-valuenow={Math.min(used!, limit!)}
            aria-valuetext={t("{{used, number}} of {{limit, number}} tokens", {
              used: used!,
              limit: limit!,
            })}
            data-full={percent >= 90 || undefined}
          >
            <span style={{ width: `${Math.min(100, percent)}%` }} />
          </div>
        </div>
      )}
      <dl
        className={`${styles.numbers} ${styles.contextNumbers}`}
        data-unknown-limit={limit == null || undefined}
      >
        {!compact && (
          <>
            <dt>{t("Used tokens")}</dt>
            <dd>{used != null ? count(used) : t("Unknown")}</dd>
          </>
        )}
        {limit != null && (
          <>
            <dt>{t("Context window")}</dt>
            <dd>{count(limit)}</dd>
            <dt>{t("Remaining tokens")}</dt>
            <dd>{remaining != null ? count(remaining) : t("Unknown")}</dd>
          </>
        )}
        {used != null && limit != null && used > limit && (
          <>
            <dt>{t("Over capacity")}</dt>
            <dd>{count(used - limit)}</dd>
          </>
        )}
        {cacheRate != null && !compact && (
          <>
            <dt>{t("Cache rate")}</dt>
            <dd>{cacheRate}</dd>
          </>
        )}
      </dl>
      {compact && cacheRate != null && (
        <dl className={styles.contextCache}>
          <dt>{t("Cache rate")}</dt>
          <dd>{cacheRate}</dd>
        </dl>
      )}
      {limit == null && !compact && (
        <p className={styles.scope}>{t("Context limit unavailable.")}</p>
      )}
    </>
  );
}

type Totals = ThreadUsage["root"];
type Scope = "combined" | "root" | "descendants";

function CostValue({ totals }: { totals: Totals }) {
  const { t } = useTranslation();
  return (
    <>
      <strong>{modelCost(totals)}</strong>
      {totals.unknown_model_costs > 0 && (
        <HelpTip label={t("About estimated cost")}>
          {t(
            "{{count, number}} responses have no recorded price and are excluded from this estimate.",
            { count: totals.unknown_model_costs },
          )}
        </HelpTip>
      )}
    </>
  );
}

function TokenRows({ totals }: { totals: Totals }) {
  const { t } = useTranslation();
  const count = useCount();
  const tokens = tokenCounts(totals);
  const rows = [
    [t("Input tokens"), tokens.get("input_tokens") ?? 0],
    [t("Output tokens"), tokens.get("output_tokens") ?? 0],
    [t("Cache read"), tokens.get("cache_read_tokens") ?? 0],
    [t("Cache write"), tokens.get("cache_write_tokens") ?? 0],
    [t("Model requests"), totals.model_requests],
    ...(
      [
        [t("Input audio"), "input_audio_tokens"],
        [t("Output audio"), "output_audio_tokens"],
        [t("Cached audio"), "cache_audio_read_tokens"],
      ] as const
    ).flatMap(([label, key]) =>
      tokens.get(key) ? [[label, tokens.get(key)!] as const] : [],
    ),
  ] as const;
  return (
    <dl className={styles.numbers}>
      {rows.map(([label, value]) => (
        <Fragment key={label}>
          <dt>{label}</dt>
          <dd>{count(value)}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

export function UsageDetails({
  usage,
  compact = false,
}: {
  usage?: ThreadUsage;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const count = useCount();
  const [scope, setScope] = useState<Scope>("combined");
  if (!usage?.first_observed_at)
    return (
      <p className={styles.scope}>
        {t("No usage recorded yet. Earlier usage may be missing.")}
      </p>
    );
  const totals = usage[scope];
  const tokens = tokenCounts(totals);
  const input = tokens.get("input_tokens") ?? 0;
  const output = tokens.get("output_tokens") ?? 0;
  const total = input + output;
  const scopes = usage.model_scopes ?? [];
  const models: (readonly [string, Totals])[] = scopes.length
    ? scopes
        .filter((model) => model[scope].model_requests > 0)
        .map((model) => [model.name, model[scope]] as const)
    : scope === "combined"
      ? [
          ...usage.models.flatMap(([name, value]) =>
            typeof name === "string" &&
            typeof value === "object" &&
            value !== null
              ? [[name, value] as const]
              : [],
          ),
          ...(usage.other_models.model_requests
            ? [["Other models", usage.other_models] as const]
            : []),
        ]
      : [];
  return (
    <div
      className={`${styles.usageDetails} ${compact ? styles.compactUsage : ""}`}
    >
      {(!compact || usage.descendants.model_requests > 0) && (
        <div
          className={styles.scopeSelector}
          role="group"
          aria-label={t("Usage scope")}
        >
          {(
            [
              ["combined", t("All")],
              ["root", t("This chat")],
              ["descendants", t("Subagents")],
            ] as const
          ).map(([value, label]) => (
            <Button
              key={value}
              variant="ghost"
              size="sm"
              aria-pressed={scope === value}
              onClick={() => setScope(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      )}
      {totals.model_requests > 0 && (
        <dl className={styles.usageOverview}>
          <dt>{t("Total tokens")}</dt>
          <dd>{totals.model_requests ? count(total) : "—"}</dd>
          <dt>{t("Model cost · USD")}</dt>
          <dd>
            <CostValue totals={totals} />
          </dd>
        </dl>
      )}
      {!compact && total > 0 && (
        <div className={styles.tokenDistribution}>
          <div className={styles.tokenBar} aria-hidden="true">
            <span style={{ width: `${(input / total) * 100}%` }} />
          </div>
          <div className={styles.tokenLegend}>
            <span>
              <i />
              {t("Input")} <strong>{count(input)}</strong>
            </span>
            <span>
              <i />
              {t("Output")} <strong>{count(output)}</strong>
            </span>
          </div>
        </div>
      )}
      {totals.model_requests > 0 ? (
        <>
          <details className={styles.breakdown}>
            <summary>
              {t("Token breakdown")}{" "}
              <span>
                {t("{{count, number}} requests", {
                  count: totals.model_requests,
                })}
              </span>
            </summary>
            <TokenRows totals={totals} />
          </details>
          <details className={styles.breakdown}>
            <summary>
              {t("By model")} <span>{count(models.length)}</span>
            </summary>
            {!models.length && (
              <p className={styles.scope}>
                {t("Older records don't show which models were used here.")}
              </p>
            )}
            <ul className={styles.models}>
              {models.map(([name, value]) => {
                const groups = (usage.groups ?? []).filter(
                  (group) =>
                    group.model === name &&
                    (scope === "combined" ||
                      group.descendant === (scope === "descendants")),
                );
                return (
                  <li key={name}>
                    <details className={styles.modelDetails}>
                      <summary className={styles.modelHeader}>
                        <span
                          className={styles.modelName}
                          title={
                            name === "Other models" ? t("Other models") : name
                          }
                        >
                          {name === "Other models" ? (
                            t("Other models")
                          ) : (
                            <ModelIdentity name={name} />
                          )}
                        </span>
                        <span className={styles.modelCost}>
                          {modelCost(value)}
                        </span>
                      </summary>
                      <TokenRows totals={value} />
                      {value.unknown_model_costs > 0 && (
                        <p className={styles.scope}>
                          {t(
                            "{{count, number}} responses have no recorded price.",
                            { count: value.unknown_model_costs },
                          )}
                        </p>
                      )}
                      {groups.length > 0 && (
                        <details className={styles.attribution}>
                          <summary>{t("Agents and sources")}</summary>
                          {groups.map((group) => (
                            <div
                              className={styles.sourceGroup}
                              key={`${group.agent_instance_id}:${group.descendant}:${group.source}`}
                            >
                              <div>
                                {t("{{scope}} · {{source}}", {
                                  scope: group.descendant
                                    ? t("Subagent")
                                    : t("Root"),
                                  source:
                                    group.source === "agent"
                                      ? t("Agent requests")
                                      : group.source ===
                                          "files.media_understanding"
                                        ? t("Media understanding · view")
                                        : group.source || t("Unattributed"),
                                })}
                              </div>
                              <DebugOnly>
                                <small className={styles.scope}>
                                  {group.agent_instance_id ||
                                    t("Unknown agent")}
                                </small>
                              </DebugOnly>
                              <div>
                                {t("{{count, number}} requests", {
                                  count: group.totals.model_requests,
                                })}{" "}
                                · <CostValue totals={group.totals} />
                              </div>
                              <TokenRows totals={group.totals} />
                            </div>
                          ))}
                        </details>
                      )}
                    </details>
                  </li>
                );
              })}
            </ul>
            {!!usage.other_groups?.model_requests && (
              <p className={styles.scope}>
                {t(
                  "Some breakdown details are hidden. Their usage is still included in the totals.",
                )}
              </p>
            )}
          </details>
        </>
      ) : (
        <p className={styles.emptyScope}>{t("No usage in this scope yet.")}</p>
      )}
      {totals.provider_receipts > 0 && (
        <details className={styles.attribution}>
          <summary>
            {t("Non-model providers · {{count, number}} receipts", {
              count: totals.provider_receipts,
            })}
          </summary>
          <p className={styles.scope}>
            {t("Separate from model costs; currencies are not converted.")}
          </p>
          <dl className={styles.numbers}>
            {totals.provider_costs.map(([currency, cost]) => (
              <Fragment key={String(currency)}>
                <dt>{String(currency)}</dt>
                <dd>{String(cost)}</dd>
              </Fragment>
            ))}
          </dl>
          {!!totals.unknown_provider_costs && (
            <p className={styles.scope}>
              {t("{{count, number}} unknown-cost receipts", {
                count: totals.unknown_provider_costs,
              })}
            </p>
          )}
          {!!totals.omitted_currency_receipts && (
            <p className={styles.scope}>
              {t("{{count, number}} currency entries omitted", {
                count: totals.omitted_currency_receipts,
              })}
            </p>
          )}
        </details>
      )}
      <details className={styles.breakdown}>
        <summary>{t("About these numbers")}</summary>
        <p className={styles.scope}>
          {t("Includes auxiliary models and recorded usage in this chat.")}
        </p>
        <p className={styles.scope}>
          {t(
            "Cache and audio tokens are already included in input and output. Agent and source totals are breakdowns of the same usage.",
          )}
        </p>
        <p className={styles.scope}>
          {t(
            "Estimated cost, not a bill. + means some prices are missing. Older missing prices stay unknown.",
          )}
        </p>
      </details>
      <a
        className={styles.analyticsLink}
        href={`/usage?thread_id=${encodeURIComponent(usage.thread_id)}`}
      >
        {t("Open analytics")} <ArrowRight aria-hidden size={14} />
      </a>
    </div>
  );
}

export const TokenDetails = UsageDetails;
export const CostDetails = UsageDetails;
