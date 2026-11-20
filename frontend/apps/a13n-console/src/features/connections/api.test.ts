import { expect, it } from "vitest";
import type { Schema } from "../../shared/api";
import {
  connectionState,
  authorizationRemoval,
  revokeCleanup,
  type RemoteCleanup,
} from "./api";

const connection: Schema["Connection"] = {
  id: "conn_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  type: "mcp",
  name: "Example",
  config: { url: "https://mcp.example/mcp" },
  auth: "oauth",
  connector_provider_id: null,
  status: "ready",
  failure: null,
  credential_configured: true,
  client_secret_configured: false,
  authorization_pending: false,
  last_test: null,
  enabled: true,
  version: 3,
  created_by_id: "usr_test",
  updated_by_id: "usr_test",
  created_at: "2026-09-12T00:00:00Z",
  updated_at: "2026-09-12T00:00:00Z",
};
const revoked = (
  remote_revocation: Schema["RevokedConnection"]["remote_revocation"],
  reason?: Schema["ConnectionFailure"]["reason"],
): Schema["RevokedConnection"] => ({
  ...connection,
  status: "pending",
  version: 4,
  remote_revocation,
  failure: reason
    ? {
        operation_id: "connop_test",
        operation_kind: "revoke",
        reason,
        code: null,
      }
    : null,
});

it.each<[string, Schema["RevokedConnection"], RemoteCleanup]>([
  ["a revoked OAuth grant", revoked("revoked"), "revoked"],
  ["a rejected remote revoke", revoked("failed", "rejected"), "failed"],
  [
    "an unconfirmed remote revoke",
    revoked("failed", "outcome_unknown"),
    "unknown",
  ],
  [
    "a credential with nothing to revoke remotely",
    revoked("skipped"),
    "skipped",
  ],
])("reports the cleanup of %s", (_, result, expected) => {
  expect(revokeCleanup(result)).toBe(expected);
});

it.each([
  ["pending", false, "unauthorized"],
  ["pending", true, "authorizing"],
  ["ready", false, "ready"],
  ["ready", true, "ready"],
  ["reauthorization_required", false, "reauthorization_required"],
  ["reauthorization_required", true, "reauthorizing"],
] as const)(
  "displays %s with authorization_pending=%s as %s",
  (status, authorization_pending, expected) => {
    const value = { ...connection, status, authorization_pending };
    expect(connectionState(value)).toBe(expected);
    expect(connectionState({ ...value, enabled: false })).toBe("disabled");
  },
);

it("offers cancellation only for a flow without an existing credential", () => {
  const pending = {
    ...connection,
    status: "pending" as const,
    credential_configured: false,
  };
  expect(authorizationRemoval(pending)).toBeUndefined();
  expect(
    authorizationRemoval({ ...pending, authorization_pending: true }),
  ).toBe("cancel");
  expect(
    authorizationRemoval({ ...connection, authorization_pending: true }),
  ).toBe("revoke");
  expect(authorizationRemoval({ ...connection, enabled: false })).toBe(
    "revoke",
  );
  expect(
    authorizationRemoval({
      ...connection,
      auth: "none",
      credential_configured: false,
    }),
  ).toBeUndefined();
  expect(
    authorizationRemoval({ ...pending, status: "reauthorization_required" }),
  ).toBe("revoke");
});
