import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowserRouter } from "react-router";
import { GuestApp } from "./guest-app";
import { DebugProvider } from "./shell/debug";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Button, Logo } from "a13n-ui";
import {
  ApiError,
  createTransport,
  result,
  type Schema,
} from "./transport/client";
import { Startup } from "./shell/startup";
import { VoiceProvider } from "./voice";
import { TransportContext } from "./transport/context";
import { DraftContext, type SourceDraft } from "./configuration/sources";
import { Workbench } from "./shell/workbench";
import { NotificationsProvider } from "./shell/notifications";
import { disablePush } from "./shell/push";
import { ComposerDrafts } from "./conversations/composer";
import { NewConversationDrafts } from "./conversations/new-conversation";
import { NewDraftStore } from "./conversations/new-draft";
import { ChildControlsProvider } from "./conversations/child-controls";
import { FileBuffers, type FileBuffer } from "./native/buffer";
import type { ThreadDraft } from "./conversations/draft";
import { TextField } from "./shell/ui";
import styles from "./shell/workbench.module.css";

const KEY_STORAGE = "a13n-harness-ui.api-key";
const PUSH_CLEANUP_WARNING =
  "Couldn't turn off background alerts. Task previews may still arrive. Block this site's notifications in your browser settings.";
export function initialKey(): string {
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
function retainKey(key: string) {
  try {
    if (key) localStorage.setItem(KEY_STORAGE, key);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* Authentication does not require browser storage. */
  }
}
export function BrowserApp() {
  const shareId = /^\/share\/([^/]+)(?:\/|$)/.exec(
    window.location.pathname,
  )?.[1];
  return (
    <DebugProvider>
      {shareId ? <GuestApp shareId={shareId} /> : <OwnerApp />}
    </DebugProvider>
  );
}
function OwnerApp() {
  // Consume and remove the URL credential before mounting the router or any network consumer.
  const [key, setKey] = useState(initialKey);
  const [input, setInput] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<Schema<"ListenerStatus"> | null>(null);
  const [error, setError] = useState("");
  const [pushWarning, setPushWarning] = useState("");
  const [connecting, setConnecting] = useState(true);
  const hadAccess = useRef(false);
  const [access, setAccess] = useState<"checking" | "login" | "unavailable">(
    "checking",
  );
  const drafts = useRef(new Map<string, SourceDraft>());
  const composers = useRef(new Map<string, ThreadDraft>());
  const [newConversations] = useState(() => {
    const store = new NewDraftStore();
    // Restore before any route mounts, including a saved Thread whose first
    // submission was interrupted by a reload.
    store.get(composers.current);
    return store;
  });
  const files = useRef(new Map<string, FileBuffer>());
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (
        [...files.current.values()].some(
          (entry) => entry.dirty || entry.uncertain || entry.saving,
        )
      )
        event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  const [queries] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, staleTime: 15000 },
          mutations: { retry: false, networkMode: "always" },
        },
      }),
  );
  const transportRef = useRef<ReturnType<typeof createTransport> | null>(null);
  const unauthorized = useCallback(() => {
    void disablePush().catch(() => {
      setPushWarning(PUSH_CLEANUP_WARNING);
    });
    transportRef.current?.close();
    setStatus(null);
    setAccess("login");
    setError(
      hadAccess.current
        ? "Access expired. Enter the API key printed by this server."
        : key
          ? "This instance key wasn't accepted. Check it and try again."
          : "",
    );
    setConnecting(false);
    void queries.cancelQueries();
    queries.clear();
  }, [queries, key]);
  const transport = useMemo(
    () => createTransport(key, unauthorized),
    [key, unauthorized, attempt],
  );
  transportRef.current = transport;
  useEffect(() => {
    let active = true;
    setStatus(null);
    setError("");
    setConnecting(true);
    const controller = new AbortController();
    void result(
      transport.client.GET("/api/status", { signal: controller.signal }),
    )
      .then((next) => {
        if (
          next.api_version !== "1" ||
          typeof next.version !== "string" ||
          !next.version ||
          !next.app ||
          !next.features
        )
          throw new Error(
            "This server returned an incompatible status response.",
          );
        if (!active) return;
        hadAccess.current = true;
        retainKey(key);
        queries.setQueryData(["status"], next);
        setStatus(next);
        setConnecting(false);
      })
      .catch((failure: unknown) => {
        if (active) {
          // The authentication callback owns the initial-login, rejected-key,
          // and expired-access messages; the transport's generic 401 must not
          // replace that context.
          if (failure instanceof ApiError && failure.status === 401) return;
          setAccess((current) =>
            current === "login" ? current : "unavailable",
          );
          setError(
            failure instanceof Error ? failure.message : "Connection failed.",
          );
          setConnecting(false);
        }
      });
    return () => {
      active = false;
      controller.abort();
      transport.close();
    };
  }, [transport, key, queries]);
  const forget = async () => {
    // Keep authentication alive until bounded subscription cleanup finishes.
    setPushWarning("");
    await disablePush(transport).catch(() => {
      setPushWarning(PUSH_CLEANUP_WARNING);
    });
    transport.close();
    hadAccess.current = false;
    retainKey("");
    setInput("");
    setKey("");
    setAccess("login");
    queries.clear();
    setStatus(null);
    setAttempt((value) => value + 1);
  };
  return (
    <QueryClientProvider client={queries}>
      <TransportContext.Provider value={transport}>
        <DraftContext.Provider value={drafts.current}>
          <ComposerDrafts.Provider value={composers.current}>
            <NewConversationDrafts.Provider value={newConversations}>
              <ChildControlsProvider>
                <FileBuffers.Provider value={files.current}>
                  {status ? (
                    <BrowserRouter>
                      <NotificationsProvider>
                        <VoiceProvider>
                          <Workbench
                            status={status}
                            forget={forget}
                            unauthorized={unauthorized}
                          />
                        </VoiceProvider>
                      </NotificationsProvider>
                    </BrowserRouter>
                  ) : access !== "login" ? (
                    <Startup
                      error={error}
                      connecting={connecting}
                      retry={() => {
                        setError("");
                        setConnecting(true);
                        setAccess("checking");
                        setAttempt((value) => value + 1);
                      }}
                    />
                  ) : (
                    <main className={styles.access}>
                      <div className={styles.accessCard}>
                        <div className={styles.accessBrand}>
                          <Logo alt="" width={24} height={24} />
                          <span>Harness</span>
                        </div>
                        <h1>Log in to Harness UI</h1>
                        <p>
                          Use the login key printed by your Harness server, not
                          a model API key.
                        </p>
                        {pushWarning && <p role="alert">{pushWarning}</p>}
                        <form
                          className={styles.stack}
                          onSubmit={(event) => {
                            event.preventDefault();
                            if (!input.trim() || connecting) return;
                            hadAccess.current = false;
                            setKey(input);
                            setAttempt((value) => value + 1);
                          }}
                        >
                          <TextField
                            label="Instance API key"
                            type="password"
                            autoFocus
                            value={input}
                            onChange={setInput}
                          />
                          {error && (
                            <p role="alert" className={styles.accessError}>
                              {error}
                            </p>
                          )}
                          <Button
                            type="submit"
                            loading={connecting}
                            disabled={!input.trim()}
                          >
                            Log in
                          </Button>
                        </form>
                        {drafts.current.size > 0 && (
                          <p>
                            Your edits are still here. Log in without reloading
                            to keep them.
                          </p>
                        )}
                      </div>
                    </main>
                  )}
                </FileBuffers.Provider>
              </ChildControlsProvider>
            </NewConversationDrafts.Provider>
          </ComposerDrafts.Provider>
        </DraftContext.Provider>
      </TransportContext.Provider>
    </QueryClientProvider>
  );
}
