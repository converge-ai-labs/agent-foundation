import { useCallback, useEffect, useRef, useState } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { readDisplay, type DisplayRead } from "./display";

export type DisplayPage = Extract<DisplayRead, { available: true }>;

/** Older pages never advance the live stream checkpoint. */
export function useEarlierMessages(
  runId: string,
  onPage: (page: DisplayPage) => void,
) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const next = useRef<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const receive = useRef(onPage);
  const [hasEarlier, setHasEarlier] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [earlierError, setEarlierError] = useState<unknown>();
  useEffect(() => {
    receive.current = onPage;
  }, [onPage]);
  const resetEarlier = useCallback((cursor: string | null) => {
    request.current?.abort();
    request.current = null;
    next.current = cursor;
    setHasEarlier(cursor !== null);
    setLoadingEarlier(false);
    setEarlierError(undefined);
  }, []);
  useEffect(() => {
    resetEarlier(null);
    return () => {
      request.current?.abort();
    };
  }, [client, workspace.id, runId, resetEarlier]);
  const loadEarlier = useCallback(async () => {
    if (!next.current || request.current) return;
    const cursor = next.current;
    const controller = new AbortController();
    request.current = controller;
    setLoadingEarlier(true);
    setEarlierError(undefined);
    try {
      const page = await readDisplay(
        client,
        workspace.id,
        runId,
        controller.signal,
        cursor,
      );
      if (controller.signal.aborted) return;
      if (!page.available) throw new Error("Earlier messages are unavailable.");
      if (page.next_cursor === cursor)
        throw new Error("Item pagination repeated a cursor.");
      receive.current(page);
      next.current = page.next_cursor;
      setHasEarlier(page.next_cursor !== null);
    } catch (error) {
      if (!controller.signal.aborted) setEarlierError(error);
    } finally {
      if (request.current === controller) {
        request.current = null;
        setLoadingEarlier(false);
      }
    }
  }, [client, workspace.id, runId]);
  return {
    hasEarlier,
    loadingEarlier,
    earlierError,
    loadEarlier,
    resetEarlier,
  };
}
