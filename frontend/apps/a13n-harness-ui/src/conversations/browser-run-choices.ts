import { useLayoutEffect, useMemo } from "react";
import type { ThreadDraft } from "./draft";
import type { ModelControlValues } from "./model-controls";

type Choices = {
  agent: string;
  model: string;
  override?: string;
  controls: ModelControlValues;
};

function readChoices(key: string): Choices | undefined {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (
      !saved ||
      typeof saved.agent !== "string" ||
      typeof saved.model !== "string" ||
      (saved.override !== undefined && saved.override !== saved.model) ||
      typeof saved.controls !== "object" ||
      !saved.controls ||
      Array.isArray(saved.controls)
    )
      return;
    const { thinking, fast, reasoning_mode } = saved.controls;
    if (
      thinking != null &&
      typeof thinking !== "boolean" &&
      !["minimal", "low", "medium", "high", "xhigh", "max"].includes(thinking)
    )
      return;
    if (fast != null && typeof fast !== "boolean" && fast !== "ultrafast")
      return;
    if (reasoning_mode != null && !["standard", "pro"].includes(reasoning_mode))
      return;
    return {
      agent: saved.agent,
      model: saved.model,
      override: saved.override,
      controls: { thinking, fast, reasoning_mode },
    };
  } catch {
    return undefined;
  }
}

function storageKey(threadId: string) {
  return `a13n-harness-ui.run-choices.${threadId}`;
}

function writeChoices(key: string, value?: Choices) {
  try {
    if (value) localStorage.setItem(key, JSON.stringify(value));
    else localStorage.removeItem(key);
  } catch {
    // The current page still owns its in-memory choices.
  }
}
export function forgetRunChoices(threadId: string) {
  writeChoices(storageKey(threadId));
}

export function useBrowserRunChoices(
  threadId: string,
  draft: ThreadDraft,
  agent: string | undefined,
  inheritedModel: string | null | undefined,
) {
  const key = storageKey(threadId);
  const retained = useMemo(() => ({ value: readChoices(key) }), [key]);
  useLayoutEffect(() => {
    const saved = retained.value;
    if (!agent || !saved) return;
    if (
      saved.agent === agent &&
      !saved.override &&
      inheritedModel === undefined
    )
      return;
    if (
      saved.agent === agent &&
      (saved.override ?? inheritedModel) === saved.model
    ) {
      draft.modelId = saved.override;
      draft.controls = saved.controls;
    } else {
      draft.modelId = undefined;
      draft.controls = {};
      retained.value = undefined;
      writeChoices(key);
    }
    draft.notify();
  }, [key, draft, retained, agent, inheritedModel]);

  function choose(override: string | undefined, controls: ModelControlValues) {
    const model = override ?? inheritedModel;
    draft.modelId = override;
    draft.controls = controls;
    retained.value =
      agent &&
      model &&
      (override || Object.values(controls).some((value) => value != null))
        ? { agent, model, override, controls }
        : undefined;
    writeChoices(key, retained.value);
    draft.notify();
  }
  return {
    pending:
      !!retained.value &&
      (!agent || (!retained.value.override && inheritedModel === undefined)),
    onControlsChange: (controls: ModelControlValues) =>
      choose(draft.modelId, controls),
    onModelChange: (next: string | undefined) =>
      choose(
        next,
        (next ?? inheritedModel) === (draft.modelId ?? inheritedModel)
          ? draft.controls
          : {},
      ),
  };
}
