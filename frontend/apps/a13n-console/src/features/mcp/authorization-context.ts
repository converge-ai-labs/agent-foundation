import type { Schema } from "../../shared/api";
import { authorizationHref } from "../../shared/authorization-link";

const storageKey = "a13n.mcp-authorization";
type AuthorizationContext = {
  state: string;
  connectionId: string;
  workspaceId: string;
  returnPath: string;
  expiresAt: string;
};
export function saveMCPAuthorization(
  launch: Schema["MCPAuthorizationLaunch"],
  connection: Schema["MCPConnection"],
  basePath: string,
): string {
  const href = authorizationHref(launch.authorization_url);
  if (!href) throw new Error("Invalid authorization URL.");
  const state = new URL(href).searchParams.get("state");
  if (!state) throw new Error("Authorization state is missing.");
  const context: AuthorizationContext = {
    state,
    connectionId: connection.id,
    workspaceId: connection.workspace_id,
    returnPath: `${basePath}/connections`,
    expiresAt: launch.expires_at,
  };
  sessionStorage.setItem(storageKey, JSON.stringify(context));
  return href;
}
export function readMCPAuthorization(): AuthorizationContext | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
    if (
      !value ||
      typeof value.state !== "string" ||
      value.state.length < 32 ||
      typeof value.connectionId !== "string" ||
      typeof value.workspaceId !== "string" ||
      typeof value.returnPath !== "string" ||
      !/^\/workspace\/[a-z0-9-]+\/connections$/.test(value.returnPath) ||
      typeof value.expiresAt !== "string" ||
      !(Date.parse(value.expiresAt) > Date.now())
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
export function clearMCPAuthorization() {
  sessionStorage.removeItem(storageKey);
}
/** Remove OAuth material before any authentication request, telemetry, or rendering. */
export function takeMCPCallback(): Schema["CompleteMCPOAuthRequest"] | null {
  if (window.location.pathname !== "/mcp-setup/callback") return null;
  const params = new URLSearchParams(window.location.hash.slice(1));
  window.history.replaceState(null, "", window.location.pathname);
  const receipt = params.getAll("receipt"),
    state = params.getAll("state");
  if (
    receipt.length !== 1 ||
    receipt[0].length < 32 ||
    receipt[0].length > 512 ||
    state.length !== 1 ||
    state[0].length < 32 ||
    state[0].length > 512
  )
    return null;
  return { receipt: receipt[0], state: state[0] };
}
