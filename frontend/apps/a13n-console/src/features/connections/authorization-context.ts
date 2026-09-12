import type { Client } from "@converge.ai/a13n";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { authorizationHref } from "../../shared/authorization-link";

const storageKey = "a13n.connection-authorization";
type AuthorizationContext = {
  state: string;
  verifier: string;
  authorizationId: string;
  connectionId: string;
  workspaceId: string;
  returnPath: string;
  expiresAt: string;
};
const random = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
export async function startBrowserAuthorization(
  client: Client,
  connection: Schema["Connection"],
  basePath: string,
  options: Schema["CreateAuthorizationRequest"]["options"] = {},
) {
  const verifier = random(),
    state = random();
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  const challenge = Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  const authorization = data(
    await client.http.POST(
      "/api/v1/connections/{connection_id}/authorizations",
      {
        params: {
          path: { connection_id: connection.id },
          header: commandHeaders(connection.workspace_id, crypto.randomUUID()),
        },
        body: {
          expected_version: connection.version,
          method: "browser",
          options,
          return_url: `${window.location.origin}/connections/callback`,
          state,
          completion_challenge: challenge,
        },
      },
    ),
  );
  requireAuthorizationProgress(authorization);
  const href = authorizationHref(authorization.next_action?.url);
  if (!href) throw new Error("Invalid authorization URL.");
  const context: AuthorizationContext = {
    state,
    verifier,
    authorizationId: authorization.id,
    connectionId: connection.id,
    workspaceId: connection.workspace_id,
    returnPath: `${basePath}/connections`,
    expiresAt: authorization.expires_at,
  };
  sessionStorage.setItem(storageKey, JSON.stringify(context));
  window.location.assign(href);
  return authorization;
}
export function readAuthorization(): AuthorizationContext | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
    if (
      !value ||
      typeof value.state !== "string" ||
      !/^[a-f0-9]{64}$/.test(value.state) ||
      typeof value.verifier !== "string" ||
      !/^[a-f0-9]{64}$/.test(value.verifier) ||
      typeof value.authorizationId !== "string" ||
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
export function clearAuthorization() {
  sessionStorage.removeItem(storageKey);
}
/** Strip callback material before authentication requests or rendering. */
export function takeCallback(): {
  receipt: string;
  state: string;
  authorizationId: string;
} | null {
  if (window.location.pathname !== "/connections/callback") return null;
  const params = new URLSearchParams(window.location.search);
  window.history.replaceState(null, "", window.location.pathname);
  const receipt = params.getAll("receipt"),
    state = params.getAll("state"),
    ids = params.getAll("authorization_id");
  if (
    ids.length !== 1 ||
    !ids[0] ||
    receipt.length !== 1 ||
    receipt[0].length < 32 ||
    receipt[0].length > 512 ||
    state.length !== 1 ||
    state[0].length < 32 ||
    state[0].length > 512
  )
    return null;
  return { receipt: receipt[0], state: state[0], authorizationId: ids[0] };
}

export function requireAuthorizationProgress(
  authorization: Schema["Authorization"],
) {
  if (
    authorization.error_code ||
    ["failed", "expired", "cancelled"].includes(authorization.status)
  ) {
    throw new Error(
      `${authorization.error_code ?? authorization.status} (${authorization.id})${authorization.outcome_unknown ? ": Authorization outcome is unknown. Check its status before starting again." : ""}`,
    );
  }
  return authorization;
}
export function requireCompletedAuthorization(
  authorization: Schema["Authorization"],
) {
  requireAuthorizationProgress(authorization);
  if (authorization.status !== "completed")
    throw new Error(
      `Authorization is ${authorization.status} (${authorization.id}).`,
    );
  return authorization;
}
