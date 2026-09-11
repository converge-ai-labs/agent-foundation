import type { Schema } from "../../shared/api";

export function isOpenAICompatibleProvider(type: string) {
  return type === "openai_compatible" || type === "openai_responses_compatible";
}

export function requiresProviderCredential(
  type: string,
  configuration: Record<string, unknown>,
  definition?: Schema["ModelProviderDefinition"],
) {
  return (
    definition?.credential_schema.type !== "null" &&
    (!isOpenAICompatibleProvider(type) || configuration.auth_mode !== "none")
  );
}
