import {
  isAlias,
  isPair,
  isScalar,
  parseDocument,
  stringify,
  visit,
} from "yaml";
import {
  schemaErrors,
  validateAgentConfig,
} from "../../shared/forms/validation";
import type { AgentConfig } from "./configuration";

export const MAX_AGENT_FILE_BYTES = 1024 * 1024;
/** The Agent file format version that spec/frontend/console.md owns. */
export const AGENT_FILE_VERSION = 2;

/** A saved Service configuration, without resource identity or resolved credentials. */
export interface AgentFile {
  schema_version: typeof AGENT_FILE_VERSION;
  name: string;
  description: string | null;
  config: AgentConfig;
}

export function agentFile(
  agent: Pick<AgentFile, "name" | "description">,
  config: AgentConfig,
): AgentFile {
  return {
    schema_version: AGENT_FILE_VERSION,
    name: agent.name,
    description: agent.description,
    config,
  };
}

export function serializeAgentFile(file: AgentFile): string {
  return stringify(file, { aliasDuplicateObjects: false, lineWidth: 0 });
}

export function parseAgentFile(source: string): AgentFile {
  if (new TextEncoder().encode(source).length > MAX_AGENT_FILE_BYTES)
    throw new Error("Choose an Agent YAML file no larger than 1 MB.");
  // YAML's core schema accepts unquoted strings; custom tags and aliases are not
  // part of this JSON-compatible interchange format.
  const core = parseDocument(source, { schema: "core", uniqueKeys: true });
  if (core.errors.length || core.warnings.length)
    throw new Error(core.errors[0]?.message ?? core.warnings[0]!.message);
  let nodes = 0;
  visit(core, (_key, node, path) => {
    if (path.length > 100)
      throw new Error("The Agent YAML file is too deeply nested.");
    if (++nodes > 50_000)
      throw new Error("The Agent YAML file is too complex.");
    if (isAlias(node))
      throw new Error("YAML aliases are not supported in Agent files.");
    if (
      isPair(node) &&
      (!isScalar(node.key) || typeof node.key.value !== "string")
    )
      throw new Error("Agent YAML mapping keys must be strings.");
    if (
      isScalar(node) &&
      typeof node.value === "number" &&
      !Number.isFinite(node.value)
    )
      throw new Error("Agent YAML numbers must be finite.");
  });
  const value: unknown = core.toJS({ maxAliasCount: 0 });
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Enter an Agent YAML object.");
  const fields = value as Record<string, unknown>;
  if (fields.schema_version !== AGENT_FILE_VERSION)
    throw new Error(
      "Unsupported Agent file version. Expected schema_version: 2.",
    );
  if (
    Object.keys(fields).some(
      (key) =>
        !["schema_version", "name", "description", "config"].includes(key),
    )
  )
    throw new Error(
      "Agent files contain only schema_version, name, description, and config.",
    );
  if (typeof fields.name !== "string" || !validAgentName(fields.name))
    throw new Error(
      "Enter an Agent name with 1–128 characters and no surrounding whitespace or control characters.",
    );
  if (
    fields.description !== undefined &&
    fields.description !== null &&
    (typeof fields.description !== "string" ||
      [...fields.description].length > 4096)
  )
    throw new Error(
      "The Agent description must contain at most 4096 characters.",
    );
  if (!validateAgentConfig(fields.config)) throw new Error(schemaErrors());
  return {
    schema_version: AGENT_FILE_VERSION,
    name: fields.name.normalize("NFC"),
    description: (fields.description as string | null | undefined) ?? null,
    config: fields.config,
  };
}

export function validAgentName(value: string): boolean {
  const normalized = value.normalize("NFC");
  return (
    normalized === normalized.trim() &&
    [...normalized].length >= 1 &&
    [...normalized].length <= 128 &&
    !/[\p{Cc}\p{Cs}]/u.test(normalized)
  );
}
