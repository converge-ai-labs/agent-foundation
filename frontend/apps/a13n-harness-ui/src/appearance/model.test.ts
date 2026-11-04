import { expect, it } from "vitest";
import {
  defaultAppearance,
  parseAppearance,
  regions,
  sizeRules,
} from "./model";
it("defaults all six regions independently and validates each field", () => {
  const defaults = defaultAppearance();
  expect(defaults.sizes).toEqual({
    navigation: 13,
    interface: 14,
    conversation: 15,
    composer: 16,
    code: 12,
    terminal: 13,
  });
  for (const raw of [
    null,
    "bad json",
    "null",
    "[]",
    '{"version":2,"theme":"dark"}',
  ])
    expect(parseAppearance(raw)).toEqual(defaults);
  for (const region of regions) {
    const rule = sizeRules[region];
    for (const invalid of [rule.min - 1, rule.max + 1, 14.5, "16", null]) {
      expect(
        parseAppearance(
          JSON.stringify({ version: 1, sizes: { [region]: invalid } }),
        ),
      ).toEqual(defaults);
    }
    for (const size of [rule.min, rule.max]) {
      expect(
        parseAppearance(
          JSON.stringify({
            version: 1,
            theme: "system",
            sizes: { [region]: size },
          }),
        ),
      ).toEqual({
        ...defaults,
        theme: "system",
        sizes: { ...defaults.sizes, [region]: size },
      });
    }
  }
  expect(
    parseAppearance(
      '{"version":1,"theme":"unknown","sizes":{"code":18,"composer":9}}',
    ),
  ).toEqual({ ...defaults, sizes: { ...defaults.sizes, code: 18 } });
});
