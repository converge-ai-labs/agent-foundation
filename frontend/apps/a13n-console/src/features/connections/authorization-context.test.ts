// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import type { Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import {
  clearAuthorization,
  readAuthorization,
  startBrowserAuthorization,
  takeCallback,
} from "./authorization-context";
const context = {
  connectionId: "conn_test",
  returnPath: "/workspace/design/connections",
  expiresAt: new Date(Date.now() + 60_000).toISOString(),
};
afterEach(() => {
  clearAuthorization();
  window.history.replaceState(null, "", "/");
});
it("starts authorization under the seen version and returns to the Console callback", async () => {
  const expiresAt = new Date(Date.now() + 60_000).toISOString();
  const redirect = `${window.location.origin}/#state=provider-state`;
  const post = vi.fn().mockResolvedValue({
    data: {
      redirect_url: redirect,
      expires_at: expiresAt,
    } satisfies Schema["AuthorizationResult"],
    response: new Response(),
  });
  const client = { workspace: () => ({ POST: post }) } as unknown as Client;
  const connection = {
    id: "conn_test",
    organization_id: "org_test",
    workspace_id: "ws_test",
    type: "composio",
    name: "GitHub",
    config: { app: "github", actions: ["GITHUB_GET_REPO"], setup: {} },
    auth: "account",
    connector_provider_id: "cprov_test",
    status: "pending",
    failure: null,
    credential_configured: false,
    client_secret_configured: false,
    authorization_pending: false,
    last_test: null,
    enabled: true,
    version: 3,
    created_by_id: "usr_test",
    updated_by_id: "usr_test",
    created_at: "2026-09-12T00:00:00Z",
    updated_at: "2026-09-12T00:00:00Z",
  } satisfies Schema["Connection"];

  await startBrowserAuthorization(client, connection, "/workspace/design");

  expect(post).toHaveBeenCalledWith(
    "/api/v1/connections/{connection_id}/authorize",
    {
      params: {
        path: { connection_id: "conn_test" },
      },
      headers: { "If-Match": '"conn_test:3"' },
      body: { return_url: `${window.location.origin}/connections/callback` },
    },
  );
  expect(window.location.href).toBe(redirect);
  expect(readAuthorization()).toEqual({
    connectionId: connection.id,
    returnPath: "/workspace/design/connections",
    expiresAt,
  });
  post.mockResolvedValue({
    data: { redirect_url: null, expires_at: null },
    response: new Response(),
  });
  await expect(
    startBrowserAuthorization(client, connection, "/workspace/design"),
  ).rejects.toThrow("Invalid authorization URL.");
});
it("reads the Service outcome and strips it from the address immediately", () => {
  window.history.replaceState(
    null,
    "",
    "/connections/callback?connection_id=conn_test&status=ready",
  );
  expect(takeCallback()).toEqual({
    connectionId: "conn_test",
    status: "ready",
  });
  expect(window.location.search).toBe("");
  expect(takeCallback()).toBeNull();
  window.history.replaceState(
    null,
    "",
    "/connections/callback?connection_id=conn_test&status=pending&error=access_denied",
  );
  expect(takeCallback()).toEqual({
    connectionId: "conn_test",
    status: "pending",
    error: "access_denied",
  });
});
it.each([
  { expiresAt: new Date(Date.now() - 1).toISOString() },
  { returnPath: "https://evil.example" },
  { connectionId: undefined },
])("rejects expired or malformed application context: %j", (change) => {
  sessionStorage.setItem(
    "a13n.connection-authorization",
    JSON.stringify({ ...context, ...change }),
  );
  expect(readAuthorization()).toBeNull();
});
it.each([
  "connection_id=conn_test&connection_id=other&status=ready",
  "connection_id=conn_test&status=active",
  "connection_id=conn_test&status=pending&error=%3Cscript%3E",
  "code=provider-code&state=provider-state",
])("rejects ambiguous or unexpected callback values: %s", (query) => {
  window.history.replaceState(null, "", `/connections/callback?${query}`);
  expect(takeCallback()).toBeNull();
  expect(window.location.search).toBe("");
});
