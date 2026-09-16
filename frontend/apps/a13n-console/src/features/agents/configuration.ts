import type { Schema } from "../../shared/api";
import {
  jsonObject,
  schemaErrors,
  validateAgentConfig,
} from "../../shared/validation";
import type { AgentSearchValue } from "../web/selection";

export type AgentConfig = Schema["AgentConfig-Input"];
const commonFields = new Set([
  "toolsets",
  "memory",
  "model",
  "instructions",
  "skills",
  "connection_tools",
  "plugins",
  "secret_requirements",
]);
export function initialConfig(name: string): AgentConfig {
  return {
    model: { model_key: "" },
    instructions: "",
    input_adapter: { adapter_key: "native" },
    protocol: { public_name: name },
  };
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
    | "instructions"
    | "skills"
    | "connection_tools"
    | "toolsets"
    | "memory"
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

export function searchSelection(config: AgentConfig): AgentSearchValue | null {
  const web = config.toolsets?.web;
  const search = web?.tools?.search;
  return web?.enabled && search?.enabled
    ? (search.config as AgentSearchValue)
    : null;
}

export function withSearchSelection(
  toolsets: AgentConfig["toolsets"],
  search: AgentSearchValue | null,
): AgentConfig["toolsets"] {
  const current = toolsets?.web;
  const tools = {
    ...current?.tools,
    search: {
      enabled: search !== null,
      permission: current?.tools?.search?.permission ?? "inherit",
      config: search ?? current?.tools?.search?.config ?? {},
    },
  };
  return {
    ...toolsets,
    web: {
      enabled:
        search !== null ||
        Object.entries(tools).some(
          ([key, value]) => key !== "search" && value.enabled,
        ),
      config: current?.config ?? {},
      tools,
    },
  };
}
