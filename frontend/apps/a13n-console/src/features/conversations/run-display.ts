import type { ThreadDelta } from "../../service-client";
import { isCancelledError, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { conversationQueries, invalidateConversation } from "./api";
import { isOmitted } from "./display";
import { RunDisplayState } from "./run-display-state";
import {
  emptyExecution,
  runExecution,
  type Execution,
  type ExecutionCoverage,
} from "./execution";
import { presentItems, type PresentedItem } from "./projection";

export interface RunExecution extends Execution {
  coverage: ExecutionCoverage;
}

/** How long an active Run may go unconfirmed while its Thread reports no change. */
const SEAL_CHECK_MS = 10_000;

/**
 * One consumer per Run. The committed display is read as the Service returned
 * it, and the Thread stream's deltas change its Items after the display's
 * position; the Items and the execution view are both read from that one
 * display, so they can never disagree.
 *
 * `live` follows the Thread stream while the page shows this Run: the Run's
 * own output while it is active, and the Thread's changes at any time. An
 * attempt reset discards provisional output; a gap heals when a saved display
 * covers its missing range. Connection retries keep the contiguous local suffix.
 */
export function useRunDisplay(
  runId: string,
  { live = false }: { live?: boolean } = {},
) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    identity = useRef(""),
    retained = useRef(new RunDisplayState());
  const [items, setItems] = useState<PresentedItem[]>([]),
    [execution, setExecution] = useState<RunExecution>(() => ({
      ...emptyExecution(),
      coverage: "partial",
    })),
    [attempts, setAttempts] = useState<Schema["AttemptView"][]>([]),
    [state, setState] = useState<
      "connecting" | "connected" | "closed" | "disconnected"
    >("connecting"),
    [gap, setGap] = useState(false),
    [incomplete, setIncomplete] = useState(false),
    [dropped, setDropped] = useState(0),
    [error, setError] = useState<unknown>(),
    [generation, setGeneration] = useState(0);
  useEffect(() => {
    const controller = new AbortController(),
      { signal } = controller,
      queries = conversationQueries(client, workspace.id);
    const selected = `${workspace.id}:${runId}`;
    if (identity.current !== selected) {
      identity.current = selected;
      retained.current = new RunDisplayState();
      setItems([]);
      setAttempts([]);
      setGap(false);
      setIncomplete(false);
      setDropped(0);
    }
    const buffer = retained.current;
    let read = buffer.read;
    let known: Schema["AttemptView"][] = [];
    const asked = new Set<number>();
    let omitted = false;
    let frame: number | undefined;
    function publish() {
      if (frame !== undefined) return;
      frame = requestAnimationFrame(() => {
        frame = undefined;
        if (signal.aborted || !read) return;
        const current = [...buffer.items.values()];
        setItems(presentItems(current));
        setAttempts(known);
        setExecution({
          ...runExecution(read.run, current),
          // Items the display dropped took their execution facts with them.
          coverage:
            omitted || buffer.incomplete || !!read.dropped
              ? "partial"
              : "complete",
        });
      });
    }
    async function current<T>(read: () => Promise<T>): Promise<T> {
      for (;;) {
        signal.throwIfAborted();
        try {
          return await read();
        } catch (error) {
          // Thread changes may replace a shared query while recovery awaits it.
          // Join its replacement instead of reporting a disconnected stream.
          if (!isCancelledError(error) || signal.aborted) throw error;
        }
      }
    }
    /** Read the display again, with the output it does not cover yet. */
    async function reconcile(discard = false) {
      const [next, list] = await Promise.all([
        current(() =>
          cache.fetchQuery({ ...queries.items(runId), staleTime: 0 }),
        ),
        current(() =>
          cache.fetchQuery({ ...queries.attempts(runId), staleTime: 0 }),
        ),
      ]);
      signal.throwIfAborted();
      read = next;
      known = list;
      cache.setQueryData(queries.run(runId).queryKey, next.run);
      buffer.reconcile(
        next,
        Math.max(0, ...list.map((attempt) => attempt.number)),
        discard,
      );
      omitted = next.items.some((item) => isOmitted(item.content));
      setIncomplete(omitted);
      setGap(omitted || buffer.incomplete);
      setDropped(next.dropped);
      publish();
      return next;
    }
    async function receive(delta: ThreadDelta, cursor: string) {
      if (delta.attempt < buffer.attempt || read?.complete) return;
      if (
        !known.some((attempt) => attempt.number === delta.attempt) &&
        !asked.has(delta.attempt)
      ) {
        asked.add(delta.attempt);
        await reconcile();
      }
      const refresh = buffer.receive(delta, cursor);
      setGap(omitted || buffer.incomplete);
      publish();
      if (refresh) await reconcile();
    }
    async function follow(threadId: string) {
      // The Thread may have moved on between the display read and the stream
      // attachment; its Run reports a seal the stream would never announce.
      const check = setInterval(() => {
        if (!read || read.complete) return;
        void current(() =>
          cache.fetchQuery({ ...queries.run(runId), staleTime: 0 }),
        )
          .then((run) => (run.sealed_at ? reconcile() : undefined))
          .catch(() => undefined);
      }, SEAL_CHECK_MS);
      try {
        for await (const next of client.streamThread(workspace.id, threadId, {
          signal,
          resume: () => buffer.resume(runId),
        })) {
          if (signal.aborted) return;
          if (next.type === "changed") {
            void invalidateConversation(cache, workspace.id, {
              sessionId: read?.run.session_id,
              threadId,
            });
            // Sealing this Run is one of the changes a Thread reports.
            if (!read?.complete) {
              const thread = await current(() =>
                cache.fetchQuery({ ...queries.thread(threadId), staleTime: 0 }),
              );
              if (thread.current_run_id !== runId) await reconcile();
            }
          } else if (next.type === "delta") {
            if (next.delta.run_id === runId)
              await receive(next.delta, next.cursor);
          } else if (next.run_id !== runId) continue;
          else if (next.type === "reset") {
            await reconcile(true);
          } else if (next.type === "gap") {
            if (buffer.gap(next.position)) await reconcile();
          } else if (
            buffer.boundary(next.attempt, next.sequence, next.cursor)
          ) {
            await reconcile();
          }
          setGap(omitted || buffer.incomplete);
          publish();
          setState(read?.complete ? "closed" : "connected");
        }
      } finally {
        clearInterval(check);
      }
    }
    async function attach() {
      setState("connecting");
      setError(undefined);
      const first = await reconcile();
      setState(first.complete ? "closed" : "connecting");
      if (!live) return;
      await follow(first.run.thread_id);
      if (!signal.aborted) setState("disconnected");
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
  }, [client, workspace.id, runId, cache, generation, live]);
  return {
    items,
    execution,
    attempts,
    state,
    gap,
    incomplete,
    /** The earliest Items the display dropped over its item limit. */
    dropped,
    error,
    reconnect: () => setGeneration((value) => value + 1),
  };
}
