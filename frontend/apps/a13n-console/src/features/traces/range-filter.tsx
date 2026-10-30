import { Button, Input, Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import { SlidersHorizontalIcon, XIcon } from "@phosphor-icons/react";
import Decimal from "decimal.js-light";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import filterStyles from "../../shared/forms/filter-popover.module.css";
import styles from "./list.module.css";

export type NumericRange = { min: string; max: string };
type MetricRanges = { duration: NumericRange; cost: NumericRange };
const metrics = ["duration", "cost"] as const;
const empty = (): MetricRanges => ({
  duration: { min: "", max: "" },
  cost: { min: "", max: "" },
});
const active = (range: NumericRange) => range.min !== "" || range.max !== "";

/** Edit both ranges together; only Apply launches the whole-window query. */
export function MetricFilter({
  value,
  onChange,
}: {
  value: MetricRanges;
  onChange: (value: MetricRanges) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [error, setError] = useState<keyof MetricRanges | null>(null);
  const count = metrics.filter((metric) => active(value[metric])).length;
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) {
          setDraft(value);
          setError(null);
        }
      }}
    >
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            type="button"
            className={styles.filterTrigger}
            aria-label={t("Filter by duration or cost")}
          />
        }
      >
        <SlidersHorizontalIcon aria-hidden="true" />
        {t("Filters")}
        {count > 0 && <span className={styles.filterCount}>{count}</span>}
      </PopoverTrigger>
      <PopoverPopup
        align="start"
        className={`${styles.metricPopup} ${filterStyles.popup}`}
        aria-label={t("Duration and cost")}
      >
        <form
          className={filterStyles.body}
          onSubmit={(event) => {
            event.preventDefault();
            const invalid = metrics.find((metric) => {
              const range = draft[metric];
              return (
                range.min &&
                range.max &&
                new Decimal(range.min).greaterThan(new Decimal(range.max))
              );
            });
            if (invalid) {
              setError(invalid);
              return;
            }
            onChange(draft);
            setOpen(false);
          }}
        >
          {metrics.map((metric) => {
            const label = metric === "duration" ? "Duration" : "Cost";
            return (
              <fieldset key={metric} className={styles.metricSection}>
                <legend className={styles.metricLegend}>
                  {t(label)}{" "}
                  <span>{metric === "duration" ? t("seconds") : "USD"}</span>
                </legend>
                <div className={styles.metricBounds}>
                  {(["min", "max"] as const).map((bound) => (
                    <label key={bound} className={styles.metricBound}>
                      <span>{t(bound === "min" ? "Minimum" : "Maximum")}</span>
                      <Input
                        type="number"
                        min="0"
                        step="any"
                        value={draft[metric][bound]}
                        placeholder={t("No limit")}
                        aria-label={`${t(label)} ${t(bound === "min" ? "Minimum" : "Maximum")}`}
                        aria-invalid={error === metric || undefined}
                        onChange={(event) => {
                          setDraft({
                            ...draft,
                            [metric]: {
                              ...draft[metric],
                              [bound]: event.target.value,
                            },
                          });
                          setError(null);
                        }}
                      />
                    </label>
                  ))}
                </div>
                {error === metric && (
                  <p className={styles.rangeError} role="alert">
                    {t("Minimum must not exceed maximum.")}
                  </p>
                )}
              </fieldset>
            );
          })}
          <div className={filterStyles.footer}>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setDraft(empty());
                setError(null);
              }}
            >
              {t("Reset")}
            </Button>
            <div className={filterStyles.actions}>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setOpen(false)}
              >
                {t("Cancel")}
              </Button>
              <Button type="submit" size="sm" variant="outline">
                {t("Apply")}
              </Button>
            </div>
          </div>
        </form>
      </PopoverPopup>
    </Popover>
  );
}

export function MetricFilterSummary({
  value,
  onChange,
  onClear,
}: {
  value: MetricRanges;
  onChange: (value: MetricRanges) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  if (!metrics.some((metric) => active(value[metric]))) return null;
  return (
    <div
      className={styles.activeMetricFilters}
      aria-label={t("Applied filters")}
    >
      {metrics
        .filter((metric) => active(value[metric]))
        .map((metric) => {
          const label = t(metric === "duration" ? "Duration" : "Cost");
          const range = value[metric];
          const minutes =
            metric === "duration" &&
            [range.min, range.max].every(
              (value) => !value || Number(value) % 60 === 0,
            ) &&
            [range.min, range.max].some((value) => Number(value) > 0);
          const number = (value: string) =>
            metric === "cost"
              ? new Decimal(value).toString()
              : String(Number(value) / (minutes ? 60 : 1));
          const prefix = metric === "cost" ? "$" : "";
          const bounds =
            range.min && range.max
              ? `${prefix}${number(range.min)}–${number(range.max)}`
              : range.min
                ? `≥ ${prefix}${number(range.min)}`
                : `≤ ${prefix}${number(range.max)}`;
          const text =
            metric === "duration"
              ? `${bounds} ${t(minutes ? "minutes" : "seconds")}`
              : bounds;
          return (
            <span key={metric} className={styles.activeMetricFilter}>
              <span className={styles.chipLabel}>{label}</span>
              <span className={styles.metricValue} title={text}>
                {text}
              </span>
              <Button
                variant="ghost"
                size="icon-xs"
                type="button"
                aria-label={`${t("Remove filter")} ${label}: ${text}`}
                onClick={() =>
                  onChange({ ...value, [metric]: { min: "", max: "" } })
                }
              >
                <XIcon aria-hidden="true" />
              </Button>
            </span>
          );
        })}
      <Button
        variant="ghost"
        size="sm"
        type="button"
        className={styles.clearMetricFilters}
        onClick={onClear}
      >
        {t("Clear all")}
      </Button>
    </div>
  );
}
