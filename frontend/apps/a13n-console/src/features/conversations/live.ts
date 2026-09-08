import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ReplayGapError } from "@converge.ai/a13n";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { conversationApi, isActiveRun } from "./api";
import {
  applyRunEvent,
  compareCursors,
  mergeRetainedItems,
  type PresentedItem,
} from "./projection";

export function useLiveRun(runId: string) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    projection = useRef(new Map<string, PresentedItem>()),
    cursor = useRef<string | undefined>(undefined);
  const [items, setItems] = useState<PresentedItem[]>([]),
    [state, setState] = useState<
      "connecting" | "connected" | "closed" | "disconnected"
    >("connecting"),
    [gap, setGap] = useState(false),
    [error, setError] = useState<unknown>(),
    [generation, setGeneration] = useState(0);
  useEffect(() => {
    const controller = new AbortController(),
      { signal } = controller,
      api = conversationApi(client, workspace.id);
    let frame: number | undefined;
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
    async function reconcile() {
      const [run, retained, pending] = await Promise.all([
        api.run(runId, signal),
        api.retainedItems(runId, signal),
        api.pending(runId, signal),
      ]);
      if (signal.aborted) return { run, available: retained.available };
      cache.setQueryData(["run", workspace.id, runId], run);
      cache.setQueryData(["pending-actions", workspace.id, runId], pending);
      projection.current = mergeRetainedItems(
        projection.current,
        retained.items,
      );
      publish();
      void cache.invalidateQueries({
        queryKey: ["thread", workspace.id, run.thread_id],
      });
      return { run, available: retained.available };
    }
    async function attach() {
      setState("connecting");
      setError(undefined);
      const initial = await reconcile();
      if (
        !signal.aborted &&
        initial.available &&
        !isActiveRun(initial.run.status)
      ) {
        setState("closed");
        return;
      }
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
            projection.current = applyRunEvent(projection.current, entry);
            cursor.current = entry.cursor;
            publish();
            setState("connected");
            if (
              entry.event.event_type.startsWith("run.") ||
              entry.event.event_type.startsWith("run_attempt.")
            ) {
              void cache.invalidateQueries({
                queryKey: ["run", workspace.id, runId],
              });
              void cache.invalidateQueries({
                queryKey: ["thread", workspace.id],
              });
              void cache.invalidateQueries({
                queryKey: ["thread-runs", workspace.id],
              });
              void cache.invalidateQueries({
                queryKey: ["run-attempts", workspace.id, runId],
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
          if (!isActiveRun(latest.run.status)) {
            setState("closed");
            return;
          }
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
          if (error instanceof ReplayGapError && gaps++ < 1) {
            setGap(true);
            cursor.current = undefined;
            await reconcile();
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
  }, [client, workspace.id, runId, cache, generation]);
  return {
    items,
    state,
    gap,
    error,
    reconnect: () => setGeneration((value) => value + 1),
  };
}
