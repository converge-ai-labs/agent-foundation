import { readPreference, writePreference } from "./preferences";

const key = "notifications.browser-id";
let ephemeralId: string | undefined;

export const currentBrowserId = () => readPreference(key, "") || ephemeralId;

export async function notificationBrowserId(): Promise<string> {
  const existing = currentBrowserId();
  if (existing) return existing;
  const initialize = () => {
    const stored = currentBrowserId();
    if (stored) return stored;
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const id =
      "browser_" +
      Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    writePreference(key, id);
    if (!readPreference(key, "")) ephemeralId = id;
    return id;
  };
  // Concurrent first submissions in separate tabs must use the same identity.
  return navigator.locks
    ? navigator.locks
        .request("a13n-harness-ui.notification-browser", initialize)
        .catch(initialize)
    : initialize();
}

export async function browserSubmissionHeaders() {
  return { "X-Harness-Browser": await notificationBrowserId() };
}
