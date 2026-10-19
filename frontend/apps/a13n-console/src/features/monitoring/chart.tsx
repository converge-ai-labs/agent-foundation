import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { Panel, TimeRange } from "./api";
import { formatValue, type ChartSeries } from "./series";
import styles from "./monitoring.module.css";

const colors = [
  "#818cf8",
  "#2dd4bf",
  "#fbbf24",
  "#fb7185",
  "#38bdf8",
  "#c084fc",
  "#a3e635",
  "#fb923c",
];
export function MetricsChart({
  series,
  panel,
  range,
  syncKey,
  onZoom,
}: {
  series: ChartSeries[];
  panel: Panel;
  range: TimeRange;
  syncKey: string;
  onZoom?: (range: TimeRange) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const rendered = useRef<{ series: ChartSeries[]; range: TimeRange } | null>(
    null,
  );
  const legendValues = useRef<(HTMLElement | null)[]>([]);
  const cursorTime = useRef<HTMLSpanElement>(null);
  const currentSeries = useRef(series);
  currentSeries.current = series;
  const zoom = useRef(onZoom);
  zoom.current = onZoom;
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const { t } = useTranslation();
  // Data refreshes update the existing Canvas; only series topology rebuilds it.
  const topology = JSON.stringify(
    series.map(({ id, label, unit }) => [id, label, unit]),
  );
  useEffect(() => {
    const series = currentSeries.current;
    if (!host.current || panel.visualization === "stat" || !series.length)
      return;
    const node = host.current;
    const chart = new uPlot(
      {
        width: Math.max(100, node.clientWidth),
        height: Math.max(100, node.clientHeight),
        padding: [12, 12, 0, 0],
        legend: { show: false },
        scales: {
          x: { time: true, min: range.start, max: range.end },
          y: { auto: true },
        },
        axes: [
          {
            stroke: "#888899",
            grid: { show: false },
            ticks: { show: false },
            font: "11px system-ui",
            size: 40,
          },
          {
            stroke: "#888899",
            grid: { stroke: "rgba(128,128,148,.12)", width: 1 },
            ticks: { show: false },
            font: "11px system-ui",
            size: 64,
            values: (_u, ticks) =>
              ticks.map((value) =>
                formatValue(
                  value,
                  panel.unit === "auto" || !panel.unit
                    ? series[0]?.unit
                    : panel.unit,
                ),
              ),
          },
        ],
        series: [
          {},
          ...series.map((item, index) => ({
            label: item.label,
            stroke: colors[index % colors.length],
            width: 1.5,
            spanGaps: false,
            points: { show: false },
            value: (_u: uPlot, value: number) =>
              formatValue(
                value,
                panel.unit === "auto" || !panel.unit ? item.unit : panel.unit,
              ),
          })),
        ],
        cursor: {
          drag: { x: true, y: false, setScale: false },
          sync: { key: syncKey, scales: ["x", null] },
          points: { size: 5 },
        },
        hooks: {
          setCursor: [
            (u) => {
              const index = u.cursor.idx;
              const hovering =
                index != null && u.cursor.left != null && u.cursor.left >= 0;
              if (cursorTime.current)
                cursorTime.current.textContent = hovering
                  ? new Date(u.data[0][index] * 1000).toLocaleString()
                  : "";
              currentSeries.current.forEach((item, position) => {
                const node = legendValues.current[position];
                if (node)
                  node.textContent = formatValue(
                    hovering
                      ? u.data[position + 1][index]
                      : item.points.at(-1)?.[1],
                    panel.unit === "auto" || !panel.unit
                      ? item.unit
                      : panel.unit,
                  );
              });
            },
          ],
          setSelect: [
            (u) => {
              if (u.select.width < 8) return;
              const start = Math.floor(u.posToVal(u.select.left, "x"));
              const end = Math.ceil(
                u.posToVal(u.select.left + u.select.width, "x"),
              );
              u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
              if (end - start >= 60) zoom.current?.({ start, end });
            },
          ],
        },
      },
      [[], ...series.map(() => [])],
      node,
    );
    plot.current = chart;
    const observer = new ResizeObserver(() =>
      chart.setSize({
        width: Math.max(100, node.clientWidth),
        height: Math.max(100, node.clientHeight),
      }),
    );
    observer.observe(node);
    return () => {
      observer.disconnect();
      chart.destroy();
      plot.current = null;
      rendered.current = null;
    };
    // Topology owns construction; data and range have their own update below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topology, panel.visualization, panel.unit, syncKey]);
  useEffect(() => {
    const chart = plot.current;
    if (!chart) return;
    // Normalized points are immutable. Parent renders may recreate wrappers,
    // but retained responses and legend toggles need no scan of sample values.
    const previous = rendered.current;
    const changed =
      !previous ||
      previous.series.length !== series.length ||
      series.some(
        (item, index) => item.points !== previous.series[index].points,
      );
    const rangeChanged =
      !previous ||
      previous.range.start !== range.start ||
      previous.range.end !== range.end;
    if (!changed && !rangeChanged) return;
    chart.batch(() => {
      if (changed) {
        const tables: uPlot.AlignedData[] = series.map((item) => [
          item.points.map((point) => point[0]),
          item.points.map((point) => point[1]),
        ]);
        chart.setData(
          uPlot.join(
            tables,
            tables.map(() => [0, 2]),
          ),
        );
      }
      chart.setScale("x", { min: range.start, max: range.end });
    });
    rendered.current = { series, range };
  }, [
    series,
    topology,
    range.start,
    range.end,
    panel.visualization,
    panel.unit,
    syncKey,
  ]);
  useEffect(() => {
    series.forEach((item, index) =>
      plot.current?.setSeries(index + 1, { show: !hidden.has(item.id) }),
    );
  }, [hidden, topology, panel.visualization, panel.unit, syncKey]);
  if (!series.length)
    return (
      <div className={styles.noData}>{t("No samples in this time range")}</div>
    );
  if (panel.visualization === "stat")
    return (
      <div className={styles.stats}>
        {series.map((item, index) => (
          <div key={item.id}>
            <strong style={{ color: colors[index % colors.length] }}>
              {formatValue(
                item.points.at(-1)?.[1],
                panel.unit === "auto" || !panel.unit ? item.unit : panel.unit,
              )}
            </strong>
            <span title={item.label}>{item.label}</span>
          </div>
        ))}
      </div>
    );
  return (
    <div className={styles.chart}>
      <div
        className={styles.canvas}
        ref={host}
        role="img"
        aria-label={`${panel.title}: ${series.length} ${t("series")}`}
      />
      <span ref={cursorTime} className={styles.cursorTime} />
      {panel.legend !== false && (
        <div className={styles.legend} aria-label={t("Toggle series")}>
          {series.map((item, index) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={!hidden.has(item.id)}
              title={item.label}
              onClick={() =>
                setHidden((previous) => {
                  const next = new Set(previous);
                  if (next.has(item.id)) next.delete(item.id);
                  else next.add(item.id);
                  return next;
                })
              }
            >
              <i style={{ background: colors[index % colors.length] }} />
              <span>{item.label}</span>
              <b
                ref={(node) => {
                  legendValues.current[index] = node;
                }}
              >
                {formatValue(
                  item.points.at(-1)?.[1],
                  panel.unit === "auto" || !panel.unit ? item.unit : panel.unit,
                )}
              </b>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
