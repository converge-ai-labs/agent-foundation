export type Point = { x: number; y: number };
export type Bounds = Point & { width: number; height: number };
export type DrawingTool =
  "arrow" | "rectangle" | "ellipse" | "pen" | "mosaic" | "redact";
export type Tool = DrawingTool | "select" | "crop" | "text";

type Appearance = { color: string; strokeWidth: number; fontSize: number };
type Transform = Point & { scaleX: number; scaleY: number };
type Mark = Transform & { id: string; color: string; strokeWidth: number };
export type Annotation = Mark &
  (
    | { kind: "arrow"; points: number[] }
    | { kind: "pen"; points: number[] }
    | {
        kind: "rectangle" | "ellipse" | "mosaic" | "redact";
        width: number;
        height: number;
      }
    | { kind: "text"; text: string; fontSize: number }
  );
export type Document = { crop: Bounds; annotations: Annotation[] };
export type History = {
  past: Document[];
  present: Document;
  future: Document[];
};
export type HistoryAction =
  { type: "commit"; document: Document } | { type: "undo" | "redo" };

export function historyReducer(state: History, action: HistoryAction): History {
  if (action.type === "commit") {
    if (action.document === state.present) return state;
    return {
      past: [...state.past, state.present],
      present: action.document,
      future: [],
    };
  }
  if (action.type === "undo") {
    const previous = state.past.at(-1);
    return previous
      ? {
          past: state.past.slice(0, -1),
          present: previous,
          future: [state.present, ...state.future],
        }
      : state;
  }
  const next = state.future[0];
  return next
    ? {
        past: [...state.past, state.present],
        present: next,
        future: state.future.slice(1),
      }
    : state;
}

export function clampPoint(point: Point, bounds: Bounds): Point {
  return {
    x: Math.max(bounds.x, Math.min(bounds.x + bounds.width, point.x)),
    y: Math.max(bounds.y, Math.min(bounds.y + bounds.height, point.y)),
  };
}

/** Crop boundaries are integral source pixels, independent of display zoom and DPR. */
export function cropBetween(start: Point, end: Point, bounds: Bounds): Bounds {
  const a = clampPoint(start, bounds);
  const b = clampPoint(end, bounds);
  const x = Math.min(
    bounds.x + bounds.width - 1,
    Math.floor(Math.min(a.x, b.x)),
  );
  const y = Math.min(
    bounds.y + bounds.height - 1,
    Math.floor(Math.min(a.y, b.y)),
  );
  return {
    x,
    y,
    width: Math.max(1, Math.ceil(Math.max(a.x, b.x)) - x),
    height: Math.max(1, Math.ceil(Math.max(a.y, b.y)) - y),
  };
}

export function startAnnotation(
  kind: DrawingTool,
  point: Point,
  appearance: Appearance,
  id: string,
): Annotation {
  const base = { id, ...point, ...appearance, scaleX: 1, scaleY: 1 };
  return kind === "arrow" || kind === "pen"
    ? { ...base, kind, points: [0, 0, 0, 0] }
    : { ...base, kind, width: 0, height: 0 };
}

export function drawAnnotation(
  mark: Annotation,
  start: Point,
  end: Point,
  constrained: boolean,
): Annotation {
  let dx = end.x - start.x;
  let dy = end.y - start.y;
  if (mark.kind === "text") return mark;
  if (mark.kind === "pen") return { ...mark, points: [...mark.points, dx, dy] };
  if (constrained) {
    if (mark.kind === "arrow") {
      const length = Math.hypot(dx, dy);
      const angle =
        Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
      dx = Math.cos(angle) * length;
      dy = Math.sin(angle) * length;
    } else {
      const side = Math.max(Math.abs(dx), Math.abs(dy));
      dx = Math.sign(dx || 1) * side;
      dy = Math.sign(dy || 1) * side;
    }
  }
  return mark.kind === "arrow"
    ? { ...mark, points: [0, 0, dx, dy] }
    : {
        ...mark,
        x: start.x + Math.min(0, dx),
        y: start.y + Math.min(0, dy),
        width: Math.abs(dx),
        height: Math.abs(dy),
      };
}

export function meaningfulAnnotation(mark: Annotation): boolean {
  if (mark.kind === "text") return !!mark.text.trim();
  if (mark.kind === "pen" || mark.kind === "arrow")
    return mark.points.some((value) => Math.abs(value) >= 2);
  return mark.width >= 2 && mark.height >= 2;
}

export function replaceAnnotation(
  document: Document,
  mark: Annotation,
): Document {
  return {
    ...document,
    annotations: document.annotations.map((item) =>
      item.id === mark.id ? mark : item,
    ),
  };
}
