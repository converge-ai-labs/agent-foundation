import { expect, it } from "vitest";
import { speedLabel } from "./speed-label";

it.each([
  ["on", "Fast"],
  ["off", "Standard"],
  ["default", "Standard"],
  ["ultrafast", "Ultrafast"],
  [null, "—"],
  [undefined, "—"],
] as const)("labels request speed %s as %s", (state, label) => {
  expect(speedLabel(state)).toBe(label);
});
