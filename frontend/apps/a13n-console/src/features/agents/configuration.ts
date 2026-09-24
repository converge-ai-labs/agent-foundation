import type { Schema } from "../../shared/api";
import {
  jsonObject,
  schemaErrors,
  validateAgentConfig,
} from "../../shared/forms/validation";

export type AgentConfig = Schema["AgentConfig-Input"];
const commonFields = new Set([
  "toolsets",
  "model",
  "media_understanding",
  "instructions",
  "skills",
  "connection_tools",
  "memory_mounts",
  "reviewer",
  "default_environment_template_id",
  "plugins",
  "secret_requirements",
]);
export function initialConfig(): AgentConfig {
  return { model: { model_id: "" }, instructions: "" };
}
export function advancedConfig(config: AgentConfig) {
  return JSON.stringify(
    Object.fromEntries(
      Object.entries(config).filter(([key]) => !commonFields.has(key)),
    ),
    null,
    2,
  );
}
export function buildConfig(
  original: AgentConfig,
  common: Pick<
    AgentConfig,
    | "model"
    | "media_understanding"
    | "instructions"
    | "skills"
    | "connection_tools"
    | "memory_mounts"
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
  // Hidden requirements are retained verbatim; only the exposed advanced slice is replaced.
  const value = {
    ...extra,
    ...common,
    plugins: original.plugins,
    secret_requirements: original.secret_requirements,
  };
  if (!validateAgentConfig(value)) throw new Error(schemaErrors());
  return value;
}
