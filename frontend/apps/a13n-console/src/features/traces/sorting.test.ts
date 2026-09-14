import { expect, it } from "vitest";
import { compareObservations, compareValues } from "./sorting";
import type { Schema } from "../../shared/api";

it("compares decimal costs exactly, including scientific notation", () => {
  expect(
    compareValues("9007199254740993", "9007199254740992", "asc"),
  ).toBeGreaterThan(0);
  expect(
    compareValues("1e-20", "0.00000000000000000002", "desc"),
  ).toBeGreaterThan(0);
  expect(compareValues("0.10", "1e-1", "asc")).toBe(0);
});

it("keeps unknowns last in both directions and preserves ties", () => {
  const values = [
    { id: "missing", value: null },
    { id: "first", value: "0.1" },
    { id: "second", value: "1e-1" },
    { id: "zero", value: "0" },
  ];
  for (const direction of ["asc", "desc"] as const) {
    const result = [...values].sort((a, b) =>
      compareValues(a.value, b.value, direction),
    );
    expect(result.at(-1)?.id).toBe("missing");
    expect(
      result
        .filter((item) => item.value && item.id !== "zero")
        .map((item) => item.id),
    ).toEqual(["first", "second"]);
  }
});

it("sorts reported durations rather than treating unfinished spans as zero", () => {
  const root = {
    started_at: "2026-09-11T00:00:00Z",
    ended_at: null,
  } as Schema["Observation"];
  const finished = { ...root, ended_at: "2026-09-11T00:00:01Z" };
  expect(
    compareObservations(root, finished, {
      field: "duration",
      direction: "desc",
    }),
  ).toBeGreaterThan(0);
  expect(
    compareObservations(
      finished,
      { ...finished, ended_at: "2026-09-11T00:00:02Z" },
      { field: "duration", direction: "asc" },
    ),
  ).toBeLessThan(0);
});
