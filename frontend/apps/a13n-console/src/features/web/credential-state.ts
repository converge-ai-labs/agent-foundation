import { credentialMode } from "../../shared/provider-authentication";
import type { CredentialState } from "../providers/credentials-pill";

export function webCredentialState(
  definition: Parameters<typeof credentialMode>[0],
  provider: { config: Record<string, unknown>; credential_configured: boolean },
): CredentialState {
  if (provider.credential_configured) return "configured";
  return credentialMode(definition, provider.config) === "required"
    ? "not_configured"
    : "not_required";
}
