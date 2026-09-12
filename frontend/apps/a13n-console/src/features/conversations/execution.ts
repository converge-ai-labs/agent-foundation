import type { RunEvent } from "@converge.ai/a13n";
import { applyRunEvent, isObject, type PresentedItem } from "./projection";

export interface ExecutionStep {
  id: string;
  scope: string;
  state: "running" | "completed" | "failed";
  items: string[];
}
export interface Execution {
  steps: ExecutionStep[];
  items: Map<string, PresentedItem>;
}
export function applyExecution(current: Execution, entry: RunEvent): Execution {
  const { event } = entry;
  const items = event.item_id
    ? applyRunEvent(current.items, entry)
    : current.items;
  let steps = current.steps;
  const scope = `${event.run_attempt_id ?? ""}/${event.harness_run_id ?? ""}`;
  const value = event.payload.value;
  if (
    event.event_type === "agui.custom" &&
    event.payload.name === "a13n.harness.lifecycle" &&
    isObject(value) &&
    isObject(value.event) &&
    isObject(value.event.payload)
  ) {
    const payload = value.event.payload;
    if (typeof payload.request_id === "string") {
      const id = `${scope}/${payload.request_id}`;
      const step = steps.find((step) => step.id === id);
      if (payload.type === "model_request_started" && !step)
        steps = [...steps, { id, scope, state: "running", items: [] }];
      else if (step && payload.type === "model_request_completed")
        steps = steps.map((entry) =>
          entry === step ? { ...step, state: "completed" } : entry,
        );
      else if (step && payload.type === "model_request_failed")
        steps = steps.map((entry) =>
          entry === step ? { ...step, state: "failed" } : entry,
        );
    }
  }
  // Item-producing Service events carry their kind and role from their first observation.
  if (event.item_id && !current.items.has(event.item_id)) {
    const item = items.get(event.item_id);
    const step = steps.findLast((step) => step.scope === scope);
    if (
      step &&
      item &&
      item.display !== false &&
      (item.kind === "tool_call" ||
        item.kind === "reasoning_message" ||
        (item.kind === "text_message" && item.role === "assistant"))
    )
      steps = steps.map((entry) =>
        entry === step ? { ...step, items: [...step.items, item.id] } : entry,
      );
  }
  return { steps, items };
}
