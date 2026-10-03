import { useInfiniteQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { conversationQueries } from "./api";

export interface EarlierItems {
  /** The Items read so far before the newest read, in ordinal order. */
  items: Schema["Item"][];
  /** Earlier Items remain unread. */
  more: boolean;
  loading: boolean;
  error: unknown;
  load: () => void;
}

/**
 * The Items before a Run's newest read, whose first Item has ordinal `first`.
 * Nothing is read until the reader asks; each request reads the page before
 * the earliest Item read so far.
 */
export function useEarlierItems(
  runId: string,
  first: number | undefined,
): EarlierItems {
  const client = useClient(),
    { workspace } = useWorkspace();
  const [requested, setRequested] = useState<string>();
  const before = first ?? 1;
  const query = useInfiniteQuery({
    ...conversationQueries(client, workspace.id).earlierItems(runId, before),
    enabled: requested === runId && before > 1,
  });
  const items = useMemo(
    () =>
      [...(query.data?.pages ?? [])].reverse().flatMap((page) => page.items),
    [query.data],
  );
  return {
    items,
    more: (items[0]?.ordinal ?? before) > 1,
    loading: query.isFetching,
    error: query.error,
    load: () => {
      if (requested !== runId) setRequested(runId);
      else void query.fetchNextPage();
    },
  };
}
