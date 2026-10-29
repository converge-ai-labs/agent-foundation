import { Button, Popover, PopoverPopup, PopoverTrigger } from "a13n-ui";
import { CaretDownIcon } from "@phosphor-icons/react";
import type { TFunction } from "i18next";
import { useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { DateTimeField } from "../forms";
import { formatLocalDateTime, parseLocalDateTime } from "../local-date-time";
import styles from "./time-range.module.css";
import filterStyles from "../forms/filter-popover.module.css";

/** A trailing window that ends now, in seconds, or a fixed range. */
export type RangeSelection = { preset: number } | { start: Date; end: Date };

/** Short button text and the full name of a trailing window. */
export function presetLabels(t: TFunction, seconds: number, language?: string) {
  switch (seconds) {
    case 900:
      return { short: t("15 minutes"), long: t("Last 15 minutes") };
    case 3600:
      return { short: t("1 hour"), long: t("Last hour") };
    case 21600:
      return { short: t("6 hours"), long: t("Last 6 hours") };
    case 86400:
      return { short: t("24 hours"), long: t("Last 24 hours") };
    case 604800:
      return { short: t("7 days"), long: t("Last 7 days") };
    case 2592000:
      return { short: t("30 days"), long: t("Last 30 days") };
  }
  // A saved window outside the presets reads in its largest whole unit.
  const [value, unit] =
    seconds % 86400 === 0
      ? [seconds / 86400, "day"]
      : seconds % 3600 === 0
        ? [seconds / 3600, "hour"]
        : [Math.round(seconds / 60), "minute"];
  const short = new Intl.NumberFormat(language, {
    style: "unit",
    unit,
    unitDisplay: "long",
  }).format(value);
  return { short, long: t("Last {{range}}", { range: short }) };
}

/**
 * The one time range control: preset windows apply at once, a custom range
 * applies from two date-time fields. Presets stay relative, so their owner
 * resolves them against the current time whenever it queries.
 */
export function TimeRangePicker({
  value,
  presets,
  maxDays,
  minSeconds = 60,
  precision = "minute",
  earliest,
  resolvePreset,
  disabled,
  onChange,
}: {
  value: RangeSelection;
  /** Trailing windows in seconds. */
  presets: readonly number[];
  maxDays: number;
  minSeconds?: number;
  precision?: "minute" | "second";
  /** The earliest start the data still covers. */
  earliest?: Date | null;
  /** The window a preset covers now, when it is not simply the trailing seconds. */
  resolvePreset?: (seconds: number) => { start: Date; end: Date };
  disabled?: boolean;
  onChange: (value: RangeSelection) => void;
}) {
  const { t, i18n } = useTranslation();
  const language = i18n.resolvedLanguage;
  const [open, setOpen] = useState(false);
  // Keep exact instants until edited; draft text may be incomplete while typing.
  const [start, setStart] = useState<Date | string>("");
  const [end, setEnd] = useState<Date | string>("");
  const startText =
    typeof start === "string" ? start : formatLocalDateTime(start, precision);
  const endText =
    typeof end === "string" ? end : formatLocalDateTime(end, precision);
  const startDate =
    typeof start === "string" ? parseLocalDateTime(start) : start;
  const endDate = typeof end === "string" ? parseLocalDateTime(end) : end;
  const seconds =
    startDate && endDate ? (endDate.getTime() - startDate.getTime()) / 1000 : 0;
  const error =
    !startDate || !endDate || seconds <= 0
      ? t("Choose a valid time range.")
      : seconds < minSeconds
        ? t("Choose a range of at least 1 minute.")
        : seconds > maxDays * 86400
          ? t("Choose a range of up to {{count}} days.", { count: maxDays })
          : earliest && startDate < earliest
            ? t("Choose a start after {{date}}.", {
                date: new Intl.DateTimeFormat(language, {
                  dateStyle: "medium",
                  timeStyle: "short",
                }).format(earliest),
              })
            : undefined;
  const label =
    "preset" in value
      ? presetLabels(t, value.preset, language).long
      : new Intl.DateTimeFormat(language, {
          year:
            value.start.getFullYear() !== new Date().getFullYear()
              ? "numeric"
              : undefined,
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: precision === "second" ? "2-digit" : undefined,
          hourCycle: "h23",
        }).formatRange(value.start, value.end);
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) {
          // The fields open on the window currently shown.
          const now = Date.now();
          const window =
            "preset" in value
              ? (resolvePreset?.(value.preset) ?? {
                  start: new Date(now - value.preset * 1000),
                  end: new Date(now),
                })
              : value;
          setStart(window.start);
          setEnd(window.end);
        }
        setOpen(next);
      }}
    >
      <PopoverTrigger
        disabled={disabled}
        aria-label={`${t("Time range")}: ${label}`}
        render={
          <Button variant="outline" type="button" className={styles.trigger} />
        }
      >
        <span className={styles.triggerLabel}>{t("Time range")}</span>
        <span className={styles.triggerValue}>{label}</span>
        <CaretDownIcon size={16} aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup
        aria-label={t("Time range")}
        align="start"
        className={`${styles.popup} ${filterStyles.popup}`}
      >
        <div className={filterStyles.body}>
          <div
            className={filterStyles.presets}
            style={
              { "--columns": Math.min(presets.length, 3) } as CSSProperties
            }
          >
            {presets.map((preset) => {
              const names = presetLabels(t, preset, language);
              const active = "preset" in value && value.preset === preset;
              return (
                <Button
                  key={preset}
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={names.long}
                  aria-pressed={active}
                  data-active={active || undefined}
                  onClick={() => {
                    onChange({ preset });
                    setOpen(false);
                  }}
                >
                  {names.short}
                </Button>
              );
            })}
          </div>
          <div className={filterStyles.dates}>
            <DateTimeField
              label={t("Start time")}
              value={startText}
              clearable={false}
              precision={precision}
              onValueChange={(next) => {
                if (next !== startText) setStart(next);
              }}
            />
            <DateTimeField
              label={t("End time")}
              value={endText}
              clearable={false}
              precision={precision}
              onValueChange={(next) => {
                if (next !== endText) setEnd(next);
              }}
            />
          </div>
          {error && (
            <p role="alert" className={styles.error}>
              {error}
            </p>
          )}
          <div className={filterStyles.footer}>
            <span>{Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
            <Button
              size="sm"
              variant="outline"
              type="button"
              disabled={!!error}
              onClick={() => {
                if (!startDate || !endDate) return;
                onChange({ start: startDate, end: endDate });
                setOpen(false);
              }}
            >
              {t("Apply")}
            </Button>
          </div>
        </div>
      </PopoverPopup>
    </Popover>
  );
}
