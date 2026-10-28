import { useId, useState, type PointerEvent } from "react";
import type { Schema } from "../transport/client";
import styles from "./environment-monitor.module.css";

type Point = Schema<"HistoryPoint"> & { value?: Schema<"Gauge"> | null };
type Metric = Exclude<keyof Point, "minute" | "segment">;
export type TimeRange = { start: number; end: number };
export const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${value.toFixed(1)}%`;
export function bytes(value: number | null | undefined): string {
  if (value == null) return "—";
  const unit =
    value > 0
      ? Math.max(0, Math.min(4, Math.floor(Math.log(value) / Math.log(1024))))
      : 0;
  return `${(value / 1024 ** unit).toFixed(unit ? 1 : 0)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}
export const timeLabel = (minute: number) =>
  new Date(minute * 60000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

export function zoomRange(
  range: TimeRange,
  factor: number,
  latest: number,
): TimeRange {
  const width = Math.min(60, Math.max(2, (range.end - range.start) * factor));
  const end = Math.min(
    latest,
    Math.max(latest - 60 + width, (range.start + range.end + width) / 2),
  );
  return { start: end - width, end };
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

/** Cubic interpolation through every observation; bounded controls prevent overshoot. */
export function smoothPath(points: { x: number; y: number }[]): string {
  if (!points.length) return "";
  const slopes = points
    .slice(1)
    .map((p, i) => (p.y - points[i]!.y) / (p.x - points[i]!.x));
  const tangents = points.map((_, i) => {
    const before = slopes[i - 1] ?? slopes[0] ?? 0;
    const after = slopes[i] ?? before;
    return before * after <= 0
      ? 0
      : Math.sign(before) * Math.min(Math.abs(before), Math.abs(after));
  });
  let path = `M${points[0]!.x},${points[0]!.y}`;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]!;
    const b = points[i]!;
    const dx = (b.x - a.x) / 3;
    path += ` C${a.x + dx},${a.y + tangents[i - 1]! * dx} ${b.x - dx},${b.y - tangents[i]! * dx} ${b.x},${b.y}`;
  }
  return path;
}

export function Trend({
  title,
  metric,
  history,
  format,
  range,
  cursor,
  onCursor,
  onZoom,
}: {
  title: string;
  metric: Metric;
  history: Point[];
  format: (value: number | null | undefined) => string;
  range: TimeRange;
  cursor: number | null;
  onCursor: (minute: number | null) => void;
  onZoom: (range: TimeRange) => void;
}) {
  const helpId = useId();
  const [drag, setDrag] = useState<TimeRange | null>(null);
  const visible = history.filter(
    (p) => p.minute >= range.start && p.minute <= range.end,
  );
  const segments = trendSegments(visible, metric);
  const points = segments.flat();
  const selected =
    cursor == null ? points.at(-1) : points.find((p) => p.minute === cursor);
  const maximum =
    metric === "cpu_busy_percent"
      ? 100
      : Math.max(1, ...points.map((p) => p[metric]!.peak)) * 1.1;
  const x = (minute: number) =>
    2 + ((minute - range.start) / (range.end - range.start)) * 696;
  const y = (value: number) => 144 - (value / maximum) * 138;
  const minuteAt = (event: PointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(
      0,
      Math.min(1, (((event.clientX - rect.left) / rect.width) * 700 - 2) / 696),
    );
    return range.start + ratio * (range.end - range.start);
  };
  return (
    <section className={styles.trend} aria-label={`${title} history`}>
      <div className={styles.trendHeading}>
        <h3>{title}</h3>
        <span>
          <i className={styles.averageKey} /> Average{" "}
          <i className={styles.peakKey} /> Peak
        </span>
      </div>
      <div className={styles.plot}>
        <div className={styles.axis} aria-hidden="true">
          <span>{format(maximum)}</span>
          <span>{format(maximum / 2)}</span>
          <span>{format(0)}</span>
        </div>
        <svg
          viewBox="0 0 700 150"
          preserveAspectRatio="none"
          role="group"
          tabIndex={0}
          aria-label={`${title} interactive chart`}
          aria-describedby={helpId}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            const minute = minuteAt(event);
            event.currentTarget.setPointerCapture(event.pointerId);
            setDrag({ start: minute, end: minute });
          }}
          onPointerMove={(event) => {
            const minute = minuteAt(event);
            onCursor(Math.round(minute));
            if (drag) setDrag({ ...drag, end: minute });
          }}
          onPointerUp={(event) => {
            if (drag) {
              const end = minuteAt(event);
              if (Math.abs(end - drag.start) >= 2)
                onZoom({
                  start: Math.min(drag.start, end),
                  end: Math.max(drag.start, end),
                });
            }
            setDrag(null);
          }}
          onPointerCancel={() => {
            setDrag(null);
            onCursor(null);
          }}
          onLostPointerCapture={() => setDrag(null)}
          onPointerLeave={() => {
            if (!drag) onCursor(null);
          }}
          onBlur={() => onCursor(null)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setDrag(null);
              onCursor(null);
            }
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              onCursor(
                Math.max(
                  Math.ceil(range.start),
                  Math.min(
                    Math.floor(range.end),
                    (cursor ?? points.at(-1)?.minute ?? Math.floor(range.end)) +
                      (event.key === "ArrowLeft" ? -1 : 1),
                  ),
                ),
              );
            }
          }}
        >
          <path d="M0 144H700 M0 75H700 M0 6H700" className={styles.grid} />
          {segments.map((segment, i) =>
            (["peak", "average"] as const).map((kind) => {
              const coordinates = segment.map((p) => ({
                x: x(p.minute),
                y: y(p[metric]![kind]),
              }));
              return (
                <g
                  key={`${i}-${kind}`}
                  className={kind === "average" ? styles.average : styles.peak}
                >
                  <path d={smoothPath(coordinates)} />
                  {segment.length === 1 && (
                    <circle
                      cx={coordinates[0]!.x}
                      cy={coordinates[0]!.y}
                      r="2.5"
                    />
                  )}
                </g>
              );
            }),
          )}
          {cursor != null && cursor >= range.start && cursor <= range.end && (
            <line
              x1={x(cursor)}
              x2={x(cursor)}
              y1={6}
              y2={144}
              className={styles.crosshair}
            />
          )}
          {selected && cursor != null && (
            <circle
              cx={x(selected.minute)}
              cy={y(selected[metric]!.average)}
              r="3"
              className={styles.marker}
            />
          )}
          {drag && (
            <rect
              x={x(Math.min(drag.start, drag.end))}
              y={0}
              width={Math.abs(x(drag.end) - x(drag.start))}
              height={150}
              className={styles.selection}
            />
          )}
        </svg>
        {!points.length && (
          <span className={styles.emptyPlot}>
            No observations in this range
          </span>
        )}
        <div className={styles.timeAxis}>
          {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
            <span key={fraction}>
              {timeLabel(range.start + fraction * (range.end - range.start))}
            </span>
          ))}
        </div>
      </div>
      <div className={styles.readout}>
        <span>
          {selected
            ? new Date(selected.minute * 60000).toLocaleString()
            : cursor != null
              ? timeLabel(cursor)
              : "No observations yet"}
        </span>
        <span>
          Average <strong>{format(selected?.[metric]?.average)}</strong>
        </span>
        <span>
          Peak <strong>{format(selected?.[metric]?.peak)}</strong>
        </span>
      </div>
      <span id={helpId} className={styles.srOnly}>
        Drag to zoom, down to 2 minutes. Arrow keys move by a minute. Escape
        clears the selection.
      </span>
    </section>
  );
}
