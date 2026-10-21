import { useEffect, useRef, useState } from "react";
import { Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { Panel, TimeRange } from "./api";
import { formatValue, latestValue, type ChartSeries } from "./series";
import styles from "./monitoring.module.css";

const lightColors = [
  "#6252d8",
  "#0d9488",
  "#d97706",
  "#dc5173",
  "#0284c7",
  "#9333ea",
  "#4d7c0f",
  "#c2410c",
];
const darkColors = [
  "#a69bff",
  "#4dd4bf",
  "#f4bb54",
  "#fa8ba5",
  "#62bdf0",
  "#ce9aff",
  "#b1d56f",
  "#f6a56f",
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
  const currentRange = useRef(range);
  currentRange.current = range;
  const zoom = useRef(onZoom);
  zoom.current = onZoom;
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [search, setSearch] = useState("");
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  const colors = dark ? darkColors : lightColors;
  const matches = (item: ChartSeries) =>
    `${item.label} ${item.detail ?? ""}`
      .toLowerCase()
      .includes(search.trim().toLowerCase());
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setDark(document.documentElement.classList.contains("dark")),
    );
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);
  const { t } = useTranslation();
  const translate = useRef(t);
  translate.current = t;
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
        padding: [16, 12, 0, 0],
        legend: { show: false },
        scales: {
          x: { time: true, min: range.start, max: range.end },
          y: {
            auto: true,
            range: (_u, min, max) => {
              const unit =
                panel.unit === "auto" || !panel.unit
                  ? series[0]?.unit
                  : panel.unit;
              if (unit === "ratio" && min >= 0 && max <= 1) return [0, 1];
              return uPlot.rangeNum(
                Math.min(0, min),
                Math.max(0, max),
                0.12,
                true,
              );
            },
          },
        },
        axes: [
          {
            stroke: dark ? "#9da4b3" : "#697180",
            grid: { show: false },
            ticks: { show: false },
            font: "11px system-ui",
            size: 32,
            space: 90,
          },
          {
            stroke: dark ? "#9da4b3" : "#697180",
            grid: {
              stroke: dark ? "rgba(180,190,210,.10)" : "rgba(90,105,125,.09)",
              width: 1,
            },
            ticks: { show: false },
            font: "11px system-ui",
            size: 64,
            space: 42,
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
            width: 2,
            fill:
              series.length === 1
                ? `${colors[index % colors.length]}12`
                : undefined,
            spanGaps: false,
            points: {
              show: (u: uPlot, seriesIndex: number) => {
                let count = 0;
                for (const value of u.data[seriesIndex])
                  if (value != null && ++count > 1) return false;
                return count === 1;
              },
              size: 5,
            },
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
                  : translate.current("Latest");
              currentSeries.current.forEach((item, position) => {
                const node = legendValues.current[position];
                if (node)
                  node.textContent = formatValue(
                    hovering
                      ? u.data[position + 1][index]
                      : latestValue(item, currentRange.current),
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
  }, [topology, panel.visualization, panel.unit, syncKey, dark]);
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
    dark,
  ]);
  useEffect(() => {
    series.forEach((item, index) =>
      plot.current?.setSeries(index + 1, {
        show: !hidden.has(item.id) && matches(item),
      }),
    );
  }, [
    hidden,
    search,
    series,
    topology,
    panel.visualization,
    panel.unit,
    syncKey,
    dark,
  ]);
  if (!series.length)
    return (
      <div className={styles.noData}>{t("No samples in this time range")}</div>
    );
  if (panel.visualization === "stat")
    return (
      <div className={`${styles.stats} a13n-scrollbar`}>
        {series.map((item) => (
          <div key={item.id}>
            <strong>
              {formatValue(
                latestValue(item, range),
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
      <div className={styles.legendHeader}>
        <span ref={cursorTime} className={styles.cursorTime}>
          {t("Latest")}
        </span>
        {series.length > 4 && (
          <Input
            type="search"
            aria-label={t("Filter series")}
            placeholder={t("Filter series…")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        )}
      </div>
      {panel.legend !== false && (
        <div
          className={`${styles.legend} a13n-scrollbar`}
          aria-label={t("Toggle series")}
        >
          {series.map((item, index) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={!hidden.has(item.id)}
              title={item.detail ?? item.label}
              hidden={!matches(item)}
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
                  latestValue(item, range),
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
