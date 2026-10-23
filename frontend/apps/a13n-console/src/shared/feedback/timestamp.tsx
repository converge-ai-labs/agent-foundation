import { useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { relativeTime } from "../time";

// Lists share one clock, started only while a relative timestamp is mounted.
const listeners = new Set<() => void>();
let now = Date.now();
let timer: ReturnType<typeof setInterval> | undefined;
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!timer) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      listeners.forEach((notify) => notify());
    }, 60_000);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}
const snapshot = () => now;

function RelativeTime({ date, locale }: { date: Date; locale?: string }) {
  const current = useSyncExternalStore(subscribe, snapshot, snapshot);
  return relativeTime(date, locale, current);
}

export function Timestamp({
  value,
  relative = false,
  precise = false,
}: {
  value?: string | null;
  relative?: boolean;
  /** Seconds matter where durations are compared, as in a Run's record. */
  precise?: boolean;
}) {
  const { i18n, t } = useTranslation();
  const date = value ? new Date(value) : undefined;
  const valid = date && Number.isFinite(date.getTime());
  const full = valid
    ? new Intl.DateTimeFormat(i18n.resolvedLanguage, {
        dateStyle: "medium",
        timeStyle: precise ? "medium" : "short",
      }).format(date)
    : t("Unavailable");
  return (
    <time
      className="text-xs text-muted-foreground tabular-nums"
      dateTime={valid ? value! : undefined}
      title={full}
    >
      {valid && relative ? (
        <RelativeTime date={date} locale={i18n.resolvedLanguage} />
      ) : (
        full
      )}
    </time>
  );
}
