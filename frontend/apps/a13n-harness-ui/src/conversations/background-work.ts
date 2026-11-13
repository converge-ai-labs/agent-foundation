import type { Schema } from "../transport/client";
import type { ProcessObservation } from "./process-observations";
import {
  describeTool,
  toolLabel,
  toolSummary,
  parsed,
  record,
  type ToolView,
} from "./tool-presentation";

export type BackgroundSection = "subagents" | "tools";
export const SECTION_LABELS: Record<BackgroundSection, string> = {
  subagents: "Subagents",
  tools: "Tool calls",
};
export type BackgroundTarget = {
  kind: "child" | "tool" | "process" | BackgroundSection;
  id: string;
  threadId: string;
  title: string;
  icon?: "terminal";
};
export type BackgroundItem = {
  target: BackgroundTarget;
  state: string;
  running: boolean;
  preview: string;
  started?: string;
  ended?: string | null;
  child?: Schema<"ChildExecutionView">;
  tool?: Schema<"ToolExecutionView">;
  process?: ProcessObservation;
  call?: ToolView;
};
export function backgroundKey(target: BackgroundTarget) {
  return `${target.kind}:${target.threadId}:${target.id}`;
}
export function targetSection(target: BackgroundTarget) {
  return target.kind === "subagents" || target.kind === "tools"
    ? target.kind
    : undefined;
}
export function sectionTarget(
  section: BackgroundSection,
  threadId: string,
  t: (key: string) => string,
): BackgroundTarget {
  return {
    kind: section,
    id: threadId,
    threadId,
    title: t(SECTION_LABELS[section]),
  };
}
export const GLYPH_COLORS = 8;
export const GLYPH_EYES = 4;
const GLYPH_SIDES = [3, 4, 4, 5, 6, 8];

