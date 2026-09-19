import { schemaProperties } from "./schemas";

/**
 * What a service asks you for, read from its credential schema: the tile hint
 * on the catalog, and the sentence under the connect title.
 */
export function credentialHint(schema?: Record<string, unknown> | null) {
  const keys = Object.keys(schemaProperties(schema));
  if (!keys.length) return "No credentials";
  if (keys.length > 1) return "Credentials";
  if (keys[0] === "api_key") return "API key";
  if (keys[0] === "token") return "Token";
  return "Credentials";
}

/** Interpolates `provider`; pass the definition's display name. */
export function credentialDescription(schema?: Record<string, unknown> | null) {
  const keys = Object.keys(schemaProperties(schema));
  if (!keys.length) return "This service needs no credentials.";
  if (keys.length === 1)
    return keys[0] === "token"
      ? "Paste the access token for {{provider}}."
      : "Paste the API key from your {{provider}} account.";
  if (keys.includes("token_id") && keys.includes("token_secret"))
    return "Enter the token ID and secret from your {{provider}} account.";
  return "Enter the credentials from your {{provider}} account.";
}
