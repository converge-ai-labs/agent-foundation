import type { RunEvent } from "@converge.ai/a13n";
import { isObject, type PresentedItem } from "./projection";

export type ExecutionKind =
  | "llm"
  | "tool"
  | "hitl"
  | "subagent"
  | "compaction"
  | "handoff"
  | "memory"
  | "codeact";
export interface ExecutionStep {
  id: string;
  scope: string;
  kind: ExecutionKind;
  name?: string;
  state: string;
  items: string[];
  callId?: string;
  parentId?: string;
  childScope?: string;
  dispatchOnly?: boolean;
  detail?: unknown;
}
export interface ExecutionObservation {
  id: string;
  name: string;
  detail: unknown;
}
export interface Execution {
  steps: ExecutionStep[];
  items: Map<string, PresentedItem>;
  observations?: ExecutionObservation[];
  lastCursor?: string;
}
export function executionScope(
  entry: RunEvent,
  harnessRunId = entry.event.harness_run_id ?? "",
) {
  return `${entry.event.run_attempt_id ?? ""}/${harnessRunId}`;
}
export function updateStep(
  execution: Execution,
  id: string,
  update: Partial<ExecutionStep>,
) {
  return {
    ...execution,
    steps: execution.steps.map((step) =>
      step.id === id
        ? {
            ...step,
            ...update,
            detail:
              isObject(step.detail) && isObject(update.detail)
                ? { ...step.detail, ...update.detail }
                : (update.detail ?? step.detail),
          }
        : step,
    ),
  };
}
export function addStep(execution: Execution, step: ExecutionStep) {
  return execution.steps.some((existing) => existing.id === step.id)
    ? execution
    : { ...execution, steps: [...execution.steps, step] };
}
