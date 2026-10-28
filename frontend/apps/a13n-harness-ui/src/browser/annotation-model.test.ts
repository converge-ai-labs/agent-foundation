import { expect, it } from "vitest";
import {
  cropBetween,
  drawAnnotation,
  historyReducer,
  meaningfulAnnotation,
  replaceAnnotation,
  resizeAnnotation,
  startAnnotation,
  type Document,
  type DrawingTool,
  type History,
} from "./annotation-model";

const bounds = { x: 0, y: 0, width: 1600, height: 800 };
const appearance = { color: "#ef4444", strokeWidth: 4, fontSize: 28 };
it("keeps crop boundaries within integral source pixels, including reverse and edge drags", () => {
  expect(
    cropBetween({ x: 750.5, y: 420.2 }, { x: 200.2, y: 100.8 }, bounds),
  ).toEqual({ x: 200, y: 100, width: 551, height: 321 });
  expect(cropBetween({ x: -20, y: -10 }, { x: 2000, y: 1000 }, bounds)).toEqual(
    bounds,
  );
  expect(cropBetween({ x: 1600, y: 800 }, { x: 1600, y: 800 }, bounds)).toEqual(
    { x: 1599, y: 799, width: 1, height: 1 },
  );
});
it.each<DrawingTool>([
  "arrow",
  "pen",
  "rectangle",
  "ellipse",
  "mosaic",
  "redact",
])("records a completed %s gesture without mutating the start", (kind) => {
  const start = startAnnotation(kind, { x: 100, y: 100 }, appearance, "mark");
  expect(meaningfulAnnotation(start)).toBe(false);
  const end = drawAnnotation(
    start,
    { x: 100, y: 100 },
    { x: 20, y: 50 },
    false,
  );
  expect(meaningfulAnnotation(end)).toBe(true);
  expect(meaningfulAnnotation(start)).toBe(false);
  if ("width" in end)
    expect(end).toMatchObject({ x: 20, y: 50, width: 80, height: 50 });
});
it.each<DrawingTool>([
  "rectangle",
  "ellipse",
  "arrow",
  "pen",
  "mosaic",
  "redact",
])(
  "bakes a nonuniform %s resize into geometry without scaling its stroke",
  (kind) => {
    const mark = drawAnnotation(
      startAnnotation(kind, { x: 20, y: 40 }, appearance, "mark"),
      { x: 20, y: 40 },
      { x: 120, y: 90 },
      false,
    );
    const resized = resizeAnnotation(mark, {
      x: 30,
      y: 50,
      scaleX: 1.5,
      scaleY: 2,
    });
    expect(resized).toMatchObject({
      x: 30,
      y: 50,
      scaleX: 1,
      scaleY: 1,
      strokeWidth: 4,
    });
    if ("width" in resized)
      expect(resized).toMatchObject({ width: 150, height: 100 });
    else if ("points" in resized)
      expect(resized.points.slice(-2)).toEqual([150, 100]);
    expect(mark).toMatchObject({ x: 20, y: 40, scaleX: 1, scaleY: 1 });
  },
);
it("constrains shapes and arrows with Shift", () => {
  const start = { x: 10, y: 10 },
    end = { x: 90, y: 40 };
  const rectangle = drawAnnotation(
    startAnnotation("rectangle", start, appearance, "rect"),
    start,
    end,
    true,
  );
  expect(rectangle).toMatchObject({ width: 80, height: 80 });
  const arrow = drawAnnotation(
    startAnnotation("arrow", start, appearance, "arrow"),
    start,
    end,
    true,
  );
  expect(arrow.kind === "arrow" && arrow.points[3]).toBe(0);
});
it("undoes and redoes crop, move, resize and deletion as document edits; new edits clear redo", () => {
  const original: Document = { crop: bounds, annotations: [] };
  let history: History = { past: [], present: original, future: [] };
  const mark = drawAnnotation(
    startAnnotation("rectangle", { x: 20, y: 40 }, appearance, "rect"),
    { x: 20, y: 40 },
    { x: 200, y: 160 },
    false,
  );
  const versions = [
    { ...original, annotations: [mark] },
    { crop: { x: 10, y: 20, width: 800, height: 400 }, annotations: [mark] },
  ];
  versions.push(replaceAnnotation(versions[1], { ...mark, x: 80, scaleX: 2 }));
  versions.push({ ...versions[2], annotations: [] });
  for (const document of versions)
    history = historyReducer(history, { type: "commit", document });
  for (const previous of [versions[2], versions[1], versions[0], original]) {
    history = historyReducer(history, { type: "undo" });
    expect(history.present).toBe(previous);
  }
  for (const next of versions) {
    history = historyReducer(history, { type: "redo" });
    expect(history.present).toBe(next);
  }
  history = historyReducer(history, { type: "undo" });
  history = historyReducer(history, { type: "commit", document: original });
  expect(history.future).toEqual([]);
});
