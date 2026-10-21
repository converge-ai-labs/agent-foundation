import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { MetricsChart } from "./chart";
import type { Panel } from "./api";
import type { ChartSeries } from "./series";
import uPlot from "uplot";

const state = vi.hoisted(() => ({
  setData: vi.fn(),
  setScale: vi.fn(),
  setSeries: vi.fn(),
  destroy: vi.fn(),
  t: (key: string) => key,
}));
vi.mock("uplot", () => ({
  default: Object.assign(
    vi.fn(function () {
      return {
        ...state,
        batch: (callback: () => void) => callback(),
        setSize: vi.fn(),
      };
    }),
    { join: (tables: unknown[]) => tables },
  ),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: state.t, i18n: { resolvedLanguage: "en" } }),
}));

it("retains Canvas and skips sample work for retained responses and legend changes", async () => {
  const user = userEvent.setup();
  const panel: Panel = {
    id: "cpu",
    title: "CPU",
    visualization: "timeseries",
    position: { x: 0, y: 0, w: 6, h: 5 },
    queries: [],
  };
  const series: ChartSeries[] = [
    {
      id: "cpu",
      label: "CPU",
      unit: "ratio",
      points: [
        [60, 0.5],
        [120, null],
        [180, 0.8],
      ],
    },
  ];
  const props = {
    panel,
    series,
    range: { start: 0, end: 3600 },
    syncKey: "test",
  };
  const { rerender, unmount } = render(<MetricsChart {...props} />);
  expect(state.setData).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: /CPU/ }));
  expect(state.setData).toHaveBeenCalledTimes(1);
  expect(state.setSeries).toHaveBeenLastCalledWith(1, { show: false });
  rerender(
    <MetricsChart {...props} series={series.map((item) => ({ ...item }))} />,
  );
  expect(state.setData).toHaveBeenCalledTimes(1);
  rerender(<MetricsChart {...props} range={{ start: 60, end: 3660 }} />);
  expect(state.setData).toHaveBeenCalledTimes(1);
  expect(state.setScale).toHaveBeenLastCalledWith("x", { min: 60, max: 3660 });
  rerender(
    <MetricsChart
      {...props}
      series={[{ ...series[0], points: [[240, 0.9]] }]}
    />,
  );
  expect(state.setData).toHaveBeenCalledTimes(2);
  expect(state.destroy).not.toHaveBeenCalled();
  rerender(
    <MetricsChart {...props} series={[{ ...series[0], id: "other" }]} />,
  );
  expect(state.destroy).toHaveBeenCalledTimes(1);
  expect(state.setData).toHaveBeenCalledTimes(3);
  unmount();
  expect(state.destroy).toHaveBeenCalledTimes(2);
});

it("filters plotted series without rebuilding data and preserves explicit legend toggles", async () => {
  const user = userEvent.setup();
  const panel: Panel = {
    id: "network",
    title: "Network",
    visualization: "timeseries",
    position: { x: 0, y: 0, w: 6, h: 5 },
    queries: [],
  };
  const series: ChartSeries[] = Array.from({ length: 6 }, (_, index) => ({
    id: `eth${index}`,
    label: `eth${index}`,
    detail: `interface=eth${index}`,
    unit: "bytes_per_second",
    points: [
      [60, index],
      [120, index + 1],
    ],
  }));
  const { rerender } = render(
    <MetricsChart
      panel={panel}
      series={series}
      range={{ start: 0, end: 180 }}
      syncKey="filter"
    />,
  );
  const dataCalls = state.setData.mock.calls.length;
  const input = screen.getByRole("searchbox", { name: "Filter series" });
  await user.type(input, "ETH3");
  expect(screen.getAllByRole("button")).toHaveLength(1);
  expect(state.setSeries).toHaveBeenCalledWith(4, { show: true });
  expect(state.setSeries).toHaveBeenLastCalledWith(6, { show: false });
  await user.click(screen.getByRole("button", { name: /eth3/ }));
  await user.clear(input);
  expect(screen.getAllByRole("button")).toHaveLength(6);
  expect(state.setSeries).toHaveBeenCalledWith(4, { show: false });
  expect(state.setSeries).toHaveBeenLastCalledWith(6, { show: true });
  expect(state.setData).toHaveBeenCalledTimes(dataCalls);
  await user.type(input, "renamed");
  expect(screen.queryAllByRole("button")).toHaveLength(0);
  rerender(
    <MetricsChart
      panel={panel}
      series={series.map((item) => ({ ...item, detail: "renamed" }))}
      range={{ start: 0, end: 180 }}
      syncKey="filter"
    />,
  );
  expect(screen.getAllByRole("button")).toHaveLength(6);
  expect(state.setSeries).toHaveBeenLastCalledWith(6, { show: true });
  expect(state.setData).toHaveBeenCalledTimes(dataCalls);
});

