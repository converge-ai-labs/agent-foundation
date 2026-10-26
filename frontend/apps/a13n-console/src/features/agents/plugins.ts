import Ajv from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { ApiError, isRecord } from "../../service-client";
import type { Schema } from "../../shared/api";
import type { ToolSearchDraft } from "./tool-search";

export type Plugin = Schema["PluginSelection"];
export type PluginType = Schema["PluginType"];

export function suggestedInstanceName(key: string, plugins: Plugin[]) {
  const base = key.replace(/[^A-Za-z0-9_.:-]/g, "-").slice(0, 110);
  const prefix = /^[A-Za-z_]/.test(base) ? base : `plugin-${base}`;
  let name = prefix;
  for (
    let index = 2;
    plugins.some((item) => item.instance_name === name);
    index++
  )
    name = `${prefix}-${index}`;
  return name;
}

export function instanceNameError(
  name: string,
  plugins: Plugin[],
  original?: string,
) {
  if (!/^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$/.test(name))
    return "Use 1–128 letters, digits, underscores, dots, colons or hyphens, starting with a letter or underscore.";
  if (plugins.some((item) => item.instance_name === name && name !== original))
    return "This agent already has a plugin with this instance name.";
  return undefined;
}

/** Renaming or removing an instance updates only its own discovery memberships. */
export function replacePluginReferences(
  search: ToolSearchDraft,
  previous: string,
  next?: string,
): ToolSearchDraft {
  return {
    ...search,
    groups: search.groups.map((group) => ({
      ...group,
      plugins: group.plugins?.flatMap((name) =>
        name === previous ? (next ? [next] : []) : [name],
      ),
    })),
  };
}

/** Complex/recursive schemas stay in JSON, without letting a form drop unrepresented values. */
export function hasPluginForm(schema: PluginType["config_schema"]): boolean {
  if (
    !schema ||
    !isRecord(schema.properties) ||
    !Object.keys(schema.properties).length
  )
    return false;
  if (
    Object.keys(schema).some((key) =>
      ["oneOf", "anyOf", "allOf", "if", "$ref"].includes(key),
    )
  )
    return false;
  return Object.values(schema.properties).every(
    (field) =>
      isRecord(field) &&
      ["string", "number", "integer", "boolean"].includes(String(field.type)) &&
      !Object.keys(field).some((key) =>
        ["oneOf", "anyOf", "allOf", "$ref", "x-visible-when"].includes(key),
      ) &&
      (!Array.isArray(field.enum) ||
        (field.type === "string" &&
          field.enum.every((value) => typeof value === "string"))),
  );
}

export function pluginValidationIndex(error: unknown): number | undefined {
  if (!(error instanceof ApiError) || error.code !== "invalid_argument")
    return undefined;
  const field = error.details.field;
  const match =
    typeof field === "string" && /^plugins\.(\d+)(?:\.|$)/.exec(field);
  return match ? Number(match[1]) : undefined;
}

/** Packages may reuse schema IDs; never register their schemas in the Console's shared validator. */
export function validatePluginSettings(
  schema: Record<string, unknown>,
  value: Record<string, unknown>,
) {
  const validator = new Ajv({ strict: false, allErrors: true });
  addFormats(validator);
  const validate = validator.compile(schema);
  if (!validate(value))
    throw new Error(
      validator.errorsText(validate.errors, {
        separator: "\n",
        dataVar: "config",
      }),
    );
}
