import { Button, DisclosureSection, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "./models.module.css";

type Pricing = Schema["TokenPricing-Input"];
type Rates = Schema["TokenRates-Input"];
const rateFields = [
  ["input", "Input"],
  ["output", "Output"],
  ["cache_read", "Cache read"],
  ["cache_write", "Cache write"],
] as const;

export function ModelPricing({
  value,
  onChange,
}: {
  value: Pricing | null;
  onChange: (value: Pricing | null) => void;
}) {
  const { t } = useTranslation();
  const tiers = value?.tiers ?? [{ above: null, rates: {} }];
  function rates(index: number, key: keyof Rates, price: string) {
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
