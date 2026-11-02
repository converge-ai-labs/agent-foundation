import type { TFunction } from "i18next";
import type { Schema } from "../transport/client";

// Account status has no message code. Match only known Host-authored text.
const accountMessages = new Set([
  "Host-managed ChatGPT registration; independent of Codex.",
  "CLI keychain storage is unsupported. Use native login or explicitly enable CLI plaintext storage.",
  "Sign in to create a managed subscription account.",
  "The external login could not be imported. Sign in here.",
  "Imported the existing login. Its refresh grant is still shared with the original CLI; sign in again here if that grant is rejected.",
]);

export function accountMessage(message: string, t: TFunction): string {
  return accountMessages.has(message) ? t(message) : message;
}

export function loginMessage(
  status: Schema<"LoginStatus">,
  t: TFunction,
): string | null | undefined {
  if (status.state === "waiting") {
    if (status.provider === "chatgpt" && status.method === "manual_callback")
      return t(
        "After sign-in, paste the complete callback URL from the browser address bar, even if the loopback page cannot be reached. Never share that URL.",
      );
    if (status.provider === "chatgpt" && status.method === "browser")
      return t(
        "If the automatic callback cannot reach this Host, paste the complete callback URL from the browser address bar. Never share that URL.",
      );
    if (status.provider === "codex" && status.method === "browser")
      return t(
        "The browser must reach this Host at localhost:1455. Use device authorization for a remote Host.",
      );
    if (status.provider === "grok" && status.method === "browser")
      return t(
        "The browser must reach this Host's loopback callback. Use device authorization for a remote Host.",
      );
    if (status.provider === "copilot" && status.method === "device")
      return t(
        "Uses the official Copilot CLI application identity. Review GitHub's requested permissions, including repository access. Authorization does not verify Copilot entitlement.",
      );
  }
  if (status.state === "failed" || status.state === "expired") {
    switch (status.error_code) {
      case "device_authorization_expired":
        return t("Device authorization expired. No fallback was started.");
      case "device_authorization_denied":
        return t("Device authorization denied. No fallback was started.");
      case "device_authorization_unsupported":
        return t("Device authorization unsupported. No fallback was started.");
    }
    // The Host preserves typed error codes while using this same failure text.
    const failureMessage =
      "Login did not complete. Check account status before retrying; no fallback was started.";
    if (
      status.error_code === "oauth_login_failed" ||
      status.message === failureMessage
    )
      return t(failureMessage);
    if (status.state === "expired" && !status.error_code)
      return t("The login expired. Start a new login.");
  }
  return status.message;
}
