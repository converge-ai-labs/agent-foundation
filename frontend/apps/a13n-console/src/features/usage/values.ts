import {
  formatLocalDateTime,
  parseLocalDateTime,
} from "../../shared/local-date-time";
import { UNKNOWN } from "../../shared/unknown";

export function presetWindow(days: number, now = new Date()) {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - days + 1);
  return { start: start.toISOString(), end: now.toISOString() };
}

export function customWindow(start: string, end: string) {
  const from = parseLocalDateTime(start),
    to = parseLocalDateTime(end);
  if (
    !from ||
    !to ||
    to <= from ||
    to.getTime() - from.getTime() > 366 * 86_400_000
  )
    return null;
  return { start: from.toISOString(), end: to.toISOString() };
}

export function localWindow(window: { start: string; end: string }) {
  return {
    start: formatLocalDateTime(new Date(window.start)),
    end: formatLocalDateTime(new Date(window.end)),
  };
}

export function duration(value: number | null) {
  if (value === null) return UNKNOWN;
  return value >= 60
    ? `${(value / 60).toFixed(1)} min`
    : `${value.toFixed(1)} s`;
}

export function percent(value: number | null) {
  return value === null ? UNKNOWN : `${(value * 100).toFixed(1)}%`;
}
