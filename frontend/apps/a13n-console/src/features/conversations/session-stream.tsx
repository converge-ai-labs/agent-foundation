import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useEffectEvent,
  useState,
  type ReactNode,
} from "react";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { applyThreadState, conversationKeys } from "./api";
import { SessionDisplays } from "./session-displays";
import type { SessionEvent } from "../../service-client";

export interface SessionStream {
  displays: SessionDisplays;
  /** `closed` after a refusal or an invalid frame; the stream reconnects from anything else. */
  state: "connecting" | "live" | "reconnecting" | "closed";
  /** Why the stream is reconnecting or closed. */
  error?: unknown;
  /** Open a closed stream again. */
  reconnect: () => void;
}

const Context = createContext<SessionStream | undefined>(undefined);

/**
 * Follow a session while the page shows it: thread changes reach the query
 * cache, and the output of its active runs reaches their displays.
 */
export function SessionStreamProvider({
  sessionId,
  enabled = true,
  onEvent,
  children,
}: {
  sessionId: string;
  enabled?: boolean;
  onEvent?: (event: SessionEvent) => void;
  children: ReactNode;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const [displays] = useState(() => new SessionDisplays());
  const [connection, setConnection] = useState<
    Pick<SessionStream, "state" | "error">
  >({ state: "connecting" });
  const [generation, setGeneration] = useState(0);
  const received = useEffectEvent((event: SessionEvent) => onEvent?.(event));
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController(),
      keys = conversationKeys(workspace.id);
    async function follow() {
      for await (const event of client.streamSession(workspace.id, sessionId, {
        signal: controller.signal,
      })) {
        received(event);
        switch (event.type) {
          case "connection":
            if (event.state === "live")
              for (const runId of displays.connected())
                void cache.invalidateQueries({
                  queryKey: keys.items(runId),
                  exact: true,
                });
            setConnection({ state: event.state, error: event.error });
            break;
          case "thread":
            applyThreadState(cache, workspace.id, event.data);
            break;
          case "items":
            displays.items(event.data);
            break;
          case "delta":
            displays.delta(event.data);
            break;
          case "gap":
            displays.gap(event.data.run_id);
            break;
        }
      }
    }
    follow().catch((error: unknown) => {
      if (controller.signal.aborted) return;
      revalidateSession(error);
      setConnection({ state: "closed", error });
    });
    return () => controller.abort();
  }, [client, workspace.id, sessionId, cache, displays, generation, enabled]);
  useEffect(() => () => displays.close(), [displays]);
  const value = useMemo(
    () => ({
      displays,
      ...connection,
      reconnect: () => setGeneration((value) => value + 1),
    }),
    [displays, connection],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** The stream of the session the page shows, if any. */
export function useSessionStream() {
  return useContext(Context);
}
