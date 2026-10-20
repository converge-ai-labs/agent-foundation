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
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
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
}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  hint?: string;
  disabled?: boolean;
  clearable?: boolean;
  precision?: "minute" | "second";
}) {
  const id = useId();
  const { t, i18n } = useTranslation();
  const locale = i18n.resolvedLanguage ?? i18n.language;
  const [open, setOpen] = useState(false);
  const [day, setDay] = useState<Date>();
  const [time, setTime] = useState("00:00");
  const selected = parseLocalDateTime(value);
  const draft = day ? `${formatLocalDateTime(day).slice(0, 10)}T${time}` : "";
  const valid = parseLocalDateTime(draft);
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
            setDay(selected);
            setTime(
              formatLocalDateTime(selected ?? new Date(), precision).slice(11),
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
          className="w-auto max-w-[calc(100vw-2rem)] [&_[data-slot=popover-viewport]]:p-4"
        >
          <div className="flex flex-col gap-4">
            <Calendar
              mode="single"
              selected={day}
              onSelect={setDay}
              defaultMonth={selected}
              locale={locale?.startsWith("zh") ? zhCN : enUS}
              autoFocus
            />
            <div className="flex gap-2">
              <ChoiceField
                label={t("Hour")}
                options={hours}
                value={time.slice(0, 2)}
                onValueChange={(hour) => setTime(`${hour}:${time.slice(3)}`)}
                className="min-w-0 flex-1 [&_[data-slot=select-trigger]]:min-w-0"
              />
              <ChoiceField
                label={t("Minute")}
                options={minutes}
                value={time.slice(3, 5)}
                onValueChange={(minute) =>
                  setTime(`${time.slice(0, 2)}:${minute}${time.slice(5)}`)
                }
                className="min-w-0 flex-1 [&_[data-slot=select-trigger]]:min-w-0"
              />
              {precision === "second" && (
                <ChoiceField
                  label={t("Second")}
                  options={minutes}
                  value={time.slice(6, 8)}
                  onValueChange={(second) =>
                    setTime(`${time.slice(0, 5)}:${second}`)
                  }
                  className="min-w-0 flex-1 [&_[data-slot=select-trigger]]:min-w-0"
                />
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {Intl.DateTimeFormat().resolvedOptions().timeZone}
            </p>
            {day && !valid && (
              <p role="alert" className="text-sm text-destructive">
                {t("This local time does not exist. Choose another time.")}
              </p>
            )}
            <div className="flex justify-end gap-2 border-t pt-3">
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
                disabled={!valid}
                onClick={() => {
                  onValueChange(draft);
                  setOpen(false);
                }}
              >
                {t("Apply")}
              </Button>
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
