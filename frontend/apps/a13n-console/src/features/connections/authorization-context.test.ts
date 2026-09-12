// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import {
  clearAuthorization,
  readAuthorization,
  takeCallback,
} from "./authorization-context";
const state = "a".repeat(64),
  receipt = "r".repeat(48);
const context = {
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
  { verifier: undefined },
  { state: "short" },
])("rejects expired or malformed application context: %j", (change) => {
  sessionStorage.setItem(
    "a13n.connection-authorization",
    JSON.stringify({ ...context, ...change }),
  );
  expect(readAuthorization()).toBeNull();
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
