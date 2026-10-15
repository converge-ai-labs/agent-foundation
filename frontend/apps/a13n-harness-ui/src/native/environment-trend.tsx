import { useState } from "react";
import type { Schema } from "../transport/client";
import styles from "./environment-monitor.module.css";

type Point = Schema<"HistoryPoint">;
type Metric = "cpu_busy_percent" | "memory_used_bytes" | "cgroup_memory_bytes";
export const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${value.toFixed(1)}%`;
export function bytes(value: number | null | undefined): string {
  if (value == null) return "—";
  const unit =
    value > 0 ? Math.min(4, Math.floor(Math.log(value) / Math.log(1024))) : 0;
  return `${(value / 1024 ** unit).toFixed(unit ? 1 : 0)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}

/** Missing minutes, missing gauges and connection generations break each line. */
export function trendSegments(history: Point[], metric: Metric): Point[][] {
  const segments: Point[][] = [];
  let current: Point[] = [];
  for (const point of history) {
    const previous = current.at(-1);
    if (!point[metric]) {
      current = [];
      continue;
    }
    if (
      !previous ||
      point.minute !== previous.minute + 1 ||
      point.segment !== previous.segment
    ) {
      current = [];
      segments.push(current);
    }
    current.push(point);
  }
  return segments;
}

export function Trend({
  title,
  metric,
  history,
  format,
}: {
  title: string;
  metric: Metric;
  history: Point[];
  format: (value: number | null | undefined) => string;
}) {
  const [selectedMinute, setSelectedMinute] = useState<number>();
  const end = Math.floor(Date.now() / 60000);
  const start = end - 1439;
  const segments = trendSegments(history, metric);
  const points = segments.flat();
  const selected =
    points.find((point) => point.minute === selectedMinute) ?? points.at(-1);
  const index = selected ? points.indexOf(selected) : 0;
  const maximum =
    metric === "cpu_busy_percent"
      ? 100
      : Math.max(1, ...points.map((point) => point[metric]!.peak));
  const x = (minute: number) => 2 + ((minute - start) / 1439) * 696;
  const y = (value: number) => 96 - (value / maximum) * 90;
  const time = (minute: number) =>
    new Date(minute * 60000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  return (
    <section className={styles.trend} aria-label={`${title} history`}>
      <div className={styles.trendHeading}>
        <h3>{title}</h3>
        <span>
          Average <i className={styles.averageKey} /> Peak{" "}
          <i className={styles.peakKey} />
        </span>
      </div>
      <div className={styles.axis}>
        <span>{format(maximum)}</span>
        <span>{format(0)}</span>
      </div>
      <svg
        viewBox="0 0 700 100"
        preserveAspectRatio="none"
        role="img"
        aria-label={`${title}: 24-hour minute averages and peaks`}
      >
        <path d="M0 96H700 M0 51H700 M0 6H700" className={styles.grid} />
        {segments.map((segment, i) =>
          (["peak", "average"] as const).map((kind) => (
            <g
              key={`${i}-${kind}`}
              className={kind === "average" ? styles.average : styles.peak}
            >
              <polyline
                points={segment
                  .map(
                    (point) => `${x(point.minute)},${y(point[metric]![kind])}`,
                  )
                  .join(" ")}
              />
              {segment.length === 1 && (
                <circle
                  cx={x(segment[0]!.minute)}
                  cy={y(segment[0]![metric]![kind])}
                  r="2"
                />
              )}
            </g>
          )),
        )}
      </svg>
      <div className={styles.timeAxis}>
        <span>{time(start)}</span>
        <span>Now</span>
      </div>
      {selected ? (
        <>
          <input
            type="range"
            min={0}
            max={points.length - 1}
            value={index}
            aria-label={`${title} observation minute`}
            onChange={(event) =>
              setSelectedMinute(points[Number(event.target.value)]!.minute)
            }
          />
          <p className={styles.readout}>
            {new Date(selected.minute * 60000).toLocaleString()} · Average{" "}
            {format(selected[metric]!.average)} · Peak{" "}
            {format(selected[metric]!.peak)}
          </p>
        </>
      ) : (
        <p className={styles.readout}>No observations yet</p>
      )}
    </section>
  );
}
