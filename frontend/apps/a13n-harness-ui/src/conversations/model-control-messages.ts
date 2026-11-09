import type { TFunction } from "i18next";

// Only call for control descriptors, never model/agent names or arbitrary errors.
// These descriptors have no message codes. Keep the allowlist aligned with
// model_thinking.py, model_fast.py and model_reasoning_mode.py; unknown text
// remains verbatim. Capability and selection values still belong to the server.
export const modelControlMessages = new Set([
  "Minimal",
  "Low",
  "Medium",
  "High",
  "Xhigh",
  "Max",
  "On",
  "Off",
  "Default",
  "Model default",
  "Provider default",
  "Custom",
  "Custom configuration",
  "Custom (extra_body)",
  "Custom budget",
  "Dynamic budget",
  "On (requested)",
  "Off (requested)",
  "Standard",
  "Fast",
  "Ultrafast",
  "Pro",
  "Unavailable",
  "Unavailable selection",
  "Reasoning effort",
  "Disable thinking",
  "Enable thinking",
  "Adaptive thinking effort",
  "Thinking level",
  "Thinking level; not disabled",
  "No thinking before responding; tool updates may still appear",
  "Thinking is controlled by extra_body. Edit the Model configuration before using an override.",
  "Thinking budget must be below max_tokens; adjust the Model output limit first.",
  "Thinking controls are not supported for this model.",
  "Thinking controls are not known for this model/adapter. Model settings remain unchanged.",
  "Fast controls are not available for this model connection.",
  "Fast is controlled by extra_body or extra_headers. Edit the Model configuration first.",
  "Ultrafast requires GPT-6 Astra through a Codex subscription connection.",
  "Reasoning mode is not available for this model connection.",
  "Reasoning mode is controlled by extra_body. Edit the Model configuration first.",
]);

export function modelControlMessage(message: string, t: TFunction): string {
  if (modelControlMessages.has(message)) return t(message);
  // Exact server-authored formats; do not partially translate unknown text.
  const budget =
    /^(Thinking budget: |Budget - )(-?(?:0|[1-9]\d{0,2}(?:,\d{3})*)) tokens$/.exec(
      message,
    );
  if (budget) {
    const count = Number(budget[2].replaceAll(",", ""));
    if (Number.isSafeInteger(count))
      return budget[1] === "Thinking budget: "
        ? t("Thinking budget: {{count, number}} tokens", { count })
        : t("Budget - {{count, number}} tokens", { count });
  }
  const effort =
    /^(Adaptive|Effort) - (Minimal|Low|Medium|High|Xhigh|Max|Provider default|Custom)$/.exec(
      message,
    );
  if (effort)
    return effort[1] === "Adaptive"
      ? t("Adaptive - {{level}}", { level: t(effort[2]) })
      : t("Effort - {{level}}", { level: t(effort[2]) });
  return message;
}
