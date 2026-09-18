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
}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  hint?: string;
  disabled?: boolean;
  clearable?: boolean;
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
            setTime(formatLocalDateTime(selected ?? new Date()).slice(11));
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
              className="justify-start"
              disabled={disabled}
            />
          }
          aria-label={label}
        >
          <CalendarDotsIcon aria-hidden="true" />
          <span>{display}</span>
        </PopoverTrigger>
        <PopoverPopup
          aria-label={label}
          className="w-auto max-w-[calc(100vw-2rem)] p-4"
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
            <div className="flex items-center justify-between gap-3">
              <span className="text-sm">{t("Time")}</span>
              <div className="flex items-center gap-2">
                <ChoiceField
                  label={t("Hour")}
                  hideLabel
                  options={hours}
                  value={time.slice(0, 2)}
                  onValueChange={(hour) => setTime(`${hour}:${time.slice(3)}`)}
                  className="w-20 [&_[data-slot=select-trigger]]:min-w-0"
                />
                <span aria-hidden="true">:</span>
                <ChoiceField
                  label={t("Minute")}
                  hideLabel
                  options={minutes}
                  value={time.slice(3)}
                  onValueChange={(minute) =>
                    setTime(`${time.slice(0, 2)}:${minute}`)
                  }
                  className="w-20 [&_[data-slot=select-trigger]]:min-w-0"
                />
              </div>
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
