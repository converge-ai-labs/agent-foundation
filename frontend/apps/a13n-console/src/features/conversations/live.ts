import { useEarlierMessages } from "./earlier";
import { ReplayGapError } from "../../service-client";
import { isCancelledError, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  conversationKeys,
  conversationQueries,
  invalidateConversation,
} from "./api";
import {
  applyRunEvent,
  interruptOpenItems,
  compareCursors,
  mergeRetainedItems,
  type PresentedItem,
} from "./projection";

export function useLiveRun(runId: string) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    projection = useRef(new Map<string, PresentedItem>()),
    cursor = useRef<string | undefined>(undefined),
    identity = useRef("");
  const [items, setItems] = useState<PresentedItem[]>([]),
    [state, setState] = useState<
      "connecting" | "connected" | "closed" | "disconnected"
    >("connecting"),
    [gap, setGap] = useState(false),
    [incomplete, setIncomplete] = useState(false),
    [error, setError] = useState<unknown>(),
    [generation, setGeneration] = useState(0);
  const earlier = useEarlierMessages(runId, (page) => {
    projection.current = mergeRetainedItems(projection.current, page.items);
    setItems(
      [...projection.current.values()].sort((a, b) =>
        compareCursors(a.firstCursor, b.firstCursor),
      ),
    );
    if (!page.complete || page.recovery_exhausted) {
      setIncomplete(true);
      setGap(true);
    }
  });
  const { resetEarlier } = earlier;
  useEffect(() => {
    const controller = new AbortController(),
      { signal } = controller,
      queries = conversationQueries(client, workspace.id);
    const selected = `${workspace.id}:${runId}`;
    if (identity.current !== selected) {
      identity.current = selected;
      projection.current = new Map();
      cursor.current = undefined;
      setItems([]);
      setGap(false);
      setIncomplete(false);
    }
    let frame: number | undefined;
    let initialized = false;
    let partial = false;
    function publish() {
      if (frame !== undefined) return;
      frame = requestAnimationFrame(() => {
        frame = undefined;
        if (!signal.aborted)
          setItems(
            [...projection.current.values()].sort((a, b) =>
              compareCursors(a.firstCursor, b.firstCursor),
            ),
          );
      });
    }
    async function current<T>(read: () => Promise<T>): Promise<T> {
      for (;;) {
        signal.throwIfAborted();
        try {
          return await read();
        } catch (error) {
          // Notifications may replace a shared query while recovery awaits it.
          // Join its replacement instead of reporting a disconnected stream.
          if (!isCancelledError(error) || signal.aborted) throw error;
        }
      }
    }
    async function reconcile(reset = false) {
      signal.throwIfAborted();
      // Recovery must read current resources even when the display cache is fresh.
      const [run, retained] = await Promise.all([
        current(() =>
          cache.fetchQuery({ ...queries.run(runId), staleTime: 0 }),
        ),
        current(() =>
          cache.fetchQuery({ ...queries.items(runId), staleTime: 0 }),
        ),
        current(() =>
          cache.fetchQuery({ ...queries.pending(runId), staleTime: 0 }),
        ),
      ]);
      if (signal.aborted) return { run, retained };
      if (retained.available) {
        // A new attachment/gap starts a fresh window; ordinary reconciliation
        // keeps pages the reader has already requested.
        if (reset || !initialized) {
          resetEarlier(retained.next_cursor);
          partial = retained.next_cursor !== null;
          initialized = true;
        }
        projection.current = mergeRetainedItems(
          reset ? new Map() : projection.current,
          retained.items,
        );
        if (reset || cursor.current === undefined)
          cursor.current = retained.projection_cursor ?? undefined;
        setIncomplete(
          !retained.complete || retained.recovery_exhausted === true,
        );
        if (!retained.complete || retained.recovery_exhausted) setGap(true);
      }
      publish();
      void cache.invalidateQueries({
        queryKey: conversationKeys(workspace.id).thread(run.thread_id),
      });
      return { run, retained };
    }
    function settled(
      retained: Awaited<ReturnType<typeof reconcile>>["retained"],
    ) {
      if (retained.recovery_exhausted) {
        setIncomplete(true);
        setGap(true);
        if (cursor.current !== undefined) {
          projection.current = interruptOpenItems(
            projection.current,
            cursor.current,
          );
          publish();
        }
        setState("closed");
        return true;
      }
      if (!retained.available) return false;
      const caughtUp =
        retained.projection_cursor === null ||
        (cursor.current !== undefined &&
          compareCursors(cursor.current, retained.projection_cursor) >= 0);
      if (!retained.complete || (retained.finalized && caughtUp)) {
        if (retained.finalized && cursor.current !== undefined) {
          projection.current = interruptOpenItems(
            projection.current,
            cursor.current,
          );
          publish();
        }
        setState(retained.finalized ? "closed" : "disconnected");
        return true;
      }
      return false;
    }
    async function attach() {
      setState("connecting");
      setError(undefined);
      const initial = await reconcile(true);
      if (signal.aborted) return;
      if (settled(initial.retained)) return;
      let gaps = 0;
      while (!signal.aborted) {
        try {
          for await (const entry of client.streamRun(runId, {
            signal,
            after: cursor.current,
            workspaceId: workspace.id,
          })) {
            if (signal.aborted) return;
            // The in-memory projection is committed before advancing our replay checkpoint.
            const { event } = entry;
            // An older, unloaded Item can still be generating. Do not render a
            // fragment without its prefix; its page will provide the full Item.
            const hiddenContinuation =
              partial &&
              event.item_id &&
              !projection.current.has(event.item_id) &&
              !event.event_type.endsWith("_start") &&
              event.payload.item_kind !== "run_output";
            if (!hiddenContinuation)
              projection.current = applyRunEvent(projection.current, entry);
            cursor.current = entry.cursor;
            publish();
            setState("connected");
            if (
              entry.event.event_type.startsWith("run.") ||
              entry.event.event_type.startsWith("run_attempt.")
            ) {
              void invalidateConversation(cache, workspace.id, {
                sessionId: initial.run.session_id,
                threadId: entry.event.thread_id,
                runId,
              });
            }
            if (
              [
                "run.waiting",
                "run.completed",
                "run.failed",
                "run.cancelled",
              ].includes(entry.event.event_type)
            )
              break;
          }
          const latest = await reconcile();
          if (signal.aborted) return;
          if (settled(latest.retained)) return;
          await new Promise<void>((resolve) => {
            const finish = () => {
              clearTimeout(timer);
              signal.removeEventListener("abort", finish);
              resolve();
            };
            const timer = setTimeout(finish, 500);
            signal.addEventListener("abort", finish, { once: true });
            if (signal.aborted) finish();
          });
        } catch (error) {
          if (signal.aborted) return;
          if (error instanceof ReplayGapError && gaps++ < 3) {
            setGap(true);
            const latest = await reconcile(true);
            if (signal.aborted) return;
            if (settled(latest.retained)) return;
            if (!latest.retained.available) throw error;
            continue;
          }
          throw error;
        }
      }
    }
    void attach().catch((error) => {
      if (!signal.aborted) {
        revalidateSession(error);
        setError(error);
        setState("disconnected");
      }
    });
    return () => {
      controller.abort();
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [client, workspace.id, runId, cache, generation, resetEarlier]);
  return {
    ...earlier,
    items,
    state,
    gap,
    incomplete,
    error,
    reconnect: () => setGeneration((value) => value + 1),
  };
}