it("uses a stable percentage scale and updates isolated-point visibility with current samples", () => {
  render(
    <MetricsChart
      panel={{
        id: "cpu",
        title: "CPU",
        visualization: "timeseries",
        position: { x: 0, y: 0, w: 6, h: 5 },
        queries: [],
      }}
      series={[{ id: "cpu", label: "CPU", unit: "ratio", points: [[60, 0.2]] }]}
      range={{ start: 0, end: 180 }}
      syncKey="scale"
    />,
  );
  const options = vi.mocked(uPlot).mock.calls.at(-1)![0];
  const range = options.scales!.y.range;
  const show = options.series[1].points!.show;
  if (typeof range !== "function" || typeof show !== "function")
    throw new Error("Expected dynamic scale and point visibility");
  const chart = Object.assign(Object.create(uPlot.prototype) as uPlot, {
    data: [
      [60, 120],
      [0.2, null],
    ],
  });
  expect(range(chart, 0.2, 0.3, "y")).toEqual([0, 1]);
  expect(show(chart, 1, 0, 1)).toBe(true);
  chart.data = [
    [60, 120],
    [0.2, 0.3],
  ];
  expect(show(chart, 1, 0, 1)).toBe(false);
});

it("expires latest values in statistics and legends while retaining historical cursor values", () => {
  const panel: Panel = {
    id: "cpu",
    title: "CPU",
    position: { x: 0, y: 0, w: 6, h: 5 },
    queries: [],
  };
  const series: ChartSeries[] = [
    { id: "cpu", label: "CPU", unit: "ratio", points: [[120, 0.5]] },
  ];
  const props = {
    panel,
    series,
    range: { start: 0, end: 210 },
    syncKey: "stale",
  };
  const { rerender } = render(<MetricsChart {...props} />);
  expect(screen.getByRole("button", { name: /CPU/ }).textContent).toContain(
    "50%",
  );
  const options = vi.mocked(uPlot).mock.calls.at(-1)![0];
  rerender(<MetricsChart {...props} range={{ start: 0, end: 211 }} />);
  expect(screen.getByRole("button", { name: /CPU/ }).textContent).toContain(
    "—",
  );
  const chart = Object.assign(Object.create(uPlot.prototype) as uPlot, {
    data: [[120], [0.5]],
    cursor: { idx: 0, left: 10 },
  });
  options.hooks!.setCursor![0]!(chart);
  expect(screen.getByRole("button", { name: /CPU/ }).textContent).toContain(
    "50%",
  );
  state.t = (key) => (key === "Latest" ? "最新" : key);
  rerender(<MetricsChart {...props} range={{ start: 0, end: 211 }} />);
  chart.cursor.left = -1;
  options.hooks!.setCursor![0]!(chart);
  expect(screen.getByText("最新")).toBeDefined();
  state.t = (key) => key;
  expect(screen.getByRole("button", { name: /CPU/ }).textContent).toContain(
    "—",
  );
  rerender(
    <MetricsChart
      {...props}
      panel={{ ...panel, visualization: "stat" }}
      range={{ start: 0, end: 211 }}
    />,
  );
  expect(screen.getByText("—")).toBeDefined();
  rerender(
    <MetricsChart {...props} panel={{ ...panel, visualization: "stat" }} />,
  );
  expect(screen.getByText("50%")).toBeDefined();
});
