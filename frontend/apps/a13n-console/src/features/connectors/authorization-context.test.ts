// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import {
  clearAuthorization,
  createBrowserNonce,
  readAuthorization,
  saveAuthorization,
  takeCallbackSession,
} from "./authorization-context";

afterEach(() => {
  clearAuthorization();
  window.history.replaceState(null, "", "/");
});

it("keeps browser proof separate from the upstream session and rejects expired context", () => {
  const nonce = createBrowserNonce();
  expect(nonce).toMatch(/^[a-f0-9]{64}$/);
  expect(createBrowserNonce()).not.toBe(nonce);
  const context = {
    browser_nonce: nonce,
    return_path: "/organization/default/connectors",
    workspace_id: "ws_test",
    connection_id: "cconn_test",
    attempt_id: "csa_test",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  };
  saveAuthorization(context);
  expect(readAuthorization()).toEqual(context);
  window.history.replaceState(
    null,
    "",
    "/connector-setup/callback?session_uri=opaque%3A%2F%2Fsession",
  );
  expect(takeCallbackSession()).toBe("opaque://session");
  expect(window.location.search).toBe("");
  expect(takeCallbackSession()).toBeNull();
  expect(JSON.stringify(sessionStorage)).not.toContain("opaque");
  saveAuthorization({
    ...context,
    expires_at: new Date(Date.now() - 1).toISOString(),
  });
  expect(readAuthorization()).toBeNull();
});

it("rejects ambiguous callback parameters and missing tab context", () => {
  expect(readAuthorization()).toBeNull();
  window.history.replaceState(
    null,
    "",
    "/connector-setup/callback?session_uri=one&session_uri=two",
  );
  expect(takeCallbackSession()).toBeNull();
  expect(window.location.search).toBe("");
});
