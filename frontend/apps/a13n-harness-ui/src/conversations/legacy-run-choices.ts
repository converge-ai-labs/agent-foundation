import type { ThreadDraft } from "./draft";

/** Import the former per-browser choice once, only into pristine shared options. */
export function initializeLegacyRunChoices(
  threadId: string,
  draft: ThreadDraft,
  agent: string | undefined,
  inheritedModel: string | null | undefined,
) {
  if (!draft.synchronized) return;
  const key = `a13n-harness-ui.run-choices.${threadId}`;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return;
    // An acknowledged shared value (including an explicit reset) wins. Remove
    // the old record only after synchronization, so failed saves remain recoverable.
    if (!draft.optionsUninitialized) {
      localStorage.removeItem(key);
      return;
    }
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
      (thinking != null &&
        typeof thinking !== "boolean" &&
        !["minimal", "low", "medium", "high", "xhigh", "max"].includes(
          thinking,
        )) ||
      (fast != null && typeof fast !== "boolean" && fast !== "ultrafast") ||
      (reasoning_mode != null && !["standard", "pro"].includes(reasoning_mode))
    )
      return;
    if (
      saved.agent !== agent ||
      (saved.override ?? inheritedModel) !== saved.model
    )
      return;
    draft.modelOptions = {
      agent: saved.agent,
      effective_model: saved.model,
      model_id: saved.override,
      thinking,
      fast,
      reasoning_mode,
    };
  } catch {
    // Unavailable storage or malformed legacy records do not block the composer.
  }
}
