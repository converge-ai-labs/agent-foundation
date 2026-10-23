import { components } from "../../../../../../proto/a13n-service/openapi.json";
import addFormats from "ajv-formats";
import Ajv from "ajv/dist/2020";
import type { Schema } from "../api";

const ajv = new Ajv({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addFormat("password", true);
ajv.addFormat("binary", true);
ajv.addSchema({ components }, "service");
export const validateAgentConfig = ajv.compile<Schema["AgentConfig-Input"]>({
  $ref: "service#/components/schemas/AgentConfig-Input",
});
export const validateRunOverride = ajv.compile<Schema["AgentOverride-Input"]>({
  $ref: "service#/components/schemas/AgentOverride-Input",
});
export function runOverride(value: unknown): Schema["AgentOverride-Input"] {
  if (!validateRunOverride(value))
    throw new Error(
      ajv.errorsText(validateRunOverride.errors, {
        separator: "\n",
        dataVar: "configuration",
      }),
    );
  return value;
}
export const validateJsonObject = ajv.compile<
  Record<string, Schema["JsonValue"]>
>({
  type: "object",
  additionalProperties: { $ref: "service#/components/schemas/JsonValue" },
});
export const validateJson = ajv.compile<Schema["JsonValue"]>({
  $ref: "service#/components/schemas/JsonValue",
});

const validateScheduleInvocation = ajv.compile<
  Schema["ScheduleInvocation-Input"]
>({ $ref: "service#/components/schemas/ScheduleInvocation-Input" });
export function scheduleInvocation(
  text: string,
): Schema["ScheduleInvocation-Input"] {
  const value: unknown = JSON.parse(text);
  if (!validateScheduleInvocation(value))
    throw new Error(
      ajv.errorsText(validateScheduleInvocation.errors, {
        separator: "\n",
        dataVar: "invocation",
      }),
    );
  return value;
}

export function jsonObject(text: string): Record<string, Schema["JsonValue"]> {
  const value: unknown = JSON.parse(text);
  if (!validateJsonObject(value)) throw new Error("Enter a JSON object.");
  return value;
}
export function jsonValue(text: string): Schema["JsonValue"] {
  const value: unknown = JSON.parse(text);
  if (!validateJson(value)) throw new Error("Enter a valid JSON value.");
  return value;
}
export function schemaErrors() {
  return ajv.errorsText(validateAgentConfig.errors, {
    separator: "\n",
    dataVar: "config",
  });
}
export function validateSettings(
  schema: Record<string, unknown>,
  value: Record<string, unknown>,
): void {
  const validate = ajv.compile(schema);
  if (!validate(value))
    throw new Error(
      ajv.errorsText(validate.errors, { separator: "\n", dataVar: "settings" }),
    );
}

export function stringValues(
  value: Record<string, unknown>,
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item !== "string")
      throw new Error("Credential values must be strings.");
    result[key] = item;
  }
  return result;
}
