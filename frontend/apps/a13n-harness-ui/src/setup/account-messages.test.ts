import { afterEach, expect, it } from "vitest";
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
afterEach(async () => {
  await i18n.changeLanguage("en");
});

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
])(
  "localizes known login failure %s in both languages",
  async (error_code, key) => {
    for (const locale of ["en", "zh-CN"]) {
      await i18n.changeLanguage(locale);
      expect(i18n.exists(key, { lng: locale })).toBe(true);
      const message = loginMessage(
        { ...base, state: "expired", error_code },
        i18n.t,
      );
      expect(message).toBe(i18n.t(key));
      if (locale === "zh-CN") expect(message).not.toBe(key);
    }
  },
);

it.each([
  "account_switch_confirmation_required",
  "oauth_callback_invalid",
  "future_provider_error",
])(
  "localizes the Host failure message with error code %s",
  async (error_code) => {
    const message =
      "Login did not complete. Check account status before retrying; no fallback was started.";
    for (const locale of ["en", "zh-CN"]) {
      await i18n.changeLanguage(locale);
      expect(loginMessage({ ...base, error_code, message }, i18n.t)).toBe(
        i18n.t(message),
      );
      if (locale === "zh-CN") expect(i18n.t(message)).not.toBe(message);
      expect(loginMessage({ ...base, error_code }, i18n.t)).toBe(base.message);
    }
  },
);

it.each([
  ["codex", "browser"],
  ["grok", "browser"],
  ["copilot", "device"],
] as const)(
  "localizes known %s %s login instructions",
  async (provider, method) => {
    const status = { ...base, state: "waiting" as const, provider, method };
    const english = loginMessage(status, i18n.t)!;
    await i18n.changeLanguage("zh-CN");
    expect(i18n.exists(english, { lng: "zh-CN" })).toBe(true);
    expect(loginMessage(status, i18n.t)).not.toBe(english);
  },
);

it("preserves unknown login and account diagnostics verbatim", async () => {
  await i18n.changeLanguage("zh-CN");
  expect(
    loginMessage({ ...base, error_code: "future_provider_error" }, i18n.t),
  ).toBe(base.message);
  expect(loginMessage({ ...base, state: "waiting" }, i18n.t)).toBe(
    base.message,
  );
  expect(accountMessage(base.message!, i18n.t)).toBe(base.message);
});

it("translates only known Host account status messages", async () => {
  const messages = [
    "Host-managed ChatGPT registration; independent of Codex.",
    "CLI keychain storage is unsupported. Use native login or explicitly enable CLI plaintext storage.",
    "Sign in to create a managed subscription account.",
    "The external login could not be imported. Sign in here.",
    "Imported the existing login. Its refresh grant is still shared with the original CLI; sign in again here if that grant is rejected.",
  ];
  for (const locale of ["en", "zh-CN"]) {
    await i18n.changeLanguage(locale);
    for (const message of messages) {
      expect(i18n.exists(message, { lng: locale })).toBe(true);
      expect(accountMessage(message, i18n.t)).toBe(i18n.t(message));
      if (locale === "zh-CN")
        expect(accountMessage(message, i18n.t)).not.toBe(message);
    }
  }
});
