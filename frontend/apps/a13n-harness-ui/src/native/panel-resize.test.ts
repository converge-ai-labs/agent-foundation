import { expect, it } from "vitest";
import { panelResize } from "./panel-resize";

it.each([
  [44, 1000, { kind: "resize", width: 44 }],
  [71, 1000, { kind: "resize", width: 71 }],
  [72, 1000, { kind: "tabs" }],
  [29, 1000, { kind: "resize", width: 29 }],
  [28, 1000, { kind: "close" }],
  [74, 1600, { kind: "resize", width: 74 }],
  [75, 1600, { kind: "tabs" }],
  [25, 1600, { kind: "close" }],
  [26, 1600, { kind: "resize", width: 26 }],
  [35, 390, { kind: "close" }],
  [65, 390, { kind: "tabs" }],
  [50, 390, { kind: "resize", width: 50 }],
  [-20, 1000, { kind: "close" }],
  [120, 1000, { kind: "tabs" }],
])("resizes %s%% of %spx or snaps at an edge", (width, available, expected) => {
  expect(panelResize(width, available)).toEqual(expected);
});
