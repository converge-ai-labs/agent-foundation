import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { Panel, TimeRange } from "./api";
import {
  formatValue,
  latestValue,
  seriesName,
  type ChartSeries,
} from "./series";
import styles from "./monitoring.module.css";

// Byte ticks step through whole binary units, so an axis reads 0, 5, 10 MiB.
const binaryIncrements = [0, 1, 2, 3, 4].flatMap((power) =>
  [1, 2, 5, 10, 20, 50, 100, 200, 500].map((step) => step * 1024 ** power),
);
const time = (language: string | undefined, seconds: number) =>
  new Intl.DateTimeFormat(language, {
    ...(seconds > 129600 && { month: "short", day: "numeric" }),
    ...(seconds <= 345600 && {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }),
  });

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
  const { t, i18n } = useTranslation();
  const language = i18n.resolvedLanguage;
  const translate = useRef(t);
  translate.current = t;
  // The metric name only tells series apart when a panel mixes metrics.
  const mixed = new Set(series.map((item) => item.metric)).size > 1;
  const name = (item: ChartSeries) => seriesName(item, t, mixed);
  const legend = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflow, setOverflow] = useState(0);
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [search, setSearch] = useState("");
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  const colors = dark ? darkColors : lightColors;
  const matches = (item: ChartSeries) =>
    `${name(item)} ${item.detail ?? ""}`
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
  // Data refreshes update the existing Canvas; only series topology rebuilds it.
  const topology = JSON.stringify(
    series.map(({ id, label, unit }) => [id, label, unit]),
  );
  useEffect(() => {
    const series = currentSeries.current;
    if (!host.current || panel.visualization === "stat" || !series.length)
      return;
    const node = host.current;
    // Axes follow the theme tokens the chart host inherits.
    const style = getComputedStyle(node);
    const axis = {
      stroke: style.color,
      font: `${style.fontSize} ${style.fontFamily}`,
      ticks: { show: false },
    };
    const unit =
      panel.unit === "auto" || !panel.unit ? series[0]?.unit : panel.unit;
    const cursorFormat = new Intl.DateTimeFormat(language, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
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
            ...axis,
            grid: { show: false },
            size: 28,
            space: 96,
            values: (u, ticks) => {
              const format = time(
                language,
                (u.scales.x.max ?? 0) - (u.scales.x.min ?? 0),
              );
              return ticks.map((value) => format.format(value * 1000));
            },
          },
          {
            ...axis,
            grid: { stroke: style.borderTopColor, width: 1 },
            size: 64,
            space: 42,
            incrs:
              unit === "bytes" || unit === "bytes_per_second"
                ? binaryIncrements
                : undefined,
            values: (_u, ticks) =>
              ticks.map((value) => formatValue(value, unit)),
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
                  ? cursorFormat.format(u.data[0][index] * 1000)
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
  }, [topology, panel.visualization, panel.unit, syncKey, dark, language]);
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
    language,
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
    language,
  ]);
  // Two legend rows stay visible; the rest is counted until the reader expands it.
  useLayoutEffect(() => {
    const node = legend.current;
    if (!node) return;
    const measure = () => {
      let count = 0;
      if (node.scrollHeight > node.clientHeight + 1)
        for (const child of node.children)
          if (
            child instanceof HTMLElement &&
            !child.hidden &&
            child.offsetTop >= node.clientHeight
          )
            count++;
      setOverflow(count);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [series, search, expanded]);
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
            <span title={item.detail ?? name(item)}>{name(item)}</span>
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
            size="sm"
            className={styles.legendSearch}
            aria-label={t("Filter series")}
            placeholder={t("Filter series…")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        )}
        {panel.legend !== false && (overflow > 0 || expanded) && (
          <Button
            size="xs"
            variant="ghost"
            className={styles.legendToggle}
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded
              ? t("Show less")
              : t("{{count}} more", { count: overflow })}
          </Button>
        )}
      </div>
      {panel.legend !== false && (
        <div
          ref={legend}
          className={`${styles.legend} a13n-scrollbar`}
          data-expanded={expanded || undefined}
          aria-label={t("Toggle series")}
        >
          {series.map((item, index) => (
            <button
              key={item.id}
              type="button"
              aria-pressed={!hidden.has(item.id)}
              title={item.detail ?? name(item)}
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
              <span>{name(item)}</span>
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
