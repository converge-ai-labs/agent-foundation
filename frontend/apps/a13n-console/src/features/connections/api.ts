import type { Client } from "../../service-client";
import { data, type Schema } from "../../shared/api";

type Connection = Schema["Connection"];

/** Display authorization progress without changing the Service credential states. */
export function connectionState(connection: Connection) {
  if (!connection.enabled) return "disabled";
  if (connection.status === "ready") return "ready";
  if (connection.authorization_pending)
    return connection.status === "reauthorization_required"
      ? "reauthorizing"
      : "authorizing";
  return connection.status === "pending" ? "unauthorized" : connection.status;
}

/** Cancelling a flow is safe only when there is no existing credential to revoke. */
export function authorizationRemoval(connection: Connection) {
  if (connection.auth === "none") return undefined;
  if (connection.credential_configured) return "revoke";
  if (connection.authorization_pending) return "cancel";
  if (connection.status === "reauthorization_required") return "revoke";
  return undefined;
}

export type AuthorizationRemovalResult = RemoteCleanup | "cancelled";

/** Discovers the tools with the current configuration and credential; the outcome becomes `last_test`. */
export function testConnection(client: Client, connection: Connection) {
  return client
    .workspace(connection.workspace_id)
    .POST("/api/v1/connections/{connection_id}/test", {
      params: { path: { connection_id: connection.id } },
    })
    .then(data);
}

/** A failed test carries safe text explaining why the tools could not be listed. */
export function requireTestSuccess(test: Schema["ConnectionTest"]) {
  if (test.status === "failed") throw new Error(test.message ?? test.status);
  return test;
}

/** What revoking did beyond clearing the local credential. */
export type RemoteCleanup =
  Schema["RevokedConnection"]["remote_revocation"] | "unknown";
export function revokeCleanup(
  revoked: Schema["RevokedConnection"],
): RemoteCleanup {
  // A failed remote revoke is recorded as the connection's failure; one whose
  // outcome is unknown may still have taken effect.
  return revoked.remote_revocation === "failed" &&
    revoked.failure?.reason === "outcome_unknown"
    ? "unknown"
    : revoked.remote_revocation;
}
