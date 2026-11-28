import {
  keepPreviousData,
  useInfiniteQuery,
  useQueries,
} from "@tanstack/react-query";
import { useMemo, useState, useSyncExternalStore } from "react";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { isRecord, type Client } from "../../../service-client";
import { conversationQueries } from "../api";
import { isOmitted, readDisplay, type DisplayItem } from "../display";
import { parseItemValue, presentItem, publishedAsset } from "../projection";
import type { HeldDisplay } from "../session-displays";
import { useSessionStream } from "../session-stream";
import { legacyPublication } from "../transcript/items";
import type { FileReference } from "./assets";

const BATCH = 5;

function hasOmittedFileItems(items: readonly DisplayItem[]) {
  return items.some(
    ({ kind, content }) =>
      (kind === "observation" || kind === "tool_call") && isOmitted(content),
  );
}

export function attachedFiles(input: unknown): FileReference[] {
  return isRecord(input) && Array.isArray(input.content)
    ? input.content.flatMap((part) =>
        isRecord(part) &&
        part.type === "asset" &&
        typeof part.asset_id === "string"
          ? [{ assetId: part.asset_id }]
          : [],
      )
    : [];
}

export function publishedFiles(
  runId: string,
  items: readonly DisplayItem[],
): FileReference[] {
  return items.flatMap((raw) => {
    if (raw.kind === "text_message") {
      const item = presentItem(raw);
      return (item.realtimeAssetIds ?? []).map((assetId) => ({ assetId }));
    }
    if (raw.kind !== "observation" && raw.kind !== "tool_call") return [];
    const item = presentItem(raw);
    const observed = publishedAsset(raw);
    const publication =
      observed ??
      (raw.kind === "tool_call" && item.display !== false
        ? legacyPublication({
            kind: "tool",
            name: item.toolName,
            state: item.state,
            result: parseItemValue(item.result, item.incomplete),
            failure: item.failure,
            toolCallId: item.toolCallId,
            subagentRunId: item.subagentRunId,
          })
        : undefined);
    return publication
      ? [
          {
            assetId: publication.assetId,
            publication: {
              runId,
              toolCallId: publication.toolCallId,
              legacy: !observed,
            },
          },
        ]
      : [];
  });
}

/** Index older Item windows without expanding or mutating the Chat history. */
async function olderPublications(
  client: Client,
  workspaceId: string,
  runId: string,
  before: number,
  signal: AbortSignal,
) {
  const files: FileReference[] = [];
  let after = 0;
  let incomplete = false;
  while (after < before - 1) {
    const page = await readDisplay(client, workspaceId, runId, signal, {
      after,
      limit: Math.min(200, before - 1 - after),
    });
    signal.throwIfAborted();
    const last = page.items.at(-1)?.ordinal;
    if (last === undefined || last <= after)
      throw new Error("Earlier run content could not be loaded.");
    files.push(...publishedFiles(runId, page.items));
    incomplete ||= hasOmittedFileItems(page.items);
    after = last;
  }
  return { files, incomplete };
}

const noop = () => () => {};

export function useConversationFiles(runId: string) {
  const client = useClient();
  const { workspace } = useWorkspace();
  const queries = conversationQueries(client, workspace.id);
  const displays = useSessionStream()?.displays;
  const [limit, setLimit] = useState(BATCH);
  const lineage = useInfiniteQuery({
    ...queries.lineage(runId),
    enabled: !!runId,
    placeholderData: keepPreviousData,
  });
  const ids = [
    ...(runId ? [runId] : []),
    ...(lineage.data?.pages
      .flatMap((page) => page.items.map((entry) => entry.id))
      .filter((id) => id !== runId) ?? []),
  ];
  const visible = ids.slice(0, limit);
  // Keep listening to loaded Runs when the Thread starts its next turn.
  const visibleKey = visible.join(":");
  const displayStore = useMemo(() => {
    const ids = visibleKey ? visibleKey.split(":") : [];
    let snapshot: (HeldDisplay | undefined)[] = [];
    return {
      subscribe: (listener: () => void) => {
        if (!displays) return noop();
        const stop = ids.map((id) => displays.subscribe(id, listener));
        return () => stop.forEach((unsubscribe) => unsubscribe());
      },
      getSnapshot: () => {
        const next = ids.map((id) => displays?.get(id));
        if (
          next.length !== snapshot.length ||
          next.some((held, index) => held !== snapshot[index])
        )
          snapshot = next;
        return snapshot;
      },
    };
  }, [displays, visibleKey]);
  const live = useSyncExternalStore(
    displayStore.subscribe,
    displayStore.getSnapshot,
  );
  const runs = useQueries({ queries: visible.map((id) => queries.items(id)) });
  const older = useQueries({
    queries: runs.map(({ data }, index) => {
      const id = visible[index]!;
      const first = data?.items[0]?.ordinal ?? 1;
      return {
        queryKey: ["older-conversation-publications", workspace.id, id, first],
        enabled: first > 1,
        staleTime: Infinity,
        queryFn: ({ signal }: { signal: AbortSignal }) =>
          olderPublications(client, workspace.id, id, first, signal),
      };
    }),
  });
  let incomplete = false;
  const files = runs.flatMap(({ data }, index) => {
    const id = visible[index]!;
    const held = live[index];
    const liveItems = held?.items ?? [];
    const savedItems = data?.items ?? [];
    const firstLive = liveItems[0]?.ordinal ?? Infinity;
    const items = [
      ...savedItems.filter((item) => item.ordinal < firstLive),
      ...liveItems,
    ];
    const prefix = older[index]?.data;
    incomplete ||=
      !!held?.partial || hasOmittedFileItems(items) || !!prefix?.incomplete;
    return [
      ...attachedFiles(data?.run.input),
      ...publishedFiles(id, items),
      ...(prefix?.files ?? []),
    ];
  });
  const failed = [...runs, ...older].find((query) => query.isError);
  return {
    files,
    incomplete,
    loading:
      !!runId &&
      (lineage.isPending ||
        lineage.isPlaceholderData ||
        lineage.isFetchingNextPage ||
        runs.some((query) => query.isPending) ||
        older.some((query) => query.isLoading)),
    error: lineage.error ?? failed?.error,
    retry: () => {
      if (lineage.isError) void lineage.refetch();
      if (failed) void failed.refetch();
    },
    hasOlder: ids.length > limit || lineage.hasNextPage,
    loadOlder: async () => {
      if (
        ids.length <= limit &&
        (await lineage.fetchNextPage()).isFetchNextPageError
      )
        return;
      setLimit((current) => current + BATCH);
    },
  };
}
