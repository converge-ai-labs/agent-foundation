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
  skills: ["skills", "builtin_skills"],
  connections: ["connection_tools"],
  plugins: ["plugins"],
  toolSearch: ["tool_search"],
  memory: ["memory_mounts", "memory"],
  toolsets: ["toolsets"],
  reviewer: ["reviewer"],
  environment: ["default_environment_template_id"],
  execution: ["execution"],
} as const;
export type EditorSection = keyof typeof sectionFields | "advanced";
const commonFields = new Set<string>([...Object.values(sectionFields).flat()]);
export function initialConfig(): AgentConfig {
  return { model: "", instructions: "" };
}
/** Configuration not represented by a dedicated editor section. */
export function advancedConfig(config: AgentConfig) {
  return JSON.stringify(
    Object.fromEntries(
      Object.entries(config).filter(([key]) => !commonFields.has(key)),
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
    | "builtin_skills"
    | "connection_tools"
    | "plugins"
    | "tool_search"
    | "memory_mounts"
    | "memory"
    | "toolsets"
    | "reviewer"
    | "default_environment_template_id"
    | "execution"
  >,
  advanced: string,
): AgentConfig {
  const extra = jsonObject(advanced);
  for (const key of Object.keys(extra))
    if (commonFields.has(key))
      throw new Error(`Edit ${key} through its dedicated field.`);
  const value = {
    ...extra,
    ...common,
    plugins: common.plugins ?? original.plugins,
  };
  if (!validateAgentConfig(value)) throw new Error(schemaErrors());
  return value;
}
