import type { Schema } from "../../shared/api";

export function requiresProviderCredential(
  type: string,
  configuration: Record<string, unknown>,
  definition?: Schema["ModelProviderDefinition"],
) {
  return (
    definition?.credential_schema.type !== "null" &&
    (type !== "openai" || configuration.auth_mode !== "none")
  );
}
