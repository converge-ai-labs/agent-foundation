import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { exactCost, formatCost, sumCosts } from "../../shared/cost";
import { ModelKeyIcon } from "../models/model-icon";
import { percent } from "../usage/values";
import { formatDuration, formatSpeed, formatTokens } from "./format";
import styles from "./usage.module.css";

type ModelUsage = Schema["ModelUsage"];

export interface UsageTotals {
  requests: number;
  inputTokens: number;
  cachedTokens: number;
  outputTokens: number;
  /** Cached input is part of input, so the rate divides the two sums. */
  cacheRate: number | null;
  /** The known subtotal; null when nothing was priced. */
  cost: string | null;
  unpriced: boolean;
}

export function usageTotals(models: readonly ModelUsage[]): UsageTotals {
  const sum = (field: Counter) =>
    models.reduce((total, usage) => total + usage[field], 0);
  const inputTokens = sum("input_tokens");
  const cachedTokens = sum("cache_read_tokens");
  return {
    requests: sum("requests"),
    inputTokens,
    cachedTokens,
    outputTokens: sum("output_tokens"),
    cacheRate: inputTokens ? cachedTokens / inputTokens : null,
    cost: sumCosts(models.map((usage) => usage.cost)).total,
    unpriced: models.some((usage) => usage.cost === null),
  };
}

type Counter =
  "requests" | "input_tokens" | "output_tokens" | "cache_read_tokens";

/** The columns `Figures` renders after the labels. */
const FIGURES = 7;

/** Rows that share one label, such as the models one Run used. */
export interface UsageGroup {
  key: string;
  label?: ReactNode;
  models: readonly ModelUsage[];
}
/** Groups under one heading, such as the Runs of one conversation. */
export interface UsageSection {
  key: string;
  title?: ReactNode;
  groups: readonly UsageGroup[];
}

/**
 * Figures per model: input, the cached part of it and its rate, output, how
 * fast the model answered, and cost. A leading column labels each group; a
 * model column names each row.
 */
export function UsageTable({
  caption,
  sections,
  leading,
  model = true,
  requests = true,
  total = false,
}: {
  caption: string;
  sections: readonly UsageSection[];
  /** The leading column's heading; without it groups carry no label. */
  leading?: string;
  model?: boolean;
  /** Each model's request count beneath its name. */
  requests?: boolean;
  /** A final row summing every model. */
  total?: boolean;
}) {
  const { t } = useTranslation();
  const columns = FIGURES + (leading ? 1 : 0) + (model ? 1 : 0);
  const all = sections.flatMap((section) =>
    section.groups.flatMap((group) => group.models),
  );
  return (
    <div className={styles.scroll}>
      <table className={styles.table}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {leading && <th scope="col">{leading}</th>}
            {model && <th scope="col">{t("Model")}</th>}
            <th scope="col" data-number>
              {t("Input")}
            </th>
            <th scope="col" data-number>
              {t("Cached input")}
            </th>
            <th scope="col" data-number>
              {t("Cache hit rate")}
            </th>
            <th scope="col" data-number>
              {t("Output")}
            </th>
            <th
              scope="col"
              data-number
              title={t("Average time to first token")}
            >
              {t("TTFT")}
            </th>
            <th scope="col" data-number title={t("Output tokens per second")}>
              {t("Output speed")}
            </th>
            <th scope="col" data-number>
              {t("Spend")}
            </th>
          </tr>
        </thead>
        {sections.map((section) => (
          <tbody key={section.key}>
            {section.title && (
              <tr className={styles.sectionRow}>
                <th scope="rowgroup" colSpan={columns}>
                  {section.title}
                </th>
              </tr>
            )}
            {section.groups.flatMap((group) =>
              group.models.map((usage, index) => (
                <tr
                  key={`${group.key}:${usage.model}`}
                  data-first={!index || undefined}
                  data-continued={(leading && index > 0) || undefined}
                >
                  {leading && !index && (
                    <th
                      scope="rowgroup"
                      rowSpan={group.models.length}
                      className={styles.label}
                    >
                      {group.label}
                    </th>
                  )}
                  {model && (
                    <td className={styles.model}>
                      <span className={styles.modelName}>
                        <ModelKeyIcon modelKey={usage.model} />
                        <span title={usage.model ?? undefined}>
                          {usage.model ?? t("Unattributed model")}
                        </span>
                      </span>
                      {requests && (
                        <small>
                          {t("{{count}} requests", { count: usage.requests })}
                        </small>
                      )}
                    </td>
                  )}
                  <Figures totals={usageTotals([usage])} usage={usage} />
                </tr>
              )),
            )}
          </tbody>
        ))}
        {total && all.length > 1 && (
          <tfoot>
            <tr>
              <th scope="row" colSpan={columns - FIGURES}>
                {t("Total")}
              </th>
              <Figures totals={usageTotals(all)} />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

/** Speed is a model's own, so a total across models leaves it blank. */
function Figures({
  totals,
  usage,
}: {
  totals: UsageTotals;
  /** The one model these totals describe. */
  usage?: ModelUsage;
}) {
  const { t } = useTranslation();
  const waited = usage?.average_time_to_first_chunk_seconds;
  return (
    <>
      <td data-number>
        <Tokens value={totals.inputTokens} />
      </td>
      <td data-number>
        <Tokens value={totals.cachedTokens} />
      </td>
      <td data-number>
        <CacheRate rate={totals.cacheRate} />
      </td>
      <td data-number>
        <Tokens value={totals.outputTokens} />
      </td>
      <td data-number>
        {usage && formatDuration(waited == null ? null : waited * 1000)}
      </td>
      <td data-number>
        {usage && formatSpeed(t, usage.output_tokens_per_second)}
      </td>
      <td
        data-number
        title={
          totals.cost === null
            ? t("Not priced")
            : totals.unpriced
              ? t("Known subtotal; some requests were not priced.")
              : exactCost(totals.cost)
        }
      >
        {formatCost(totals.cost)}
        {totals.cost !== null && totals.unpriced && (
          <span className={styles.partial}>*</span>
        )}
      </td>
    </>
  );
}

/** Compact at a glance; the exact count is in the title. */
export function Tokens({ value }: { value: number }) {
  const { i18n } = useTranslation();
  return (
    <span title={value.toLocaleString(i18n.resolvedLanguage)}>
      {formatTokens(value)}
    </span>
  );
}

/** The rate with a small meter, so a column of Runs shows cache misses. */
function CacheRate({ rate }: { rate: number | null }) {
  return (
    <span className={styles.rate}>
      <Meter rate={rate} />
      {percent(rate)}
    </span>
  );
}

export function Meter({
  rate,
  wide = false,
}: {
  rate: number | null;
  wide?: boolean;
}) {
  if (rate === null) return null;
  return (
    <span
      className={styles.meter}
      data-wide={wide || undefined}
      aria-hidden="true"
      style={{ "--rate": `${rate * 100}%` } as CSSProperties}
    />
  );
}
