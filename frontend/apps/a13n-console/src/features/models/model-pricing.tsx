import { Button, DisclosureSection, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "./models.module.css";

type Entry = Schema["ModelPricingEntry-Output"];
const rateFields = [
  ["input_mtok", "Input"],
  ["output_mtok", "Output"],
  ["cache_read_mtok", "Cache read"],
  ["cache_write_mtok", "Cache write"],
] as const;
type RateKey = (typeof rateFields)[number][0];
const isRate = (key: string) => rateFields.some(([rate]) => rate === key);
/** The editor's view of a price entry: a base row, then one row per input-length threshold. */
export type PriceTable = {
  tiers: {
    above: number | null;
    rates: Partial<Record<RateKey, string | null>>;
  }[];
};

/** What an ordinary request pays: the last rule without a date or time condition. */
function standardRule(entry: Entry) {
  return entry.rules.findLast(
    (rule) =>
      !rule.service_tier && (rule.constraint?.kind ?? "always") === "always",
  );
}

/** The token prices an entry declares, as the editor shows them. */
export function priceTable(entry: Entry | null): PriceTable | null {
  const rule = entry && standardRule(entry);
  if (!rule) return null;
  const prices = rule.prices.filter((price) => isRate(price.price_key));
  const starts = [
    ...new Set(
      prices.flatMap((price) => price.tiers?.map((tier) => tier.start) ?? []),
    ),
  ].sort((a, b) => a - b);
  const rates = (
    read: (price: (typeof prices)[number]) => string | undefined,
  ) =>
    Object.fromEntries(
      prices.flatMap((price) => {
        const value = read(price);
        return value === undefined ? [] : [[price.price_key, value]];
      }),
    );
  return {
    tiers: [
      { above: null, rates: rates((price) => price.price) },
      ...starts.map((start) => ({
        above: start,
        rates: rates(
          (price) => price.tiers?.find((tier) => tier.start === start)?.price,
        ),
      })),
    ],
  };
}

/**
 * The entry an edited table saves: the standard rule's token prices replaced,
 * every other price and rule the entry declares kept, and the Console named as
 * the source of the prices. The entry prices its own model's calls; the
 * provider and model it names only record where the prices came from.
 */
export function priceEntry(
  table: PriceTable | null,
  base: Entry | null,
  identity: { provider: string; model: string },
): Schema["ModelPricingEntry-Input"] | null {
  if (!table) return null;
  const [first, ...rest] = table.tiers;
  const edited = rateFields.flatMap(([key]) => {
    const price = first?.rates[key];
    if (!price) return [];
    const tiers = rest.flatMap(({ above, rates }) => {
      const tier = rates[key];
      return above === null || !tier ? [] : [{ start: above, price: tier }];
    });
    return [{ price_key: key, price, tiers }];
  });
  const rule = base && standardRule(base);
  const prices = [
    ...(rule?.prices.filter((price) => !isRate(price.price_key)) ?? []),
    ...edited,
  ];
  if (!prices.length) return null;
  const standard = {
    ...rule,
    rule_id: rule?.rule_id ?? "standard",
    constraint: rule?.constraint,
    prices,
  };
  return {
    provider: base?.provider ?? identity.provider,
    model: base?.model ?? identity.model,
    context_window: base?.context_window,
    source: "console",
    source_revision: "manual",
    rules: base
      ? base.rules.map((item) => (item === rule ? standard : item))
      : [standard],
  };
}

export function ModelPricing({
  value,
  onChange,
}: {
  value: PriceTable | null;
  onChange: (value: PriceTable | null) => void;
}) {
  const { t } = useTranslation();
  const tiers = value?.tiers ?? [{ above: null, rates: {} }];
  function rates(index: number, key: RateKey, price: string) {
    onChange({
      ...value,
      tiers: tiers.map((tier, i) =>
        i === index
          ? {
              ...tier,
              rates: { ...tier.rates, [key]: price === "" ? null : price },
            }
          : tier,
      ),
    });
  }
  return (
    <DisclosureSection
      title={t("Pricing")}
      summary={
        value
          ? t("{{count}} price tiers", { count: tiers.length })
          : t("Unknown")
      }
    >
      <div className={styles.pricingEditor}>
        <p>
          {t(
            "USD per million tokens. Input length selects one price tier for the entire request. Blank prices are unknown.",
          )}
        </p>
        <div className={styles.pricingScroll}>
          <table className={styles.pricingTable}>
            <thead>
              <tr>
                <th>{t("Input length")}</th>
                {rateFields.map(([key, label]) => (
                  <th key={key}>{t(label)}</th>
                ))}
                <th>
                  <span className="sr-only">{t("Actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {tiers.map((tier, index) => (
                <tr key={index}>
                  <td>
                    {index === 0 ? (
                      <span>{t("Base price")}</span>
                    ) : (
                      <Input
                        type="number"
                        min={0}
                        step={1}
                        required
                        aria-label={t("Tier {{number}}: above input tokens", {
                          number: index + 1,
                        })}
                        value={tier.above ?? ""}
                        onChange={(event) =>
                          onChange({
                            ...value,
                            tiers: tiers.map((item, i) =>
                              i === index
                                ? {
                                    ...item,
                                    above:
                                      event.target.value === ""
                                        ? null
                                        : Number(event.target.value),
                                  }
                                : item,
                            ),
                          })
                        }
                      />
                    )}
                  </td>
                  {rateFields.map(([key, label]) => (
                    <td key={key}>
                      <Input
                        type="number"
                        min={0}
                        step="any"
                        aria-label={t("Tier {{number}}: {{label}}", {
                          number: index + 1,
                          label: t(label),
                        })}
                        placeholder="—"
                        value={tier.rates[key] ?? ""}
                        onChange={(event) =>
                          rates(index, key, event.target.value)
                        }
                      />
                    </td>
                  ))}
                  <td>
                    {index > 0 && (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t("Remove tier {{number}}", {
                          number: index + 1,
                        })}
                        onClick={() =>
                          onChange({
                            ...value,
                            tiers: tiers.filter((_, i) => i !== index),
                          })
                        }
                      >
                        ×
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className={styles.pricingActions}>
          <Button
            size="sm"
            variant="ghost"
            disabled={tiers.length >= 32}
            onClick={() =>
              onChange({
                ...value,
                tiers: [
                  ...tiers,
                  { above: (tiers.at(-1)?.above ?? 0) + 100000, rates: {} },
                ],
              })
            }
          >
            {t("Add price tier")}
          </Button>
          {value && (
            <Button size="sm" variant="ghost" onClick={() => onChange(null)}>
              {t("Clear prices")}
            </Button>
          )}
        </div>
      </div>
    </DisclosureSection>
  );
}
