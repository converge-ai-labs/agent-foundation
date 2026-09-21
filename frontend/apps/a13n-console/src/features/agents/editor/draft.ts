import { useState } from "react";
import { jsonObject, validateSettings } from "../../../shared/forms";
import {
  advancedConfig,
  buildConfig,
  type AgentConfig,
} from "../configuration";

export function thinkingSelection(value: unknown): string {
  if (value === false) return "false";
  if (typeof value === "string") return value;
  return "true";
}

export type AgentDraft = ReturnType<typeof useAgentDraft>;

/**
 * Editable state for one agent configuration. Edits accumulate here until the
 * save bar publishes them; nothing is written while the draft changes.
 */
export function useAgentDraft(initial: AgentConfig) {
  const initialSettings = initial.model.settings ?? {};
  const {
    thinking: initialThinking,
    max_tokens: initialMaxTokens,
    ...initialExtraSettings
  } = initialSettings;
  const initialMaxTokensText =
    typeof initialMaxTokens === "number" ? String(initialMaxTokens) : "";
  const initialSettingsText = JSON.stringify(initialExtraSettings, null, 2);
  const [instructions, setInstructions] = useState(initial.instructions ?? ""),
    [model, setModel] = useState(initial.model.model_key),
    [mediaUnderstanding, setMediaUnderstanding] = useState(
      initial.media_understanding ?? {},
    ),
    [thinking, setThinking] = useState(thinkingSelection(initialThinking)),
    [maxTokens, setMaxTokens] = useState(initialMaxTokensText),
    [settings, setSettings] = useState(initialSettingsText),
    [advanced, setAdvanced] = useState(advancedConfig(initial)),
    [environmentTemplateId, setEnvironmentTemplateId] = useState(
      initial.default_environment_template_id ?? null,
    ),
    [toolsets, setToolsets] = useState(initial.toolsets ?? {}),
    [memory, setMemory] = useState(initial.memory),
    [skills, setSkills] = useState(initial.skills ?? []),
    [connections, setConnections] = useState(initial.connection_tools ?? []);
  const dirty =
    (["image", "video", "audio"] as const).some(
      (kind) =>
        (mediaUnderstanding[kind] ?? null) !==
        (initial.media_understanding?.[kind] ?? null),
    ) ||
    JSON.stringify(memory) !== JSON.stringify(initial.memory) ||
    environmentTemplateId !==
      (initial.default_environment_template_id ?? null) ||
    JSON.stringify(toolsets) !== JSON.stringify(initial.toolsets ?? {}) ||
    instructions !== (initial.instructions ?? "") ||
    model !== initial.model.model_key ||
    thinking !== thinkingSelection(initialThinking) ||
    maxTokens !== initialMaxTokensText ||
    settings !== initialSettingsText ||
    advanced !== advancedConfig(initial) ||
    JSON.stringify(skills) !== JSON.stringify(initial.skills ?? []) ||
    JSON.stringify(connections) !==
      JSON.stringify(initial.connection_tools ?? []);
  return {
    initial,
    initialExtraSettings,
    instructions,
    setInstructions,
    model,
    setModel,
    mediaUnderstanding,
    setMediaUnderstanding,
    thinking,
    setThinking,
    maxTokens,
    setMaxTokens,
    settings,
    setSettings,
    advanced,
    setAdvanced,
    environmentTemplateId,
    setEnvironmentTemplateId,
    toolsets,
    setToolsets,
    memory,
    setMemory,
    skills,
    setSkills,
    connections,
    setConnections,
    dirty,
  };
}

export type DraftBuild =
  | { ok: true; config: AgentConfig }
  | { ok: false; stage: "model" | "advanced"; error: Error };

/** Validates the draft and produces the configuration a save would publish. */
export function buildDraftConfig(
  draft: AgentDraft,
  settingsSchema: Record<string, unknown> | undefined,
  t: (value: string) => string,
): DraftBuild {
  const { initial } = draft;
  let modelSettings: ReturnType<typeof jsonObject>;
  try {
    const extraSettings = jsonObject(draft.settings);
    if ("thinking" in extraSettings || "max_tokens" in extraSettings)
      throw new Error(
        t("Edit thinking and max output tokens using their fields above."),
      );
    modelSettings = {
      ...extraSettings,
      thinking:
        draft.thinking === "true"
          ? true
          : draft.thinking === "false"
            ? false
            : draft.thinking,
      ...(draft.maxTokens ? { max_tokens: Number(draft.maxTokens) } : {}),
    };
    if (settingsSchema) validateSettings(settingsSchema, modelSettings);
  } catch (error) {
    return {
      ok: false,
      stage: "model",
      error:
        error instanceof Error ? error : new Error(t("Invalid configuration")),
    };
  }
  try {
    return {
      ok: true,
      config: buildConfig(
        initial,
        {
          media_understanding: draft.mediaUnderstanding,
          instructions: draft.instructions,
          memory: draft.memory,
          toolsets: draft.toolsets,
          reviewer: initial.reviewer,
          model: {
            ...initial.model,
            model_key: draft.model,
            settings: modelSettings,
          },
          skills: draft.skills,
          connection_tools: draft.connections,
          default_environment_template_id: draft.environmentTemplateId,
        },
        draft.advanced,
      ),
    };
  } catch (error) {
    return {
      ok: false,
      stage: "advanced",
      error:
        error instanceof Error ? error : new Error(t("Invalid configuration")),
    };
  }
}
