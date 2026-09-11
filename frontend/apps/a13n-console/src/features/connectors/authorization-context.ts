const storageKey = "a13n.connector-authorization";

export type BrowserAuthorization = {
  browser_nonce: string;
  workspace_id: string;
  return_path: string;
  connection_id: string;
  attempt_id?: string;
  expires_at?: string;
};

export function createBrowserNonce(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export function saveAuthorization(value: BrowserAuthorization): void {
  sessionStorage.setItem(storageKey, JSON.stringify(value));
}

export function clearAuthorization(): void {
  sessionStorage.removeItem(storageKey);
}

export function readAuthorization(): BrowserAuthorization | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) ?? "null");
    if (
      !value ||
      typeof value.browser_nonce !== "string" ||
      !/^[a-f0-9]{64}$/.test(value.browser_nonce) ||
      typeof value.workspace_id !== "string" ||
      typeof value.return_path !== "string" ||
      !/^\/workspace\/[a-z0-9-]+\/connections$/.test(value.return_path) ||
      typeof value.connection_id !== "string" ||
      typeof value.attempt_id !== "string" ||
      typeof value.expires_at !== "string" ||
      !(Date.parse(value.expires_at) > Date.now())
    )
      return null;
    return value;
  } catch {
    return null;
  }
}

/** Capture once before identity requests or rendering; never store the upstream session. */
export function takeCallbackSession(): string | null {
  if (window.location.pathname !== "/connector-setup/callback") return null;
  const parameters = new URLSearchParams(window.location.search);
  const sessions = parameters.getAll("session_uri");
  window.history.replaceState(null, "", window.location.pathname);
  return sessions.length === 1 &&
    sessions[0].length > 0 &&
    sessions[0].length <= 4096
    ? sessions[0]
    : null;
}
