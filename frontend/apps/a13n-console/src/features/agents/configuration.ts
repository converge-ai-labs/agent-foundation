import type { Schema } from "../../shared/api";
import {
  jsonObject,
  schemaErrors,
  validateAgentConfig,
} from "../../shared/forms/validation";

export type AgentConfig = Schema["AgentConfig-Input"];
/** The fields each editor section shows; the advanced JSON holds the rest. */
const sectionFields = {
  model: ["model", "model_settings", "media_understanding"],
  instructions: ["instructions"],
  skills: ["skills"],
  connections: ["connection_tools"],
  toolSearch: ["tool_proxy"],
  memory: ["memory_mounts", "memory"],
  toolsets: ["toolsets"],
  reviewer: ["reviewer"],
  environment: ["default_environment_template_id"],
} as const;
export type EditorSection = keyof typeof sectionFields | "advanced";
/** Shown by no section and kept verbatim by a save. */
const retainedFields = ["plugins"];
const commonFields = new Set<string>([
  ...Object.values(sectionFields).flat(),
  ...retainedFields,
]);
export function initialConfig(): AgentConfig {
  return { model: "", instructions: "" };
}
/** The advanced JSON; a read-only view also lists the fields a save retains. */
export function advancedConfig(config: AgentConfig, readOnly = false) {
  return JSON.stringify(
    Object.fromEntries(
      Object.entries(config).filter(
        ([key]) =>
          !commonFields.has(key) || (readOnly && retainedFields.includes(key)),
      ),
    ),
    null,
    2,
  );
}
/** The editor sections that show any of these fields. */
export function sectionsOf(fields: Iterable<string>) {
  const sections = new Set<EditorSection>();
  for (const field of fields) {
    const section = (
      Object.keys(sectionFields) as (keyof typeof sectionFields)[]
    ).find((name) =>
      (sectionFields[name] as readonly string[]).includes(field),
    );
    sections.add(section ?? "advanced");
  }
  return sections;
}
export function buildConfig(
  original: AgentConfig,
  common: Pick<
    AgentConfig,
    | "model"
    | "model_settings"
    | "media_understanding"
    | "instructions"
    | "skills"
    | "connection_tools"
    | "tool_proxy"
    | "memory_mounts"
    | "memory"
    | "toolsets"
    | "reviewer"
    | "default_environment_template_id"
  >,
  advanced: string,
): AgentConfig {
  const extra = jsonObject(advanced);
  for (const key of Object.keys(extra))
    if (commonFields.has(key))
      throw new Error(`Edit ${key} through its dedicated field.`);
  // Plugins are retained verbatim; only the exposed advanced slice is replaced.
  const value = {
    ...extra,
    ...common,
    plugins: original.plugins,
  };
  if (!validateAgentConfig(value)) throw new Error(schemaErrors());
  return value;
}
