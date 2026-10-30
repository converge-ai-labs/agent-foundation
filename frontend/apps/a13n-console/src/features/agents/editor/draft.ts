import { useState } from "react";
import { replacePluginReferences, type Plugin } from "../plugins";
import { toolSearchConfig, toolSearchDraft } from "../tool-search";
import { validSpaceSelection } from "../../memories/space-form";
import { isRecord } from "../../../service-client";
import type { Schema } from "../../../shared/api";
import {
  executionOverride,
  jsonObject,
  validateSettings,
} from "../../../shared/forms";
import {
  mediaKinds,
  mediaSelected,
} from "../../models/media-understanding-fields";
import { requiresReviewer, validateReviewer } from "../reviewer";
import {
  advancedConfig,
  buildConfig,
  type AgentConfig,
} from "../configuration";

export function thinkingSelection(value: unknown): string {
  if (value === false) return "false";
  if (typeof value === "string") return value;
  return value === true ? "true" : "default";
}

/** The effort levels a calling API's settings schema allows for `thinking`. */
export function thinkingEfforts(
  settingsSchema: Record<string, Schema["JsonValue"]> | undefined,
): string[] {
  const properties = settingsSchema?.properties;
  const thinking = isRecord(properties) ? properties.thinking : undefined;
  const variants =
    isRecord(thinking) && Array.isArray(thinking.anyOf) ? thinking.anyOf : [];
  return variants
    .flatMap((variant: unknown) =>
      isRecord(variant) && Array.isArray(variant.enum) ? variant.enum : [],
    )
    .filter((value: unknown): value is string => typeof value === "string");
}

export type AgentDraft = ReturnType<typeof useAgentDraft>;

/**
 * Editable state for one agent configuration. Edits accumulate here until the
 * save bar publishes them; nothing is written while the draft changes.
 */
export function useAgentDraft(
  initial: AgentConfig,
  catalog?: readonly Schema["ToolsetDefinition"][],
) {
  const initialSettings = initial.model_settings ?? {};
  const {
    thinking: initialThinking,
    max_tokens: initialMaxTokens,
    ...initialExtraSettings
  } = initialSettings;
  const initialMaxTokensText =
    typeof initialMaxTokens === "number" ? String(initialMaxTokens) : "";
  const initialSettingsText = JSON.stringify(initialExtraSettings, null, 2);
  const [instructions, setInstructions] = useState(initial.instructions ?? ""),
    [model, setModel] = useState(initial.model),
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
    [reviewer, setReviewer] = useState(initial.reviewer),
    [skills, setSkills] = useState(initial.skills ?? []),
    [connections, setConnections] = useState(initial.connection_tools ?? []),
    [plugins, setPlugins] = useState(initial.plugins ?? []),
    [toolSearch, setToolSearch] = useState(
      toolSearchDraft(initial.tool_search),
    ),
    [memoryMounts, setMemoryMounts] = useState(initial.memory_mounts ?? []),
    [memory, setMemory] = useState(initial.memory ?? {}),
    [summarize, setSummarize] = useState(initial.summarize ?? false),
    [backgroundCompaction, setBackgroundCompaction] = useState(
      initial.background_compaction ?? {},
    ),
    [execution, setExecution] = useState(initial.execution ?? {});
  // Read-only snapshots and unavailable catalogs retain the saved selection.
  const builtinSkills = catalog
    ? linkedBuiltinSkills(initial.builtin_skills ?? [], toolsets, catalog)
    : (initial.builtin_skills ?? []);
  const dirty =
    summarize !== (initial.summarize ?? false) ||
    JSON.stringify(backgroundCompaction) !==
      JSON.stringify(initial.background_compaction ?? {}) ||
    JSON.stringify(plugins) !== JSON.stringify(initial.plugins ?? []) ||
    JSON.stringify(execution) !== JSON.stringify(initial.execution ?? {}) ||
    JSON.stringify(toolSearch) !==
      JSON.stringify(toolSearchDraft(initial.tool_search)) ||
    JSON.stringify(reviewer) !== JSON.stringify(initial.reviewer) ||
    mediaKinds.some(
      ({ kind }) =>
        (mediaUnderstanding[kind] ?? null) !==
        (initial.media_understanding?.[kind] ?? null),
    ) ||
    environmentTemplateId !==
      (initial.default_environment_template_id ?? null) ||
    JSON.stringify(toolsets) !== JSON.stringify(initial.toolsets ?? {}) ||
    instructions !== (initial.instructions ?? "") ||
    model !== initial.model ||
    thinking !== thinkingSelection(initialThinking) ||
    maxTokens !== initialMaxTokensText ||
    settings !== initialSettingsText ||
    advanced !== advancedConfig(initial) ||
    JSON.stringify(skills) !== JSON.stringify(initial.skills ?? []) ||
    JSON.stringify([...builtinSkills].sort()) !==
      JSON.stringify([...(initial.builtin_skills ?? [])].sort()) ||
    JSON.stringify(connections) !==
      JSON.stringify(initial.connection_tools ?? []) ||
    JSON.stringify(memory) !== JSON.stringify(initial.memory ?? {}) ||
    JSON.stringify(memoryMounts) !==
      JSON.stringify(initial.memory_mounts ?? []);
  return {
    initial,
    summarize,
    setSummarize,
    backgroundCompaction,
    setBackgroundCompaction,
    execution,
    setExecution,
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
    reviewer,
    setReviewer,
    skills,
    setSkills,
    builtinSkills,
    connections,
    setConnections,
    plugins,
    applyPlugin: (plugin: Plugin, previous?: string) => {
      setPlugins((current) =>
        previous
          ? current.map((item) =>
              item.instance_name === previous ? plugin : item,
            )
          : [...current, plugin],
      );
      if (previous && previous !== plugin.instance_name)
        setToolSearch((current) =>
          replacePluginReferences(current, previous, plugin.instance_name),
        );
    },
    removePlugin: (name: string) => {
      setPlugins((current) =>
        current.filter((item) => item.instance_name !== name),
      );
      setToolSearch((current) => replacePluginReferences(current, name));
    },
    toolSearch,
    setToolSearch,
    memoryMounts,
    setMemoryMounts,
    memory,
    setMemory,
    dirty,
  };
}

