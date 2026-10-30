import type { TFunction } from "i18next";
import {
  queryStep,
  type MetricsResult,
  type Panel,
  type PanelQuery,
  type TimeRange,
} from "./api";
import { metricName } from "./labels";

export type Point = [number, number | null];
export type ChartSeries = {
  id: string;
  /** The label values that tell series of one query apart. */
  label: string;
  /** A typed query's metric; PromQL series are named by their labels alone. */
  metric?: string;
  /** The exact metric and labels, for the title. */
  detail?: string;
  points: Point[];
  unit: Panel["unit"];
};
export type ChartNotice =
  string | { message: string; values: { hours: number } };
export type ChartData = { series: ChartSeries[]; notices: ChartNotice[] };
export const SERIES_LIMIT = 32;
const seriesLimitNotice =
  "Showing the first 32 series. Narrow the query to see others.";
const hiddenLabels = [
  "__name__",
  "environment_id",
  "device_id",
  "device_registration_id",
];
const labelEntries = (labels: Record<string, unknown>) =>
  Object.entries(labels)
    .filter(([key]) => !hiddenLabels.includes(key))
    .sort(([a], [b]) => a.localeCompare(b));
const labelsText = (labels: Record<string, unknown>) =>
  labelEntries(labels)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(", ");
// Values name a series; a bare number ("0") keeps its key so it still says what it counts.
const labelValues = (labels: Record<string, unknown>) =>
  labelEntries(labels)
    .map(([key, value]) =>
      /^\d+$/.test(String(value)) ? `${key} ${String(value)}` : String(value),
    )
    .join(" · ");
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/** A series' display name in the reader's language. */
export function seriesName(
  series: ChartSeries,
  t: TFunction,
  showMetric: boolean,
) {
  if (!series.metric || (!showMetric && series.label)) return series.label;
  return [metricName(t, series.metric), series.label]
    .filter(Boolean)
    .join(" · ");
}

export function metricUnit(metric: string): Panel["unit"] {
  if (metric.endsWith("_ratio")) return "ratio";
  if (metric.endsWith("_bytes_per_second")) return "bytes_per_second";
  if (metric.endsWith("_operations_per_second")) return "operations_per_second";
  if (metric.endsWith("_bytes")) return "bytes";
  return "count";
}

export function normalizeResult(
  result: MetricsResult,
  query: PanelQuery,
  step: number,
): ChartData {
  const name = query.kind === "metric" ? query.metric : query.expression;
  const unit = query.kind === "metric" ? metricUnit(query.metric) : "auto";
  if ("history" in result) {
    const { history, observation } = result;
    const notices: ChartNotice[] = [
      "Temporary history",
      {
        message: "Retention: {{hours}}h",
        values: { hours: history.retention_seconds / 3600 },
      },
    ];
    if (observation.status !== "ready") notices.push(observation.status);
    if (history.inventory_truncated || history.omitted_sources)
      notices.push("Resource inventory is truncated");
    if (history.available_from != null && history.available_until != null)
      notices.push(
        `${new Date(history.available_from * 1000).toLocaleString()} – ${new Date(history.available_until * 1000).toLocaleString()}`,
      );
    if (history.series.length > SERIES_LIMIT) notices.push(seriesLimitNotice);
    return {
      notices,
      series: history.series.slice(0, SERIES_LIMIT).map((series) => {
        const points: Point[] = [];
        let previous: (typeof series.points)[number] | undefined;
        for (const point of series.points) {
          if (previous && point[0] === previous[0]) {
            // Multiple segments in one bucket cannot form one continuous sample.
            points[points.length - 1] = [point[0], null];
            previous = point;
            continue;
          }
          if (
            previous &&
            (point[1] !== previous[1] ||
              point[0] - previous[0] > history.resolution_seconds * 1.5)
          )
            points.push([(point[0] + previous[0]) / 2, null]);
          points.push([point[0], point[2]]);
          previous = point;
        }
        const labels = series.labels ?? {};
        const suffix = labelsText(labels);
        const displayLabels = Object.fromEntries(
          Object.entries(labels).filter(
            ([key]) => key !== "identity" || !(labels.interface || labels.disk),
          ),
        );
        return {
          id: JSON.stringify([name, series.labels]),
          label: labelValues(displayLabels),
          metric: name,
          detail: suffix ? `${name} · ${suffix}` : name,
          points,
          unit,
        };
      }),
    };
  }
  const notices = [...(result.warnings ?? []), ...(result.infos ?? [])];
  const series: ChartSeries[] = [];
  if (result.data.resultType !== "matrix")
    return { series, notices: [...notices, "Expected a time-series result"] };
  for (const entry of result.data.result) {
    if (!record(entry)) continue;
    if (series.length >= SERIES_LIMIT) {
      notices.push(seriesLimitNotice);
      break;
    }
    if ("histograms" in entry)
      notices.push(
        "Native histogram samples are not rendered; query a scalar projection.",
      );
    const labels = record(entry.metric) ? entry.metric : {};
    const points: Point[] = [];
    if (Array.isArray(entry.values))
      for (const sample of entry.values) {
        if (!Array.isArray(sample) || typeof sample[0] !== "number") continue;
        const timestamp = sample[0],
          value = typeof sample[1] === "string" ? Number(sample[1]) : NaN;
        const previous = points.at(-1);
        if (previous && timestamp - previous[0] > step * 1.5)
          points.push([(timestamp + previous[0]) / 2, null]);
        points.push([timestamp, Number.isFinite(value) ? value : null]);
      }
    const suffix = labelsText(labels);
    series.push({
      id: JSON.stringify([name, labels]),
      label:
        labelValues(labels) ||
        (query.kind === "metric" ? "" : String(labels.__name__ ?? name)),
      metric: query.kind === "metric" ? name : undefined,
      detail: suffix ? `${name} · ${suffix}` : name,
      points,
      unit,
    });
  }
  return { series, notices: [...new Set(notices)] };
}

export function latestValue(series: ChartSeries, range: TimeRange) {
  const point = series.points.at(-1);
  return point &&
    point[0] >= range.start &&
    point[0] <= range.end &&
    range.end - point[0] <= queryStep(range) * 1.5
    ? point[1]
    : null;
}

export function formatValue(
  value: number | null | undefined,
  unit: Panel["unit"],
) {
  if (value === null || value === undefined || !Number.isFinite(value))
    return "—";
  if (unit === "ratio")
    return `${(value * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  if (unit === "bytes" || unit === "bytes_per_second") {
    const scale =
      value === 0
        ? 0
        : Math.max(
            0,
            Math.min(4, Math.floor(Math.log(Math.abs(value)) / Math.log(1024))),
          );
    return `${(value / 1024 ** scale).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${["B", "KiB", "MiB", "GiB", "TiB"][scale]}${unit === "bytes_per_second" ? "/s" : ""}`;
  }
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit === "seconds" ? " s" : unit === "operations_per_second" ? " /s" : ""}`;
}
