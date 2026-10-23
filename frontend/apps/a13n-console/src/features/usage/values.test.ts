import { expect, it } from "vitest";
import { formatCostTicks } from "../../shared/cost";
import { duration, niceScale, percent, presetWindow } from "./values";

it("starts presets at local midnight and ends at the supplied instant", () => {
  const now = new Date(2026, 8, 28, 15, 20);
  const window = presetWindow(7, now);
  expect(new Date(window.start)).toEqual(new Date(2026, 8, 22));
  expect(window.end).toBe(now.toISOString());
});

it("formats run time in the reader's language", () => {
  expect(duration(null)).toBe("—");
  expect(duration(0.095, "en")).toBe("95 ms");
  expect(duration(2.564, "en")).toBe("2.56 s");
  expect(duration(59.999, "en")).toBe("1 m 00 s");
  expect(duration(65, "en")).toBe("1 m 05 s");
  expect(duration(90, "zh-CN")).toBe("1 m 30 s");
});

it("distinguishes no cache observations from a zero cache rate", () => {
  expect(percent(null)).toBe("—");
  expect(percent(0)).toBe("0.0%");
  expect(percent(0.25)).toBe("25.0%");
});

it("ends the axis on a round step above the busiest day", () => {
  expect(niceScale(5.79929)).toEqual({ top: 6, ticks: [6, 4, 2, 0] });
  expect(niceScale(0.0123)).toEqual({
    top: 0.015,
    ticks: [0.015, 0.01, 0.005, 0],
  });
  expect(niceScale(1_234_567)).toEqual({
    top: 1_500_000,
    ticks: [1_500_000, 1_000_000, 500_000, 0],
  });
  expect(niceScale(0)).toEqual({ top: 1, ticks: [1, 0] });
});

it("labels cost ticks with one precision, never coarser than cents", () => {
  expect(formatCostTicks(niceScale(5.79929).ticks)).toEqual([
    "$6.00",
    "$4.00",
    "$2.00",
    "$0.00",
  ]);
  expect(formatCostTicks(niceScale(0.0123).ticks)).toEqual([
    "$0.015",
    "$0.010",
    "$0.005",
    "$0.000",
  ]);
  expect(formatCostTicks([1500, 0])).toEqual(["$1,500.00", "$0.00"]);
});
