import { expect, it } from "vitest";
import { i18n } from "../i18n";
import type { Schema } from "../transport/client";
import { accountMessage, loginMessage } from "./account-messages";

const base: Schema<"LoginStatus"> = {
  session_id: "login-test",
  provider: "codex",
  method: "device",
  state: "failed",
  message: "provider diagnostic: synthetic detail",
};
const t = i18n.getFixedT("zh-CN");

it.each([
  [
    "device_authorization_expired",
    "Device authorization expired. No fallback was started.",
  ],
  [
    "device_authorization_denied",
    "Device authorization denied. No fallback was started.",
  ],
  [
    "device_authorization_unsupported",
    "Device authorization unsupported. No fallback was started.",
  ],
  [
    "oauth_login_failed",
    "Login did not complete. Check account status before retrying; no fallback was started.",
  ],
  [null, "The login expired. Start a new login."],
])("localizes known login failure %s", (error_code, key) => {
  expect(loginMessage({ ...base, state: "expired", error_code }, t)).toBe(
    t(key),
  );
});

it("localizes the Host failure message with an unknown error code", () => {
  const message =
    "Login did not complete. Check account status before retrying; no fallback was started.";
  expect(
    loginMessage({ ...base, error_code: "future_provider_error", message }, t),
  ).toBe(t(message));
});

it.each([
  ["codex", "browser"],
  ["grok", "browser"],
  ["copilot", "device"],
] as const)("localizes known %s %s login instructions", (provider, method) => {
  const status = { ...base, state: "waiting" as const, provider, method };
  const english = loginMessage(status, i18n.getFixedT("en"))!;
  expect(loginMessage(status, t)).not.toBe(english);
  expect(loginMessage(status, t)).not.toBe(base.message);
});

it("preserves unknown login and account diagnostics verbatim", () => {
  expect(
    loginMessage({ ...base, error_code: "future_provider_error" }, t),
  ).toBe(base.message);
  expect(loginMessage({ ...base, state: "waiting" }, t)).toBe(base.message);
  expect(accountMessage(base.message!, t)).toBe(base.message);
});

it("translates only known Host account status messages", () => {
  const messages = [
    "Host-managed ChatGPT registration; independent of Codex.",
    "CLI keychain storage is unsupported. Use native login or explicitly enable CLI plaintext storage.",
    "Sign in to create a managed subscription account.",
    "The external login could not be imported. Sign in here.",
    "Imported the existing login. Its refresh grant is still shared with the original CLI; sign in again here if that grant is rejected.",
  ];
  for (const message of messages) {
    expect(accountMessage(message, t)).toBe(t(message));
    expect(accountMessage(message, t)).not.toBe(message);
  }
});
