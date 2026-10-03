import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { conversationQueries } from "./api";
import { readDisplay } from "./display";

type Item = Schema["Item"];

const NONE: Item[] = [];

/** The most Items one read returns. */
const READ_LIMIT = 500;

export interface EarlierItems {
  /** The Items read so far before the newest window, in ordinal order. */
  items: Item[];
  /** Items before the newest window remain unread. */
  more: boolean;
  loading: boolean;
  error: unknown;
  load: () => void;
}

/**
 * The Items before a Run's newest window, whose first Item has ordinal
 * `first`. Nothing is read until the reader asks; each request reads the page
 * before the earliest Item held. These Items never change, so the Run keeps
 * them as its newest window moves on, and the Items the window leaves behind
 * are read after the latest one held.
 */
export function useEarlierItems(
  runId: string,
  first: number | undefined,
): EarlierItems {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const held = conversationQueries(client, workspace.id).earlierItems(runId);
  const kept = useQuery(held).data ?? NONE;
  const [status, setStatus] = useState<{
    runId: string;
    loading: boolean;
    error?: unknown;
  }>();
  const { loading, error } =
    status?.runId === runId ? status : { loading: false, error: undefined };
  const end = first ?? 1;
  const items = useMemo(
    () => kept.filter((item) => item.ordinal < end),
    [kept, end],
  );
  const earliest = items[0]?.ordinal ?? end;
  const latest = items.at(-1)?.ordinal ?? end - 1;
  const missing = latest < end - 1;
  function load() {
    const window = missing
      ? { after: latest, limit: Math.min(READ_LIMIT, end - 1 - latest) }
      : { before: earliest };
    setStatus({ runId, loading: true });
    readDisplay(client, workspace.id, runId, undefined, window).then(
      (page) => {
        // Only the Items before the newest window are final.
        const final = page.items.filter((item) => item.ordinal < end);
        cache.setQueryData(held.queryKey, (current = []) =>
          inOrdinalOrder([...current, ...final]),
        );
        setStatus({ runId, loading: false });
      },
      (error: unknown) => setStatus({ runId, loading: false, error }),
    );
  }
  // Once read, history stays connected to the newest window as it moves on.
  useEffect(() => {
    if (missing && !loading && !error) load();
  }, [runId, end, latest]);
  return { items, more: earliest > 1 || missing, loading, error, load };
}

function inOrdinalOrder(items: Item[]) {
  const unique = new Map(items.map((item) => [item.ordinal, item]));
  return [...unique.values()].sort((a, b) => a.ordinal - b.ordinal);
}
