import type { Schema } from "./api";
import { withSchemaValues } from "./schema-fields";

export function credentialMode(
  definition:
    | {
        authentication: Schema["Authentication"];
        configuration_schema: Record<string, unknown>;
      }
    | undefined,
  configuration: Record<string, unknown>,
): Schema["CredentialMode"] {
  if (!definition) return "forbidden";
  const values = withSchemaValues(
    definition.configuration_schema,
    configuration,
  );
  const matches = new Set(
    (definition.authentication.cases ?? [])
      .filter((item) => values[item.field] === item.equals)
      .map((item) => item.mode),
  );
  if (matches.size > 1)
    throw new Error("Conflicting provider authentication conditions.");
  return [...matches][0] ?? definition.authentication.mode ?? "required";
}

/** A Provider that takes no credential reports a null credential schema. */
export function providerSchema(
  value: { [key: string]: unknown } | null | undefined,
): Record<string, unknown> {
  return value ?? {};
}
