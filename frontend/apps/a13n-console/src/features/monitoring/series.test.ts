import { expect, it } from "vitest";
import { queryStep, type MetricsResult } from "./api";
import { normalizeResult, formatValue } from "./series";

function local(points: number[][]): MetricsResult {
  return {
    observation: { status: "ready", device_id: "device-test" },
    history: {
      available_from: null,
      available_until: null,
      source: "temporary",
      temporary: true,
      retention_seconds: 3600,
      resolution_seconds: 60,
      series: [{ metric: "system_cpu_busy_ratio", points }],
    },
  };
}
const query = { kind: "metric", metric: "system_cpu_busy_ratio" } as const;
it("breaks lines across missing minutes, generation changes and duplicate segment buckets", () => {
  const result = normalizeResult(
    local([
      [60, 1, 0.1, 0.2],
      [120, 1, 0.2, 0.3],
      [240, 1, 0.4, 0.5],
      [300, 2, 0.6, 0.7],
      [300, 3, 0.8, 0.9],
      [360, 3, 0.9, 1],
    ]),
    query,
    60,
  );
  expect(result.series[0].points).toEqual([
    [60, 0.1],
    [120, 0.2],
    [180, null],
    [240, 0.4],
    [270, null],
    [300, null],
    [360, 0.9],
  ]);
  expect(result.notices).not.toContain("ready");
});
it("keeps missing numeric values and native histogram notices explicit", () => {
  const result = normalizeResult(
    {
      data: {
        resultType: "matrix",
        result: [
          {
            metric: { __name__: "rate", interface: "eth0" },
            values: [
              [0, "1"],
              [60, "NaN"],
              [240, "2"],
            ],
            histograms: [],
          },
        ],
      },
      status: "success",
      warnings: ["partial"],
    },
    { kind: "promql", source_id: "mds_test", expression: "rate(foo[5m])" },
    60,
  );
  expect(result.series[0].points).toEqual([
    [0, 1],
    [60, null],
    [150, null],
    [240, 2],
  ]);
  expect(result.series[0].label).toContain("interface=eth0");
  expect(result.notices).toContain("partial");
  expect(
    result.notices.some(
      (notice) => typeof notice === "string" && notice.includes("histogram"),
    ),
  ).toBe(true);
});
it("bounds minute aligned evaluation points across the supported range", () => {
  for (const duration of [60, 3600, 86400, 7776000]) {
    const step = queryStep({ start: 0, end: duration });
    expect(step % 60).toBe(0);
    expect(duration / step).toBeLessThanOrEqual(600);
  }
});
it("formats missing, ratio, signed byte rates and operation rates", () => {
  expect(formatValue(null, "count")).toBe("—");
  expect(formatValue(0.5, "ratio")).toBe("50%");
  expect(formatValue(-2048, "bytes_per_second")).toBe("-2 KiB/s");
  expect(formatValue(12, "operations_per_second")).toBe("12 /s");
});

it("bounds normalized series allocation before processing high-cardinality samples", () => {
  const result = normalizeResult(
    {
      status: "success",
      data: {
        resultType: "matrix",
        result: Array.from({ length: 1000 }, (_, index) => ({
          metric: { device: String(index) },
          values: [[60, "1"]],
        })),
      },
    },
    { kind: "promql", source_id: "mds_test", expression: "up" },
    60,
  );
  expect(result.series).toHaveLength(32);
  expect(result.notices).toContain(
    "Showing the first 32 series. Narrow the query to see others.",
  );
});
