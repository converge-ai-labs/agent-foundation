import { useEffect, useRef, useState } from "react";
import { api, result, message, type Model } from "./client";

export function ApiKeyConnection({
  onSelect,
  selectedReference,
}: {
  onSelect: (reference: string | null) => void;
  selectedReference: string | null;
}) {
  const [reference, setReference] = useState(
    selectedReference ?? "key-primary",
  );
  const [key, setKey] = useState("");
  const [keys, setKeys] = useState<Model<"ApiKeyStatus">[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    setKeys(result(await api.GET("/api/auth/keys")));
  }
  useEffect(() => {
    void refresh().catch((e) => setError(message(e)));
  }, []);
  async function change(remove: boolean) {
    setBusy(true);
    setError("");
    const secret = key;
    setKey("");
    try {
      if (remove) {
        result(
          await api.DELETE("/api/auth/keys/{reference}", {
            params: { path: { reference } },
          }),
        );
        onSelect(null);
      } else {
        result(
          await api.PUT("/api/auth/keys", {
            body: { credential_ref: reference, key: secret },
          }),
        );
        onSelect(reference);
      }
      await refresh();
    } catch (e) {
      setError(message(e));
      onSelect(null);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <p className="muted">
        Keys are saved immediately in this Host's private plaintext auth.json,
        independently of setup. Configuration contains only a reference. Keys
        are never returned to the browser. Saving does not verify provider
        access.
      </p>
      <label>
        Saved credential
        <select
          value=""
          disabled={busy}
          onChange={(e) => {
            setReference(e.target.value);
            onSelect(e.target.value);
          }}
        >
          <option value="">Choose an existing key</option>
          {keys.map((k) => (
            <option key={k.credential_ref}>{k.credential_ref}</option>
          ))}
        </select>
      </label>
      <label>
        Credential reference
        <input
          value={reference}
          disabled={busy}
          onChange={(e) => {
            setReference(e.target.value);
            onSelect(null);
          }}
        />
      </label>
      <label>
        API key
        <input
          type="password"
          autoComplete="off"
          value={key}
          disabled={busy}
          onChange={(e) => {
            setKey(e.target.value);
            onSelect(null);
          }}
        />
      </label>
      <div className="actions">
        <button
          disabled={
            busy || !key || !/^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$/.test(reference)
          }
          onClick={() => void change(false)}
        >
          Save / replace key
        </button>
        <button
          disabled={busy || !keys.some((k) => k.credential_ref === reference)}
          onClick={() => {
            if (
              window.confirm(
                `Delete ${reference}? Future model resolution using this reference will fail.`,
              )
            )
              void change(true);
          }}
        >
          Delete key
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

export function SubscriptionLogin({
  connected,
}: {
  connected: (provider: "codex" | "grok") => Promise<void>;
}) {
  const [session, setSession] = useState<Model<"LoginStatus">>();
  const [switchAccount, setSwitchAccount] = useState(false);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const active = useRef<string | null>(null);
  const mounted = useRef(true);
  const finished = useRef<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (active.current)
        void api.DELETE("/api/auth/logins/{session_id}", {
          params: { path: { session_id: active.current } },
        });
    };
  }, []);
  const waiting = session?.state === "starting" || session?.state === "waiting";
  useEffect(() => {
    if (!session || !waiting) return;
    const timer = window.setInterval(() => {
      void api
        .GET("/api/auth/logins/{session_id}", {
          params: { path: { session_id: session.session_id } },
        })
        .then(result)
        .then(async (next) => {
          if (!mounted.current) return;
          setSession(next);
          if (next.state !== "starting" && next.state !== "waiting")
            active.current = null;
          if (
            next.state === "succeeded" &&
            finished.current !== next.session_id
          ) {
            finished.current = next.session_id;
            await connected(next.provider);
          }
        })
        .catch((e) => setError(message(e)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [session, waiting, connected]);
  async function start(
    provider: "codex" | "grok",
    method: "device" | "browser",
  ) {
    setStarting(true);
    setError("");
    try {
      const next = result(
        await api.POST("/api/auth/logins", {
          body: { provider, method, allow_account_switch: switchAccount },
        }),
      );
      if (!mounted.current) {
        await api.DELETE("/api/auth/logins/{session_id}", {
          params: { path: { session_id: next.session_id } },
        });
        return;
      }
      active.current = next.session_id;
      setSession(next);
    } catch (e) {
      setError(message(e));
    } finally {
      setStarting(false);
    }
  }
  async function cancel() {
    if (!active.current) return;
    try {
      setSession(
        result(
          await api.DELETE("/api/auth/logins/{session_id}", {
            params: { path: { session_id: active.current } },
          }),
        ),
      );
      active.current = null;
    } catch (e) {
      setError(message(e));
    }
  }
  return (
    <section>
      <p className="muted">
        Logins write to the compatible Codex / Grok account store immediately.
        Browser login requires a browser that can reach this Host's loopback
        callback. For a remote Host, use device authorization from any browser.
        No automatic fallback or browser opening.
      </p>
      <label>
        <input
          type="checkbox"
          checked={switchAccount}
          disabled={waiting || starting}
          onChange={(e) => setSwitchAccount(e.target.checked)}
        />
        Allow replacing a different shared account
      </label>
      {(["codex", "grok"] as const).map((provider) => (
        <div className="actions" key={provider}>
          <button
            disabled={waiting || starting}
            onClick={() => void start(provider, "browser")}
          >
            Connect {provider} in browser
          </button>
          <button
            disabled={waiting || starting}
            onClick={() => void start(provider, "device")}
          >
            Connect {provider} with device code
          </button>
        </div>
      ))}
      {session && (
        <div role="status">
          <p>
            {session.provider}: {session.state}. {session.message}
          </p>
          {session.verification_url && (
            <p>
              <a
                href={session.verification_url}
                target="_blank"
                rel="noreferrer"
              >
                Open provider authorization page
              </a>
            </p>
          )}
          {session.user_code && (
            <p>
              User code: <strong>{session.user_code}</strong>. Expires within{" "}
              {session.expires_in} seconds.
            </p>
          )}
          {waiting && (
            <button onClick={() => void cancel()}>Cancel login</button>
          )}
          {session.error_code && <p>{session.error_code}</p>}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
