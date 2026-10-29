import { expect, it } from "vitest";
import { executionTime } from "./execution-time";

it.each([
  [0, "<1s"],
  [999, "<1s"],
  [1000, "1s"],
  [59999, "59s"],
  [60000, "1m"],
  [65200, "1m 5s"],
  [3660000, "1h 1m"],
])("formats recorded time %s as %s", (value, expected) => {
  expect(executionTime(Number(value))).toBe(expected);
});
it.each([undefined, null, -1, NaN, Infinity])(
  "does not invent time for %s",
  (value) => {
    expect(executionTime(value)).toBeUndefined();
  },
);