/** Platform instructions follow enabled tools; unrelated saved skill names survive. */
function linkedBuiltinSkills(
  saved: NonNullable<AgentConfig["builtin_skills"]>,
  selections: NonNullable<AgentConfig["toolsets"]>,
  catalog: readonly Schema["ToolsetDefinition"][],
): NonNullable<AgentConfig["builtin_skills"]> {
  const enabled = catalog.some(
    (group) =>
      group.platform &&
      (selections[group.key]?.enabled ?? group.default_enabled) &&
      group.tools.some(
        (tool) =>
          selections[group.key]?.tools?.[tool.key]?.enabled ??
          tool.default_enabled,
      ),
  );
  const others = saved.filter((name) => name !== "platform-operations");
  return enabled ? [...others, "platform-operations"] : others;
}

export type DraftBuild =
  | { ok: true; config: AgentConfig }
  | {
      ok: false;
      stage: "model" | "reviewer" | "toolSearch" | "compaction" | "advanced";
      error: Error;
    };

/** Validates the draft and produces the configuration a save would publish. */
export function buildDraftConfig(
  draft: AgentDraft,
  settingsSchema: Record<string, Schema["JsonValue"]> | undefined,
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
      ...(draft.thinking === "default"
        ? {}
        : {
            thinking:
              draft.thinking === "true"
                ? true
                : draft.thinking === "false"
                  ? false
                  : draft.thinking,
          }),
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
    if (
      requiresReviewer(draft.toolsets, draft.connections) &&
      !draft.reviewer?.model
    )
      throw new Error(
        t("Choose a review model before saving automatic review permissions."),
      );
    if (draft.reviewer) validateReviewer(draft.reviewer);
  } catch (error) {
    return {
      ok: false,
      stage: "reviewer",
      error:
        error instanceof Error ? error : new Error(t("Invalid configuration")),
    };
  }
  let proxy: AgentConfig["tool_search"];
  try {
    proxy = toolSearchConfig(draft.toolSearch, draft.connections);
  } catch (error) {
    return {
      ok: false,
      stage: "toolSearch",
      error:
        error instanceof Error
          ? new Error(t(error.message))
          : new Error(t("Invalid configuration")),
    };
  }
  const threshold = draft.backgroundCompaction.blocking_threshold;
  if (threshold !== undefined && !(threshold > 0 && threshold <= 1))
    return {
      ok: false,
      stage: "compaction",
      error: new Error(t("Enter a percentage greater than 0 and at most 100.")),
    };
  try {
    if (!validSpaceSelection(draft.memory))
      throw new Error(
        t(
          "Use unique mount names: lowercase letters, digits and hyphens, starting with a letter.",
        ),
      );
    return {
      ok: true,
      config: buildConfig(
        initial,
        {
          execution: executionOverride(draft.execution),
          background_compaction: draft.backgroundCompaction,
          summarize: draft.summarize,
          instructions: draft.instructions,
          toolsets: draft.toolsets,
          reviewer: draft.reviewer,
          model: draft.model,
          model_settings: modelSettings,
          skills: draft.skills,
          builtin_skills: draft.builtinSkills,
          // Absent, not empty, while every kind inherits: the saved configuration stays silent.
          ...(mediaSelected(draft.mediaUnderstanding).length
            ? { media_understanding: draft.mediaUnderstanding }
            : {}),
          connection_tools: draft.connections,
          plugins: draft.plugins,
          tool_search: proxy,
          memory_mounts: draft.memoryMounts,
          memory: draft.memory,
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
