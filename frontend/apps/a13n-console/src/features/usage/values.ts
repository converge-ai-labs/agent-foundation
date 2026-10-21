import { formatDuration } from "../../shared/time-range";
import { UNKNOWN } from "../../shared/unknown";

export function presetWindow(days: number, now = new Date()) {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - days + 1);
  return { start: start.toISOString(), end: now.toISOString() };
}

/** Seconds in the Console's one duration format. */
export function duration(seconds: number | null, language?: string) {
  return seconds === null ? UNKNOWN : formatDuration(seconds * 1000, language);
}

export function percent(value: number | null) {
  return value === null ? UNKNOWN : `${(value * 100).toFixed(1)}%`;
}

/**
 * An axis that ends on a round value: the step is 1, 2, 2.5, or 5 times a
 * power of ten, so ticks read as $0, $2, $4, $6 rather than fractions of the
 * busiest day. Ticks run from the top of the axis down to zero.
 */
export function niceScale(maximum: number, steps = 3) {
  if (!(maximum > 0)) return { top: 1, ticks: [1, 0] };
  const raw = maximum / steps;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10]
    .map((factor) => factor * magnitude)
    .find((candidate) => candidate >= raw)!;
  const count = Math.ceil(maximum / step - 1e-9);
  const ticks = Array.from({ length: count + 1 }, (_, index) =>
    Number(((count - index) * step).toPrecision(12)),
  );
  return { top: count * step, ticks };
}

/** Large counts at a glance; the exact value belongs in the title. */
export function compactNumber(value: number, language?: string) {
  return new Intl.NumberFormat(language, {
    notation: "compact",
    maximumSignificantDigits: 3,
  }).format(value);
}
