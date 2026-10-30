import { enUS, zhCN } from "@daypicker/react/locale";
import {
  Button,
  Calendar,
  ChoiceField,
  Field,
  FieldDescription,
  FieldLabel,
  Popover,
  PopoverPopup,
  PopoverTrigger,
} from "a13n-ui";
import { CalendarDotsIcon } from "@phosphor-icons/react";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import filterStyles from "./filter-popover.module.css";
import { formatLocalDateTime, parseLocalDateTime } from "../local-date-time";

const hours = Array.from({ length: 24 }, (_, index) => {
  const value = String(index).padStart(2, "0");
  return { value, label: value };
});
const minutes = Array.from({ length: 60 }, (_, index) => {
  const value = String(index).padStart(2, "0");
  return { value, label: value };
});

export function DateTimeField({
  label,
  value,
  onValueChange,
  hint,
  disabled,
  clearable = true,
  precision = "minute",
  futureOnly = false,
}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  hint?: string;
  disabled?: boolean;
  clearable?: boolean;
  precision?: "minute" | "second";
  futureOnly?: boolean;
}) {
  const id = useId();
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const [open, setOpen] = useState(false);
  const [day, setDay] = useState<Date>();
  const [time, setTime] = useState("00:00");
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!open || !futureOnly) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [open, futureOnly]);
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const selected = parseLocalDateTime(value);
  const currentYear = new Date().getFullYear();
  const selectedYear = selected?.getFullYear() ?? currentYear;
  const draft = day ? `${formatLocalDateTime(day).slice(0, 10)}T${time}` : "";
  const valid = parseLocalDateTime(draft);
  const past = futureOnly && !!valid && valid.getTime() <= now;
  // Keep a unit selectable if it still contains a future instant.
  const isPast = (hour: number, minute: number, second: number) => {
    if (!futureOnly || !day) return false;
    const date = new Date(day);
    date.setHours(hour, minute, second, 0);
    return date.getTime() <= now;
  };
  const display = selected
    ? new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        second: precision === "second" ? "2-digit" : undefined,
        hourCycle: "h23",
      }).format(selected)
    : t("Select date and time");
  return (
    <Field className="min-w-0" disabled={disabled}>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Popover
        open={open}
        onOpenChange={(next) => {
          if (next) {
            const openedAt = Date.now();
            setNow(openedAt);
            setDay(selected);
            setTime(
              formatLocalDateTime(
                selected ?? new Date(openedAt + (futureOnly ? 300_000 : 0)),
                precision,
              ).slice(11),
            );
          }
          setOpen(next);
        }}
      >
        <PopoverTrigger
          id={id}
          aria-describedby={hint ? `${id}-description` : undefined}
          render={
            <Button
              variant="outline"
              className="w-full justify-between font-normal"
              disabled={disabled}
            />
          }
          aria-label={label}
        >
          <span className={selected ? undefined : "text-muted-foreground"}>
            {display}
          </span>
          <CalendarDotsIcon
            aria-hidden="true"
            className="text-muted-foreground"
          />
        </PopoverTrigger>
        <PopoverPopup
          align="start"
          aria-label={label}
          className={`${filterStyles.popup} w-[304px] min-w-0 max-w-[calc(100vw-1.5rem)]`}
        >
          <div className="flex flex-col gap-3">
            <Calendar
              className="self-center [--cell-size:--spacing(9)] sm:[--cell-size:--spacing(8)]"
              mode="single"
              selected={day}
              onSelect={setDay}
              defaultMonth={selected ?? today}
              disabled={futureOnly ? { before: today } : undefined}
              captionLayout="dropdown"
              navLayout="around"
              startMonth={
                futureOnly
                  ? today
                  : new Date(Math.min(currentYear - 100, selectedYear), 0)
              }
              endMonth={new Date(Math.max(currentYear + 100, selectedYear), 11)}
              formatters={{
                formatMonthDropdown: (date) =>
                  new Intl.DateTimeFormat(locale, { month: "short" }).format(
                    date,
                  ),
              }}
              locale={locale?.startsWith("zh") ? zhCN : enUS}
              autoFocus
            />
            <div className="flex gap-2">
              <ChoiceField
                data-field-layout="stacked"
                label={t("Hour")}
                options={hours.map((option) => ({
                  ...option,
                  disabled: isPast(
                    Number(option.value),
                    59,
                    precision === "second" ? 59 : 0,
                  ),
                }))}
                value={time.slice(0, 2)}
                onValueChange={(hour) => setTime(`${hour}:${time.slice(3)}`)}
                className="min-w-0 flex-1 [&_[data-slot=select-trigger]]:min-w-0"
              />
              <ChoiceField
                data-field-layout="stacked"
                label={t("Minute")}
                options={minutes.map((option) => ({
                  ...option,
                  disabled: isPast(
                    Number(time.slice(0, 2)),
                    Number(option.value),
                    precision === "second" ? 59 : 0,
                  ),
                }))}
                value={time.slice(3, 5)}
                onValueChange={(minute) =>
                  setTime(`${time.slice(0, 2)}:${minute}${time.slice(5)}`)
                }
                className="min-w-0 flex-1 [&_[data-slot=select-trigger]]:min-w-0"
              />
              {precision === "second" && (
                <ChoiceField
                  data-field-layout="stacked"
                  label={t("Second")}
                  options={minutes.map((option) => ({
                    ...option,
                    disabled: isPast(
                      Number(time.slice(0, 2)),
                      Number(time.slice(3, 5)),
                      Number(option.value),
                    ),
                  }))}
                  value={time.slice(6, 8)}
                  onValueChange={(second) =>
                    setTime(`${time.slice(0, 5)}:${second}`)
                  }
                  className="min-w-0 flex-1 [&_[data-slot=select-trigger]]:min-w-0"
                />
              )}
            </div>
            {day && !valid && (
              <p role="alert" className="text-sm text-destructive">
                {t("This local time does not exist. Choose another time.")}
              </p>
            )}
            {past && (
              <p role="alert" className="text-sm text-destructive">
                {t(
                  "This time has already passed. Choose a future date and time.",
                )}
              </p>
            )}
            <div className={filterStyles.footer}>
              <span>{Intl.DateTimeFormat().resolvedOptions().timeZone}</span>
              <div className={filterStyles.actions}>
                {clearable && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      onValueChange("");
                      setOpen(false);
                    }}
                  >
                    {t("Clear")}
                  </Button>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!valid || past}
                  onClick={() => {
                    if (futureOnly && valid && valid.getTime() <= Date.now()) {
                      setNow(Date.now());
                      return;
                    }
                    onValueChange(draft);
                    setOpen(false);
                  }}
                >
                  {t("Apply")}
                </Button>
              </div>
            </div>
          </div>
        </PopoverPopup>
      </Popover>
      {hint && (
        <FieldDescription id={`${id}-description`}>{hint}</FieldDescription>
      )}
    </Field>
  );
}
