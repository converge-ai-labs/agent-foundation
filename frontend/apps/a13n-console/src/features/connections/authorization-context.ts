import type { Client } from "../../service-client";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { authorizationHref } from "../../shared/authorization-link";

const storageKey = "a13n.connection-authorization";
const statePattern = /^[A-Za-z0-9_-]{32,512}$/;
type AuthorizationContext = {
  state: string;
  authorizationId: string;
  connectionId: string;
  workspaceId: string;
  returnPath: string;
  expiresAt: string;
} & (
  { type: "connector"; verifier: string } | { type: "mcp"; verifier?: never }
);
const random = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
export function supportsBrowserAuthorization(
  location: Pick<Location, "protocol" | "hostname">,
) {
  return (
    location.protocol === "https:" ||
    (location.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname))
  );
}
export async function startBrowserAuthorization(
  client: Client,
  connection: Schema["Connection"],
  basePath: string,
  options: Schema["CreateAuthorizationRequest"]["options"] = {},
) {
  if (!supportsBrowserAuthorization(window.location))
    throw new Error(
      "Browser authorization requires HTTPS or an exact loopback HTTP origin.",
    );
  const connector = connection.source.kind === "connector";
  const verifier = connector ? random() : undefined,
    state = connector ? random() : undefined;
  const challenge = verifier
    ? Array.from(
        new Uint8Array(
          await crypto.subtle.digest(
            "SHA-256",
            new TextEncoder().encode(verifier),
          ),
        ),
        (byte) => byte.toString(16).padStart(2, "0"),
      ).join("")
    : undefined;
  const callback = `${window.location.origin}/connections/callback`;
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
          ...(connector
            ? {
                return_url: callback,
                state: state!,
                completion_challenge: challenge!,
              }
            : { redirect_uri: callback }),
        },
      },
    ),
  );
  requireAuthorizationProgress(authorization);
  const href = authorizationHref(authorization.next_action?.url);
  if (!href) throw new Error("Invalid authorization URL.");
  const providerState = connector
    ? undefined
    : new URL(href).searchParams.get("state");
  if (!connector && !providerState)
    throw new Error("Authorization URL has no state.");
  const contextBase = {
    state: connector ? state! : providerState!,
    authorizationId: authorization.id,
    connectionId: connection.id,
    workspaceId: connection.workspace_id,
    returnPath: `${basePath}/connections`,
    expiresAt: authorization.expires_at,
  };
  const context: AuthorizationContext = connector
    ? { ...contextBase, type: "connector", verifier: verifier! }
    : { ...contextBase, type: "mcp" };
  sessionStorage.setItem(storageKey, JSON.stringify(context));
  window.location.assign(href);
  return authorization;
}
export function readAuthorization(): AuthorizationContext | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
    if (
      !value ||
      !["connector", "mcp"].includes(value.type) ||
      typeof value.state !== "string" ||
      !statePattern.test(value.state) ||
      (value.type === "connector" &&
        (typeof value.verifier !== "string" ||
          !/^[a-f0-9]{64}$/.test(value.verifier))) ||
      (value.type === "mcp" && value.verifier !== undefined) ||
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
export function takeCallback():
  | ({
      state: string;
    } & (
      | { type: "connector"; receipt: string; authorizationId: string }
      | { type: "mcp"; code?: string; iss?: string; error?: string }
    ))
  | null {
  if (window.location.pathname !== "/connections/callback") return null;
  const params = new URLSearchParams(window.location.search);
  window.history.replaceState(null, "", window.location.pathname);
  const receipt = params.getAll("receipt"),
    state = params.getAll("state"),
    ids = params.getAll("authorization_id"),
    code = params.getAll("code"),
    issuer = params.getAll("iss"),
    error = params.getAll("error");
  if (
    state.length === 1 &&
    statePattern.test(state[0] ?? "") &&
    ids.length === 0 &&
    receipt.length === 0 &&
    (code.length === 1) !== (error.length === 1) &&
    (code.length === 0 || (code[0]?.length ?? 0) <= 8192) &&
    (error.length === 0 || (error[0]?.length ?? 0) <= 256) &&
    issuer.length <= 1 &&
    (issuer[0]?.length ?? 0) <= 2048
  )
    return {
      type: "mcp",
      state: state[0],
      ...(code[0] ? { code: code[0] } : {}),
      ...(issuer[0] ? { iss: issuer[0] } : {}),
      ...(error[0] ? { error: error[0] } : {}),
    };
  if (
    ids.length !== 1 ||
    !ids[0] ||
    receipt.length !== 1 ||
    receipt[0].length < 32 ||
    receipt[0].length > 512 ||
    state.length !== 1 ||
    !statePattern.test(state[0])
  )
    return null;
  return {
    type: "connector",
    receipt: receipt[0],
    state: state[0],
    authorizationId: ids[0],
  };
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
