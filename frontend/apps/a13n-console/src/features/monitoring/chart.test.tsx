import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { MetricsChart } from "./chart";
import type { Panel } from "./api";
import type { ChartSeries } from "./series";

const state = vi.hoisted(() => ({
  setData: vi.fn(),
  setScale: vi.fn(),
  setSeries: vi.fn(),
  destroy: vi.fn(),
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
  useTranslation: () => ({ t: (key: string) => key }),
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
