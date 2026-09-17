// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import type { Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import {
  clearAuthorization,
  readAuthorization,
  startBrowserAuthorization,
  supportsBrowserAuthorization,
  takeCallback,
} from "./authorization-context";
const state = "a".repeat(64),
  receipt = "r".repeat(48);
const context = {
  type: "connector",
  state,
  verifier: "b".repeat(64),
  authorizationId: "auth_test",
  connectionId: "conn_test",
  workspaceId: "ws_test",
  returnPath: "/workspace/design/connections",
  expiresAt: new Date(Date.now() + 60_000).toISOString(),
};
afterEach(() => {
  clearAuthorization();
  window.history.replaceState(null, "", "/");
});
it.each([
  ["https:", "console.example", true],
  ["http:", "localhost", true],
  ["http:", "127.0.0.1", true],
  ["http:", "[::1]", true],
  ["http:", "localhost.example", false],
  ["http:", "127.0.0.2", false],
  ["ftp:", "localhost", false],
  ["ws:", "127.0.0.1", false],
])(
  "validates browser authorization origin %s//%s",
  (protocol, hostname, expected) => {
    expect(supportsBrowserAuthorization({ protocol, hostname })).toBe(expected);
  },
);
it("keeps application state for Connector fragment authorization URLs", async () => {
  const expiresAt = new Date(Date.now() + 60_000).toISOString();
  const authorization = {
    id: "authz_test",
    connection_id: "conn_test",
    status: "awaiting_user",
    next_action: {
      type: "open_url",
      url: "http://localhost/connection-authorizations/browser#authorization_id=authz_test&token=launch-token",
    },
    error_code: null,
    outcome_unknown: false,
    expires_at: expiresAt,
    updated_at: new Date().toISOString(),
  } satisfies Schema["Authorization"];
  const post = vi.fn().mockResolvedValue({
    data: authorization,
    response: new Response(),
  });
  const client = { http: { POST: post } } as unknown as Client;
  const connection = {
    id: "conn_test",
    organization_id: "org_test",
    workspace_id: "ws_test",
    source: {
      kind: "connector",
      provider_id: "cnr_test",
      connector_key: "github",
    },
    name: "GitHub",
    status: "pending",
    version: 1,
    authorization_generation: 1,
    credential_configured: false,
    created_by: { principal_type: "user", principal_id: "usr_test" },
    created_at: "2026-09-12T00:00:00Z",
    updated_at: "2026-09-12T00:00:00Z",
  } satisfies Schema["Connection"];

  await startBrowserAuthorization(client, connection, "/workspace/design");

  const saved = readAuthorization();
  expect(saved).toMatchObject({
    type: "connector",
    authorizationId: authorization.id,
    connectionId: connection.id,
    returnPath: "/workspace/design/connections",
  });
  expect(saved?.state).toMatch(/^[a-f0-9]{64}$/);
  expect(post.mock.calls[0][1].body.state).toBe(saved?.state);
});
it("binds completion to the application proof and strips callback material immediately", () => {
  sessionStorage.setItem(
    "a13n.connection-authorization",
    JSON.stringify(context),
  );
  expect(readAuthorization()).toEqual(context);
  window.history.replaceState(
    null,
    "",
    `/connections/callback?authorization_id=auth_test&receipt=${receipt}&state=${state}`,
  );
  expect(takeCallback()).toEqual({
    type: "connector",
    receipt,
    state,
    authorizationId: "auth_test",
  });
  expect(window.location.search).toBe("");
  expect(takeCallback()).toBeNull();
  expect(JSON.stringify(sessionStorage)).not.toContain(receipt);
});
it.each([
  { expiresAt: new Date(Date.now() - 1).toISOString() },
  { returnPath: "https://evil.example" },
  { authorizationId: undefined },
  { type: "mcp" },
  { state: "short" },
])("rejects expired or malformed application context: %j", (change) => {
  sessionStorage.setItem(
    "a13n.connection-authorization",
    JSON.stringify({ ...context, ...change }),
  );
  expect(readAuthorization()).toBeNull();
});
it("accepts a single MCP provider result and rejects mixed callback fields", () => {
  const providerState = "oauth_state-with-url-safe-characters_123456789";
  window.history.replaceState(
    null,
    "",
    `/connections/callback?code=provider-code&iss=${encodeURIComponent("https://auth.example")}&state=${providerState}`,
  );
  expect(takeCallback()).toEqual({
    type: "mcp",
    code: "provider-code",
    iss: "https://auth.example",
    state: providerState,
  });
  window.history.replaceState(
    null,
    "",
    `/connections/callback?code=code&error=denied&state=${state}`,
  );
  expect(takeCallback()).toBeNull();
});
it.each([
  `authorization_id=auth_test&receipt=${receipt}&receipt=${receipt}&state=${state}`,
  `authorization_id=auth_test&authorization_id=other&receipt=${receipt}&state=${state}`,
  "status=success&connected_account_id=untrusted",
])("rejects ambiguous or provider-supplied callback values", (query) => {
  window.history.replaceState(null, "", `/connections/callback?${query}`);
  expect(takeCallback()).toBeNull();
  expect(window.location.search).toBe("");
});
