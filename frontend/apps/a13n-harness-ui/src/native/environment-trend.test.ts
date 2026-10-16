import { describe, expect, it } from "vitest";
import {
  bytes,
  percent,
  trendSegments,
  smoothPath,
  zoomRange,
} from "./environment-trend";
import type { Schema } from "../transport/client";
const point = (
  minute: number,
  segment = 1,
  value: number | null = 0,
): Schema<"HistoryPoint"> => ({
  minute,
  segment,
  cpu_busy_percent: value == null ? null : { average: value, peak: value },
});
describe("environment trends", () => {
  it("preserves zero and separates missing minutes, gauges and generations", () => {
    const segments = trendSegments(
      [
        point(1),
        point(2),
        point(4),
        point(5, 2),
        point(6, 2, null),
        point(7, 2),
      ],
      "cpu_busy_percent",
    );
    expect(
      segments.map((segment) => segment.map((item) => item.minute)),
    ).toEqual([[1, 2], [4], [5], [7]]);
    expect(percent(0)).toBe("0.0%");
    expect(percent(null)).toBe("—");
    expect(bytes(0)).toBe("0 B");
    expect(bytes(undefined)).toBe("—");
    expect(bytes(1024 ** 3)).toBe("1.0 GiB");
  });
});

it("bounds zoom to retained history and the minute-resolution minimum", () => {
  expect(zoomRange({ start: 85, end: 100 }, 0.5, 100)).toEqual({
    start: 88.75,
    end: 96.25,
  });
  expect(zoomRange({ start: 99, end: 100 }, 0.5, 100)).toEqual({
    start: 98,
    end: 100,
  });
  expect(zoomRange({ start: -1340, end: 100 }, 2, 100)).toEqual({
    start: -1340,
    end: 100,
  });
});
it("interpolates exact values without overshooting extrema or flattening peaks", () => {
  const points = [0, 20, 100, 5, 5, 30].map((y, x) => ({ x: x * 10, y }));
  const path = smoothPath(points);
  expect(path.startsWith("M0,0")).toBe(true);
  const curves = path.split(" C").slice(1);
  expect(curves).toHaveLength(points.length - 1);
  for (const [i, curve] of curves.entries()) {
    const coords = curve.split(/[ ,]/).map(Number);
    const a = points[i]!;
    const b = points[i + 1]!;
    expect(coords.slice(-2)).toEqual([b.x, b.y]);
    for (const value of [coords[1]!, coords[3]!]) {
      expect(value).toBeGreaterThanOrEqual(Math.min(a.y, b.y));
      expect(value).toBeLessThanOrEqual(Math.max(a.y, b.y));
    }
  }
  expect(smoothPath([])).toBe("");
  expect(smoothPath([{ x: 0, y: 0 }])).toBe("M0,0");
});
