import { SearchPicker } from "a13n-ui";
import type { TFunction } from "i18next";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { Timestamp } from "../../shared/feedback";
import { zoneOffset } from "../conversations/format";
import styles from "./schedules.module.css";

const NUMBER = /^\d+$/;

/** Wall-clock time of day, written the way the reader's locale writes it. */
function clock(hour: number, minute: number, locale?: string) {
  return new Intl.DateTimeFormat(locale, {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(Date.UTC(2000, 0, 1, hour, minute));
}

/** Cron weekdays (0 and 7 are Sunday), Monday first; null for anything but numbers and ranges. */
function weekdays(field: string) {
  const days = new Set<number>();
  for (const part of field.split(",")) {
    const range = /^(\d+)(?:-(\d+))?$/.exec(part);
    if (!range) return null;
    const first = Number(range[1]),
      last = Number(range[2] ?? range[1]);
    if (first > last || last > 7) return null;
    for (let day = first; day <= last; day++) days.add(day % 7);
  }
  return [...days].sort((a, b) => ((a + 6) % 7) - ((b + 6) % 7));
}

/**
 * The cadence of the common five-field cron shapes in words, with times on the
 * schedule's own clock. Anything else returns null and stays as written.
 */
export function describeCron(
  expression: string,
  t: TFunction,
  locale?: string,
): string | null {
  const fields = expression.trim().split(/\s+/);
  if (fields.length !== 5 || fields[3] !== "*") return null;
  const [minute, hour, day, , weekday] = fields as [
    string,
    string,
    string,
    string,
    string,
  ];
  const step = (field: string) => /^\*\/(\d+)$/.exec(field)?.[1];
  if (day === "*" && weekday === "*") {
    const minutes = minute === "*" ? "1" : step(minute);
    if (minutes && hour === "*")
      return minutes === "1"
        ? t("Every minute")
        : t("Every {{count}} minutes", { count: Number(minutes) });
    const hours = hour === "*" ? "1" : step(hour);
    if (minute === "0" && hours)
      return hours === "1"
        ? t("Every hour")
        : t("Every {{count}} hours", { count: Number(hours) });
    if (NUMBER.test(minute) && hour === "*" && Number(minute) < 60)
      return t("Every hour at minute {{minute}}", { minute: Number(minute) });
  }
  if (!NUMBER.test(minute) || !NUMBER.test(hour)) return null;
  if (Number(minute) > 59 || Number(hour) > 23) return null;
  const time = clock(Number(hour), Number(minute), locale);
  const days = weekday === "*" ? [] : weekdays(weekday);
  if (!days) return null;
  if (day === "*" && (!days.length || days.length === 7))
    return t("Every day at {{time}}", { time });
  if (day === "*") {
    if (days.join() === "1,2,3,4,5")
      return t("Every weekday at {{time}}", { time });
    const name = new Intl.DateTimeFormat(locale, {
      weekday: "long",
      timeZone: "UTC",
    });
    // 1 January 2023 was a Sunday, cron day 0.
    const names = days.map((value) =>
      name.format(Date.UTC(2023, 0, 1 + value)),
    );
    return t("Every {{days}} at {{time}}", {
      days: new Intl.ListFormat(locale).format(names),
      time,
    });
  }
  if (!days.length && NUMBER.test(day) && +day >= 1 && +day <= 31)
    return t("Every month on day {{day}} at {{time}}", { day: +day, time });
  return null;
}

/** A zone as people pick it: its city, then its current offset. */
export function timeZoneLabel(timeZone: string, locale?: string) {
  if (timeZone === "UTC") return timeZone;
  const city = timeZone.split("/").at(-1)!.replaceAll("_", " ");
  const offset = zoneOffset(timeZone, locale);
  return offset ? `${city} (${offset})` : city;
}

export function TimeZonePicker({
  value,
  onValueChange,
  ...control
}: {
  id?: string;
  "aria-describedby"?: string;
  value: string;
  onValueChange: (value: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage;
  const [open, setOpen] = useState(false);
  // Hundreds of zones, each with its own offset, are listed only while the picker is open.
  const options = useMemo(
    () =>
      (open
        ? // UTC is not a geographic zone, so the runtime list leaves it out; a saved zone always stays.
          [...new Set(["UTC", ...Intl.supportedValuesOf("timeZone"), value])]
        : [value]
      ).map((zone) => ({
        value: zone,
        label: timeZoneLabel(zone, locale),
        description: zone === "UTC" ? undefined : zone,
      })),
    [open, locale, value],
  );
  return (
    <SearchPicker
      {...control}
      label={t("Time zone")}
      placeholder={t("Search time zones")}
      emptyMessage={t("No matching time zones")}
      groups={[{ label: t("Time zone"), options }]}
      value={value}
      onValueChange={onValueChange}
      onOpenChange={setOpen}
      // The picker filters the loaded zones itself; this only keeps it a
      // search field while the closed picker holds the chosen zone alone.
      onSearchChange={() => {}}
    />
  );
}

/** An instant on a given zone's clock, with the reader's own clock a hover away. */
export function ZonedTime({
  value,
  timeZone,
}: {
  value: string;
  timeZone?: string;
}) {
  const { i18n } = useTranslation();
  const format = (options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat(i18n.resolvedLanguage, {
      weekday: "short",
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      ...options,
    }).format(new Date(value));
  return (
    <time dateTime={value} title={format({ timeZoneName: "short" })}>
      {format({ timeZone })}
    </time>
  );
}

/** A schedule's timing in words; the exact rule is a hover away. */
export function ScheduleTiming({ rule }: { rule: Schema["ScheduleRule"] }) {
  const { t, i18n } = useTranslation();
  if (rule.type === "once") return <Timestamp value={rule.run_at} precise />;
  const locale = i18n.resolvedLanguage;
  return (
    <span
      className={styles.cadence}
      title={`${rule.expression} · ${rule.timezone}`}
    >
      <span>
        {describeCron(rule.expression, t, locale) ?? (
          <code>{rule.expression}</code>
        )}
      </span>
      <span className={styles.zone}>
        {timeZoneLabel(rule.timezone, locale)}
      </span>
    </span>
  );
}
