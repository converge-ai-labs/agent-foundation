import { expect, it } from "vitest";
import { UNKNOWN } from "../../shared/unknown";
import { formatDuration } from "./format";

it.each([
  [0, "0ms"],
  [12.4, "12ms"],
  [999, "999ms"],
  [1000, "1s"],
  [12500, "12.5s"],
  [59000, "59s"],
  [60000, "1m 0s"],
  [60499, "1m 0s"],
  [60500, "1m 1s"],
  [119499, "1m 59s"],
  [119500, "2m 0s"],
  [119999, "2m 0s"],
  [120000, "2m 0s"],
  [179999, "3m 0s"],
])("formats %s ms as %s", (value, expected) => {
  expect(formatDuration(Number(value))).toBe(expected);
});

it.each([null, undefined, NaN, Infinity, -Infinity])(
  "keeps unreported or invalid duration %s unknown",
  (value) => {
    expect(formatDuration(value)).toBe(UNKNOWN);
  },
);
