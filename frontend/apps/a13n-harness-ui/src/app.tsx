import { useEffect, useState } from "react";

const KEY_STORAGE = "a13n-harness-ui.api-key";

function initialKey(): string {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const key = fragment.get("api_key");
  if (key !== null) {
    fragment.delete("api_key");
    const rest = fragment.toString();
    window.history.replaceState(
      null,
      "",
      window.location.pathname +
        window.location.search +
        (rest ? `#${rest}` : ""),
    );
    return key;
  }
  try {
    return window.localStorage.getItem(KEY_STORAGE) ?? "";
  } catch {
    return "";
  }
}

export function BrowserApp() {
  const [key, setKey] = useState(initialKey);
  const [input, setInput] = useState(key);
  const [attempt, setAttempt] = useState(0);
  const [version, setVersion] = useState<string | null>(null);
  const [revision, setRevision] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setVersion(null);
    setError("");
    async function connect() {
      try {
        const response = await fetch("/api/status", {
          headers: key ? { Authorization: `Bearer ${key}` } : {},
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 401) {
          throw new Error("Enter the API key printed by this server.");
        }
        if (!response.ok)
          throw new Error("Server unavailable. Retry when it is ready.");
        const status: unknown = await response.json();
        if (
          typeof status !== "object" ||
          status === null ||
          !("api_version" in status) ||
          status.api_version !== "1" ||
          !("version" in status) ||
          typeof status.version !== "string" ||
          !status.version
        ) {
          throw new Error(
            "This server returned an incompatible status response.",
          );
        }
        if (controller.signal.aborted) return;
        setVersion(status.version);
        setRevision(
          "build_revision" in status &&
            typeof status.build_revision === "string"
            ? status.build_revision
            : null,
        );
        try {
          if (key) window.localStorage.setItem(KEY_STORAGE, key);
          else window.localStorage.removeItem(KEY_STORAGE);
        } catch {
          // Storage may be disabled; authentication still works for this page.
        }
      } catch (failure) {
        if (!controller.signal.aborted) {
          setError(
            failure instanceof Error ? failure.message : "Connection failed.",
          );
        }
      }
    }
    void connect();
    return () => controller.abort();
  }, [key, attempt]);

  return (
    <main>
      <h1>Harness UI</h1>
      <p>
        WebUI foundation. Conversation and management controls are not yet
        available.
      </p>
      {version ? (
        <>
          <p role="status">
            Running version: <strong>{version}</strong>
            {revision && <span> (revision {revision})</span>}
          </p>
          <p>
            Shared drafts, Host files, Git views, and terminals are not yet
            available.
          </p>
          <button
            onClick={() => {
              try {
                window.localStorage.removeItem(KEY_STORAGE);
              } catch {
                /* Storage is optional. */
              }
              setInput("");
              setKey("");
              setAttempt((value) => value + 1);
            }}
          >
            Forget API key
          </button>
        </>
      ) : (
        <>
          <p role="status">{error || "Connecting to server…"}</p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setKey(input);
              setAttempt((value) => value + 1);
            }}
          >
            <label>
              API key{" "}
              <input
                type="password"
                autoComplete="off"
                value={input}
                onChange={(event) => setInput(event.target.value)}
              />
            </label>
            <button type="submit">Connect</button>
          </form>
        </>
      )}
    </main>
  );
}