function seededRandom(id: string) {
  let seed = 2166136261;
  for (const char of id) seed = Math.imul(seed ^ char.charCodeAt(0), 16777619);
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * A rounded polygon with eyes in a 24-unit box, derived from a Thread ID.
 * Side count, rotation, vertex jitter, proportions and corner rounding vary.
 */
export function glyphIdentity(id: string) {
  const random = seededRandom(id);
  const span = (limit: number) => (random() * 2 - 1) * limit;
  const round = (value: number) => Math.round(value * 100) / 100;
  const color = Math.floor(random() * GLYPH_COLORS);
  const sides = GLYPH_SIDES[Math.floor(random() * GLYPH_SIDES.length)]!;
  const flip = random() < 0.5;
  const base =
    sides === 4
      ? flip
        ? 45
        : 0
      : sides === 6
        ? flip
          ? 0
          : 30
        : sides === 8
          ? 22.5
          : -90;
  // Triangles get less jitter so they never collapse into a flat sliver.
  const jitter = sides === 3 ? 0.5 : 1;
  const turn = base + span(16 * jitter);
  const aspect = 1 + span(0.12 * jitter);
  let points = Array.from({ length: sides }, (_, index) => {
    const angle =
      ((turn + (index * 360) / sides + span(9 * jitter)) * Math.PI) / 180;
    const radius = 1 + span(0.1 * jitter);
    return [
      Math.cos(angle) * radius * aspect,
      (Math.sin(angle) * radius) / aspect,
    ];
  });
  // Normalise the area for similar visual weight while staying in the box.
  const xs = points.map(([x]) => x!);
  const ys = points.map(([, y]) => y!);
  const width = Math.max(...xs) - Math.min(...xs);
  const height = Math.max(...ys) - Math.min(...ys);
  const area =
    Math.abs(
      points.reduce((sum, [x, y], index) => {
        const [nx, ny] = points[(index + 1) % sides]!;
        return sum + x! * ny! - nx! * y!;
      }, 0),
    ) / 2;
  const scale = Math.min(
    (sides === 3 ? 23 : 21) / Math.max(width, height),
    Math.sqrt(300 / area),
  );
  const midX = (Math.max(...xs) + Math.min(...xs)) / 2;
  const midY = (Math.max(...ys) + Math.min(...ys)) / 2;
  points = points.map(([x, y]) => [
    12 + (x! - midX) * scale,
    12 + (y! - midY) * scale,
  ]);
  const [low, high] =
    sides === 3 ? [0.18, 0.34] : sides === 8 ? [0.5, 0.5] : [0.2, 0.5];
  const path =
    points
      .map((point, index) => {
        const toward = (other: number[], t: number) =>
          `${round(point[0]! + (other[0]! - point[0]!) * t)} ${round(point[1]! + (other[1]! - point[1]!) * t)}`;
        const t = low + random() * (high - low);
        const previous = points[(index + sides - 1) % sides]!;
        const next = points[(index + 1) % sides]!;
        return `${index ? "L" : "M"}${toward(previous, t)}Q${round(point[0]!)} ${round(point[1]!)} ${toward(next, t)}`;
      })
      .join("") + "Z";
  const eyeX = points.reduce((sum, [x]) => sum + x!, 0) / sides;
  const eyeY =
    points.reduce((sum, [, y]) => sum + y!, 0) / sides -
    (sides === 3 ? 0 : 0.6);
  return {
    color,
    path,
    eyes: Math.floor(random() * GLYPH_EYES),
    eyeX: round(eyeX),
    eyeY: round(eyeY),
    gaze: [round(span(0.7)), round(span(0.4))] as const,
    tilt: round(span(6)),
  };
}
export function executionReceipt(tool: ToolView) {
  const value = parsed(tool.result);
  return record(value) && typeof value.execution_id === "string"
    ? value
    : undefined;
}

export function backgroundItems(
  threadId: string,
  children: Schema<"ChildExecutionView">[],
  tools: Schema<"ToolExecutionView">[],
  processes: ProcessObservation[],
  t: (key: string, options?: Record<string, unknown>) => string,
  calls: ToolView[] = [],
): BackgroundItem[] {
  const latest = new Map<string, Schema<"ChildExecutionView">>();
  for (const child of children) {
    const previous = latest.get(child.child_thread_id);
    if (!previous || child.segment_index > previous.segment_index)
      latest.set(child.child_thread_id, child);
  }
  const values: BackgroundItem[] = [...latest.values()].map((child) => {
    const call = calls.find(
      (call) => executionReceipt(call)?.execution_id === child.execution_id,
    );
    const args = parsed(call?.input);
    return {
      target: {
        kind: "child",
        id: child.child_thread_id,
        threadId,
        title: child.subagent_name,
      },
      state:
        child.persisted_status === "running" &&
        child.local_status === "unavailable"
          ? "unavailable"
          : child.persisted_status,
      running:
        child.persisted_status === "running" && child.local_status === "active",
      preview:
        record(args) && typeof args.prompt === "string" ? args.prompt : "",
      started: child.created_at,
      ended: child.completed_at,
      child,
      call,
    };
  });
  for (const tool of tools.filter((tool) => tool.foreground_released)) {
    const call = calls.find(
      (call) =>
        (call.toolCallId ?? call.id) === tool.tool_call_id ||
        executionReceipt(call)?.execution_id === tool.execution_id,
    );
    const info = describeTool(
      call ?? { id: tool.tool_call_id, name: tool.tool_name },
    );
    values.push({
      target: {
        kind: "tool",
        id: tool.execution_id,
        threadId,
        title: info.shell
          ? info.command || toolLabel(info, t)
          : toolLabel(info, t),
        icon: tool.tool_name === "shell_exec" ? "terminal" : undefined,
      },
      state: tool.status,
      running: tool.status === "running" || tool.status === "queued",
      preview: info.shell ? "" : toolSummary(info, t),
      started: tool.created_at,
      ended: tool.completed_at,
      tool,
      call,
    });
  }
  for (const process of processes) {
    if (tools.some((tool) => tool.execution_id === process.processId)) continue;
    values.push({
      target: {
        kind: "process",
        id: `${process.runId}:${process.processId}`,
        threadId,
        title: process.command || process.processId,
      },
      state:
        process.phase === "exited" && process.exitCode
          ? "failed"
          : process.phase,
      running: process.phase === "running",
      preview: "",
      process,
    });
  }
  return values.sort(
    (a, b) =>
      Number(b.running) - Number(a.running) ||
      (b.ended ?? b.started ?? "").localeCompare(a.ended ?? a.started ?? "") ||
      backgroundKey(a.target).localeCompare(backgroundKey(b.target)),
  );
}
export function backgroundGroups(
  items: BackgroundItem[],
): { section: BackgroundSection; items: BackgroundItem[] }[] {
  return [
    { section: "subagents", items: items.filter((item) => item.child) },
    { section: "tools", items: items.filter((item) => !item.child) },
  ];
}
