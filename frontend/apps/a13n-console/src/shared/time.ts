export function relativeTime(date: Date, locale?: string, now = Date.now()) {
  const seconds = (date.getTime() - now) / 1000;
  const distance = Math.abs(seconds);
  const [divisor, unit]: [number, Intl.RelativeTimeFormatUnit] =
    distance < 60
      ? [1, "second"]
      : distance < 3600
        ? [60, "minute"]
        : distance < 86400
          ? [3600, "hour"]
          : [86400, "day"];
  if (distance >= 7 * 86400) {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(
      date,
    );
  }
  return new Intl.RelativeTimeFormat(locale, {
    numeric: "auto",
    style: "short",
  }).format(Math.trunc(seconds / divisor) || 0, unit);
}
