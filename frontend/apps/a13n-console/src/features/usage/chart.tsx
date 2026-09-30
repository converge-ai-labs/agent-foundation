import { useState } from "react";
import { SegmentedControl } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { formatCost } from "../../shared/cost";
import styles from "./usage.module.css";
import { compactNumber, niceScale } from "./values";

export function UsageChart({ days }: { days: Schema["DailyUsage"][] }) {
  const { t, i18n } = useTranslation();
  const [metric, setMetric] = useState("cost");
  const [active, setActive] = useState<string | null>(null);
  const values = days.map(({ usage }) =>
    metric === "cost"
      ? usage.cost === null
        ? null
        : Number(usage.cost)
      : usage.input_tokens + usage.output_tokens,
  );
  const maximum = Math.max(
    0,
    ...values.filter((value): value is number => value !== null),
  );
  const scale = niceScale(maximum);
  const number = (value: number) => value.toLocaleString(i18n.resolvedLanguage);
  const label = (value: number | null) =>
    metric === "cost"
      ? formatCost(value === null ? null : String(value))
      : number(value ?? 0);
  const tick = (value: number) =>
    metric === "cost"
      ? `$${value.toLocaleString("en-US", { maximumFractionDigits: 3 })}`
      : compactNumber(value, i18n.resolvedLanguage);
  const selected = days.find((day) => day.date === active);
  const date = (value: string) =>
    new Date(`${value}T12:00:00Z`).toLocaleDateString(i18n.resolvedLanguage, {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  return (
    <section className={styles.trend} aria-labelledby="usage-trend-title">
      <div className={styles.sectionHeading}>
        <h2 id="usage-trend-title">{t("Daily usage")}</h2>
        <SegmentedControl
          label={t("Trend metric")}
          value={metric}
          onValueChange={setMetric}
          options={[
            { value: "cost", label: t("Spend") },
            { value: "tokens", label: t("Tokens") },
          ]}
        />
      </div>
      <div className={styles.chartReadout} aria-live="polite">
        {selected ? (
          <>
            <strong>{date(selected.date)}</strong>
            <span>
              {metric === "cost"
                ? formatCost(selected.usage.cost)
                : number(
                    selected.usage.input_tokens + selected.usage.output_tokens,
                  )}
            </span>
            {selected.usage.unpriced_requests > 0 && metric === "cost" && (
              <span>{t("Some requests are unpriced")}</span>
            )}
          </>
        ) : (
          <span>{t("Select a day to inspect its usage.")}</span>
        )}
      </div>
      <div className={styles.chart}>
        <div className={styles.axis} aria-hidden="true">
          {scale.ticks.map((value) => (
            <span key={value}>{tick(value)}</span>
          ))}
        </div>
        <div className={styles.plot}>
          <div className={styles.grid} aria-hidden="true">
            {scale.ticks.map((value) => (
              <i key={value} />
            ))}
          </div>
          <div className={styles.bars} data-focused={active ? "" : undefined}>
            {days.map((day, index) => (
              <button
                key={day.date}
                type="button"
                className={styles.barSlot}
                aria-label={`${date(day.date)}: ${label(values[index])}${day.usage.unpriced_requests > 0 && metric === "cost" ? ` · ${t("Some requests are unpriced")}` : ""}`}
                aria-pressed={active === day.date}
                onFocus={() => setActive(day.date)}
                onMouseEnter={() => setActive(day.date)}
                onClick={() => setActive(day.date)}
              >
                <span
                  className={styles.bar}
                  data-unknown={values[index] === null || undefined}
                  style={{
                    height:
                      values[index] === null
                        ? "8px"
                        : `${(values[index]! / scale.top) * 100}%`,
                  }}
                />
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className={styles.dates} aria-hidden="true">
        {days
          .filter(
            (_, index) =>
              index === 0 ||
              index === days.length - 1 ||
              (days.length > 4 && index === Math.floor(days.length / 2)),
          )
          .map((day) => (
            <span key={day.date}>{date(day.date)}</span>
          ))}
      </div>
    </section>
  );
}
