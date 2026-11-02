import { useState } from "react";
import {
  Popover,
  PopoverPopup,
  PopoverTrigger,
  SegmentedControl,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { formatCost, formatCostTicks } from "../../shared/cost";
import styles from "./usage.module.css";
import { compactNumber, niceScale } from "./values";

export function UsageChart({
  pending,
  days,
  series,
  metric,
  onMetricChange,
  group,
  onGroupChange,
}: {
  pending?: boolean;
  days: Schema["DailyUsage"][];
  series: Schema["UsageSeries"][];
  metric: "cost" | "tokens";
  onMetricChange: (metric: "cost" | "tokens") => void;
  group: "agents" | "models";
  onGroupChange: (group: "agents" | "models") => void;
}) {
  const { t, i18n } = useTranslation();
  const [active, setActive] = useState<string | null>(null);
  const amount = (usage: Schema["ModelMetrics"]) =>
    metric === "cost"
      ? usage.cost === null
        ? null
        : Number(usage.cost)
      : usage.input_tokens + usage.output_tokens;
  const values = days.map(({ usage }) => amount(usage));
  const seriesName = (item: Schema["UsageSeries"]) =>
    item.key === null ? t("Others") : (item.name ?? t("Unattributed model"));
  const color = (index: number) =>
    series[index].key === null
      ? "var(--a13n-tertiary)"
      : `var(--chart-${index + 1})`;
  const points = series.map(
    (item) => new Map(item.daily.map((day) => [day.date, day.usage])),
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
  const ticks =
    metric === "cost"
      ? formatCostTicks(scale.ticks)
      : scale.ticks.map((value) => compactNumber(value, i18n.resolvedLanguage));
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
        <div className={styles.chartControls}>
          <SegmentedControl
            label={t("Chart grouping")}
            value={group}
            onValueChange={(value) =>
              onGroupChange(value === "models" ? "models" : "agents")
            }
            options={[
              { value: "agents", label: t("By agent") },
              { value: "models", label: t("By model") },
            ]}
          />
          <SegmentedControl
            label={t("Trend metric")}
            value={metric}
            onValueChange={(value) =>
              onMetricChange(value === "tokens" ? "tokens" : "cost")
            }
            options={[
              { value: "cost", label: t("Spend") },
              { value: "tokens", label: t("Tokens") },
            ]}
          />
        </div>
      </div>
      {pending ? (
        <div className={styles.chartLoading} role="status">
          {t("Loading…")}
        </div>
      ) : (
        <>
          <div className={styles.legend} aria-label={t("Chart legend")}>
            {series.map((item, index) => (
              <span key={item.key ?? "others"}>
                <i style={{ background: color(index) }} />
                {seriesName(item)}
              </span>
            ))}
          </div>
          <div className={styles.chartReadout} aria-live="polite">
            {selected ? (
              <>
                <strong>{date(selected.date)}</strong>
                <span>
                  {metric === "cost"
                    ? formatCost(selected.usage.cost)
                    : number(
                        selected.usage.input_tokens +
                          selected.usage.output_tokens,
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
              {ticks.map((value) => (
                <span key={value}>{value}</span>
              ))}
            </div>
            <div className={styles.plot}>
              <div className={styles.grid} aria-hidden="true">
                {scale.ticks.map((value) => (
                  <i key={value} />
                ))}
              </div>
              <div
                className={styles.bars}
                data-focused={active ? "" : undefined}
              >
                {days.map((day, index) => (
                  <Popover
                    key={day.date}
                    open={active === day.date}
                    onOpenChange={(open) =>
                      setActive((current) =>
                        open ? day.date : current === day.date ? null : current,
                      )
                    }
                  >
                    <PopoverTrigger
                      openOnHover
                      delay={150}
                      closeDelay={150}
                      render={<button type="button" />}
                      className={styles.barSlot}
                      aria-label={`${date(day.date)}: ${label(values[index])}${day.usage.unpriced_requests > 0 && metric === "cost" ? ` · ${t("Some requests are unpriced")}` : ""}`}
                      aria-pressed={active === day.date}
                    >
                      {values[index] !== 0 && (
                        <span
                          className={styles.bar}
                          data-unknown={values[index] === null || undefined}
                          style={{
                            height:
                              values[index] === null
                                ? "8px"
                                : `${(values[index]! / scale.top) * 100}%`,
                          }}
                        >
                          {values[index] !== null &&
                            series.map((item, seriesIndex) => {
                              const usage = points[seriesIndex].get(day.date);
                              const value = usage ? (amount(usage) ?? 0) : 0;
                              return (
                                value > 0 && (
                                  <span
                                    key={item.key ?? "others"}
                                    className={styles.segment}
                                    style={{
                                      height: `${(value / values[index]!) * 100}%`,
                                      background: color(seriesIndex),
                                    }}
                                  />
                                )
                              );
                            })}
                        </span>
                      )}
                    </PopoverTrigger>
                    <PopoverPopup
                      side="top"
                      aria-label={date(day.date)}
                      className={styles.chartPopup}
                    >
                      <div className={styles.tooltipHeading}>
                        <strong>{date(day.date)}</strong>
                        <strong>{label(values[index])}</strong>
                      </div>
                      <ul className={styles.tooltipRows}>
                        {series.map((item, seriesIndex) => {
                          const usage = points[seriesIndex].get(day.date);
                          if (!usage || !usage.requests) return null;
                          const value = amount(usage);
                          return (
                            <li key={item.key ?? "others"}>
                              <i style={{ background: color(seriesIndex) }} />
                              <span className={styles.seriesName}>
                                {seriesName(item)}
                              </span>
                              <span>{label(value)}</span>
                              <span className={styles.share}>
                                {value === null || !values[index]
                                  ? "—"
                                  : new Intl.NumberFormat(
                                      i18n.resolvedLanguage,
                                      {
                                        style: "percent",
                                        maximumFractionDigits: 1,
                                      },
                                    ).format(value / values[index]!)}
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                      {day.usage.unpriced_requests > 0 && metric === "cost" && (
                        <p className={styles.tooltipNote}>
                          {t("Some requests are unpriced")}
                        </p>
                      )}
                    </PopoverPopup>
                  </Popover>
                ))}
              </div>
            </div>
            {/* Date labels share the bars' slots, so each sits under its bar. */}
            <div className={styles.dates} aria-hidden="true">
              {days.map((day, index) => (
                <span key={day.date}>
                  {(index === 0 ||
                    index === days.length - 1 ||
                    (days.length > 4 &&
                      index === Math.floor(days.length / 2))) &&
                    date(day.date)}
                </span>
              ))}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
