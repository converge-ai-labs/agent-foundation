import { describe, expect, it } from "vitest";
import { bytes, percent, trendSegments } from "./environment-trend";
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
