import type { Client } from "../../service-client";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { authorizationHref } from "../../shared/authorization-link";

const storageKey = "a13n.connection-authorization";
const callbackPath = "/connections/callback";
type Status = Schema["ConnectionStatus"];
const statuses: readonly string[] = [
  "pending",
  "ready",
  "reauthorization_required",
] satisfies Status[];
const isStatus = (value: string): value is Status => statuses.includes(value);
type AuthorizationContext = {
  connectionId: string;
  returnPath: string;
  expiresAt: string;
};
/** A browser flow in progress: where it continues and until when. */
type BrowserAuthorization = { redirect_url: string; expires_at: string };
type AuthorizationOutcome = {
  connectionId: string;
  status: Status;
  error?: string;
};

/**
 * Asks the Service for a new credential under the seen version. A grant that
 * needs no browser (client credentials) completes here and returns no redirect.
 */
export function authorizeConnection(
  client: Client,
  connection: Schema["Connection"],
  body: Schema["AuthorizationRequest"] = {},
) {
  return client
    .workspace(connection.workspace_id)
    .POST("/api/v1/connections/{connection_id}/authorize", {
      params: { path: { connection_id: connection.id } },
      headers: ifMatch(rowTag(connection)),
      body,
    })
    .then(data);
}

/**
 * The Service completes the flow at its own callback and then returns the
 * browser to this Console route, which the deployment allowlists.
 */
export async function startBrowserAuthorization(
  client: Client,
  connection: Schema["Connection"],
  basePath: string,
): Promise<BrowserAuthorization> {
  const authorization = await authorizeConnection(client, connection, {
    return_url: `${window.location.origin}${callbackPath}`,
  });
  const href = authorizationHref(authorization.redirect_url);
  if (!href || !authorization.expires_at)
    throw new Error("Invalid authorization URL.");
  const context: AuthorizationContext = {
    connectionId: connection.id,
    returnPath: `${basePath}/connections`,
    expiresAt: authorization.expires_at,
  };
  sessionStorage.setItem(storageKey, JSON.stringify(context));
  window.location.assign(href);
  return { redirect_url: href, expires_at: authorization.expires_at };
}
export function readAuthorization(): AuthorizationContext | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
    if (
      !value ||
      typeof value.connectionId !== "string" ||
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
/** Strip the Service's authorization outcome from the address before rendering. */
export function takeCallback(): AuthorizationOutcome | null {
  if (window.location.pathname !== callbackPath) return null;
  const params = new URLSearchParams(window.location.search);
  window.history.replaceState(null, "", window.location.pathname);
  const ids = params.getAll("connection_id"),
    status = params.getAll("status"),
    error = params.getAll("error");
  if (
    ids.length !== 1 ||
    !/^[A-Za-z0-9_-]{1,128}$/.test(ids[0]) ||
    status.length !== 1 ||
    !isStatus(status[0]) ||
    error.length > 1 ||
    (error.length === 1 && !/^[A-Za-z0-9_]{1,64}$/.test(error[0]))
  )
    return null;
  return {
    connectionId: ids[0],
    status: status[0],
    ...(error[0] ? { error: error[0] } : {}),
  };
}
