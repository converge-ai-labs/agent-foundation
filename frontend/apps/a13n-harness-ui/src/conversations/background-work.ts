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

export type BackgroundTarget = {
  kind: "child" | "tool" | "process";
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
export function glyphIdentity(id: string) {
  let hash = 2166136261;
  for (const char of id) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
  return { color: (hash >>> 0) % 6, shape: ((hash >>> 8) % 3) as 0 | 1 | 2 };
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
export function backgroundGroups(items: BackgroundItem[], endedLimit = 20) {
  const ended = items.filter((item) => !item.running);
  return {
    running: items.filter((item) => item.running),
    ended: ended.slice(0, endedLimit),
    more: ended.length > endedLimit,
  };
}
