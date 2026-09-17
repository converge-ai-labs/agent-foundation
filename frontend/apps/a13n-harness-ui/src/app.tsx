import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowserRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { createTransport, result, type Schema } from "./transport/client";
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
  "Background notifications could not be disabled. Task previews may still arrive. Block notifications in this site's browser settings.";
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
  // Consume and remove the URL credential before mounting the router or any network consumer.
  const [key, setKey] = useState(initialKey);
  const [input, setInput] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<Schema<"ListenerStatus"> | null>(null);
  const [error, setError] = useState("");
  const [pushWarning, setPushWarning] = useState("");
  const [connecting, setConnecting] = useState(true);
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
    setError("Access expired. Enter the API key printed by this server.");
    setConnecting(false);
    void queries.cancelQueries();
    queries.clear();
  }, [queries]);
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
        retainKey(key);
        queries.setQueryData(["status"], next);
        setStatus(next);
        setConnecting(false);
      })
      .catch((failure: unknown) => {
        if (active) {
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
    retainKey("");
    setInput("");
    setKey("");
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
                        <Workbench
                          status={status}
                          forget={forget}
                          unauthorized={unauthorized}
                        />
                      </NotificationsProvider>
                    </BrowserRouter>
                  ) : (
                    <main className={styles.access}>
                      <div className={styles.accessCard}>
                        <span className={styles.brandMark}>a13n</span>
                        <h1>Log in to Harness UI</h1>
                        <p>
                          Use the instance API key printed by your server.
                          Provider accounts and model keys are configured after
                          connecting.
                        </p>
                        <p role="status">
                          {error ||
                            (connecting
                              ? "Connecting to server…"
                              : "Enter your instance key.")}
                        </p>
                        {pushWarning && <p role="alert">{pushWarning}</p>}
                        <form
                          className={styles.stack}
                          onSubmit={(event) => {
                            event.preventDefault();
                            setKey(input);
                            setAttempt((value) => value + 1);
                          }}
                        >
                          <TextField
                            label="API key"
                            type="password"
                            value={input}
                            onChange={setInput}
                          />
                          <Button type="submit" loading={connecting}>
                            Log in
                          </Button>
                        </form>
                        {drafts.current.size > 0 && (
                          <p>
                            Your unsaved changes are kept in this tab. Log in
                            again without reloading to continue editing.
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
