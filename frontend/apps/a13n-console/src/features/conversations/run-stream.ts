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
  interruptOpenItems,
  compareCursors,
  mergeRetainedItems,
  type PresentedItem,
} from "./projection";
import {
  applyRun,
  emptyExecution,
  type Execution,
  type ExecutionCoverage,
} from "./execution";

export type { ExecutionCoverage };

export interface RunExecution extends Execution {
  coverage: ExecutionCoverage;
}

/**
 * One stream consumer per Run. Every applied event folds the Item projection
 * and the execution view together, so the two can never disagree.
 *
 * `replay` attaches from the stream origin instead of after the Item
 * snapshot's `projection_cursor`, which is the only way to observe a complete
 * execution history. A replay gap falls back to the snapshot-then-live path
 * and reports the loss instead of hiding it.
 */
export function useRunStream(
  runId: string,
  { replay = false }: { replay?: boolean } = {},
) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    fold = useRef({
      items: new Map<string, PresentedItem>(),
      execution: emptyExecution(),
    }),
    cursor = useRef<string | undefined>(undefined),
    identity = useRef("");
  const [items, setItems] = useState<PresentedItem[]>([]),
    [execution, setExecution] = useState<RunExecution>(() => ({
      ...emptyExecution(),
      coverage: "partial",
    })),
    [state, setState] = useState<
      "connecting" | "connected" | "closed" | "disconnected"
    >("connecting"),
    [gap, setGap] = useState(false),
    [incomplete, setIncomplete] = useState(false),
    [error, setError] = useState<unknown>(),
    [generation, setGeneration] = useState(0);
  const earlier = useEarlierMessages(runId, (page) => {
    fold.current.items = mergeRetainedItems(fold.current.items, page.items);
    setItems(sorted(fold.current.items));
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
      fold.current = { items: new Map(), execution: emptyExecution() };
      cursor.current = undefined;
      setItems([]);
      setGap(false);
      setIncomplete(false);
    } else if (replay) {
      // Switching into replay keeps the Items already delivered — the origin
      // replay deduplicates against them — but the execution view must fold
      // the whole stream again rather than resume from its own checkpoint.
      fold.current = { ...fold.current, execution: emptyExecution() };
    }
    let frame: number | undefined;
    let initialized = false;
    let windowed = false;
    // Coverage facts for this attachment, published through `publish()`.
    let origin = false;
    let lost = false;
    let attached = false;
    let terminal = false;
    function publish() {
      if (frame !== undefined) return;
      frame = requestAnimationFrame(() => {
        frame = undefined;
        if (signal.aborted) return;
        setItems(sorted(fold.current.items));
        setExecution({
          ...fold.current.execution,
          coverage: lost ? "unavailable" : origin ? "complete" : "partial",
        });
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
    /**
     * `join` keeps the reader's window, `restart` rebuilds it from the current
     * snapshot, and `origin` reads the same resources but keeps the projection
     * and cursor empty so the stream itself supplies every event.
     */
    async function reconcile(mode: "join" | "restart" | "origin" = "join") {
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
        if (mode === "origin") {
          resetEarlier(null);
          windowed = false;
          initialized = true;
          cursor.current = undefined;
        } else {
          // A new attachment/gap starts a fresh window; ordinary reconciliation
          // keeps pages the reader has already requested.
          if (mode === "restart" || !initialized) {
            resetEarlier(retained.next_cursor);
            windowed = retained.next_cursor !== null;
            initialized = true;
          }
          fold.current.items = mergeRetainedItems(
            mode === "restart" ? new Map() : fold.current.items,
            retained.items,
          );
          if (mode === "restart" || cursor.current === undefined)
            cursor.current = retained.projection_cursor ?? undefined;
        }
        setIncomplete(
          !retained.complete || retained.recovery_exhausted === true,
        );
        if (!retained.complete || retained.recovery_exhausted) {
          setGap(true);
          lost = true;
        }
      }
      publish();
      void cache.invalidateQueries({
        queryKey: conversationKeys(workspace.id).thread(run.thread_id),
      });
      return { run, retained };
    }
    /**
     * A finalized Item snapshot ends ordinary delivery, but it says nothing
     * about execution history: an origin replay still has to run before the
     * finalized snapshot can close the consumer.
     */
    function settled(
      retained: Awaited<ReturnType<typeof reconcile>>["retained"],
      awaitingReplay = false,
    ) {
      // Terminal display recovery has expired: no stream can fill the gap,
      // so the consumer closes on whatever the snapshot kept.
      if (retained.recovery_exhausted) {
        setIncomplete(true);
        setGap(true);
        lost = true;
        if (cursor.current !== undefined)
          fold.current.items = interruptOpenItems(
            fold.current.items,
            cursor.current,
          );
        publish();
        setState("closed");
        return true;
      }
      if (!retained.available) return false;
      const caughtUp =
        retained.projection_cursor === null ||
        (cursor.current !== undefined &&
          compareCursors(cursor.current, retained.projection_cursor) >= 0);
      if (
        !retained.complete ||
        (retained.finalized && caughtUp && !awaitingReplay)
      ) {
        if (retained.finalized && cursor.current !== undefined) {
          fold.current.items = interruptOpenItems(
            fold.current.items,
            cursor.current,
          );
        }
        // A stream that ended without its terminal observation cannot
        // establish a complete execution history.
        if (attached && !terminal) lost = true;
        publish();
        setState(retained.finalized ? "closed" : "disconnected");
        return true;
      }
      return false;
    }
    async function attach() {
      setState("connecting");
      setError(undefined);
      origin = replay;
      const initial = await reconcile(replay ? "origin" : "restart");
      if (signal.aborted) return;
      if (settled(initial.retained, replay)) return;
      let gaps = 0;
      while (!signal.aborted) {
        try {
          attached = true;
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
              windowed &&
              event.item_id &&
              !fold.current.items.has(event.item_id) &&
              !event.event_type.endsWith("_start") &&
              event.payload.item_kind !== "run_output";
            if (!hiddenContinuation)
              fold.current = applyRun(fold.current, entry);
            cursor.current = entry.cursor;
            publish();
            setState("connected");
            if (
              event.event_type.startsWith("run.") ||
              event.event_type.startsWith("run_attempt.")
            ) {
              void invalidateConversation(cache, workspace.id, {
                sessionId: initial.run.session_id,
                threadId: event.thread_id,
                runId,
              });
            }
            if (
              [
                "run.waiting",
                "run.completed",
                "run.failed",
                "run.cancelled",
              ].includes(event.event_type)
            ) {
              terminal = true;
              break;
            }
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
            // Retention no longer reaches our checkpoint: history is lost, and
            // the snapshot path is the only way to continue.
            lost = true;
            origin = false;
            const latest = await reconcile("restart");
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
  }, [client, workspace.id, runId, cache, generation, replay, resetEarlier]);
  return {
    ...earlier,
    items,
    execution,
    state,
    gap,
    incomplete,
    error,
    reconnect: () => setGeneration((value) => value + 1),
  };
}

function sorted(items: ReadonlyMap<string, PresentedItem>) {
  return [...items.values()].sort((a, b) =>
    compareCursors(a.firstCursor, b.firstCursor),
  );
}
