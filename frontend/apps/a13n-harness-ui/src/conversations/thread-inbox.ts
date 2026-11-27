import { useCallback, useEffect, useMemo, useRef, type RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import type { PublishedMessage } from "./transcript";
import { observeSavedResult } from "./result-visibility";

export function useThreadInbox({
  threadId,
  enabled,
  autoMarkRead,
  canRead,
  reader,
  blocked,
  savedEntries,
  publications,
}: {
  threadId: string;
  enabled: boolean;
  autoMarkRead: boolean;
  canRead: boolean;
  reader: RefObject<HTMLElement | null>;
  blocked: () => boolean;
  savedEntries: Schema<"TranscriptEntry">[];
  publications?: PublishedMessage[];
}) {
  const { client, shareId } = useTransport();
  const queries = useQueryClient();
  const path = { thread_id: threadId };
  const { data } = useQuery({
    queryKey: ["inbox", "thread", threadId],
    enabled: enabled && !shareId,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/inbox/threads/{thread_id}", {
          params: { path },
          signal,
        }),
      ),
  });
  const { mutateAsync, isPending } = useMutation({
    mutationFn: (itemIds: string[]) =>
      client.POST("/api/inbox/threads/{thread_id}/mark-read", {
        params: { path },
        body: { item_ids: itemIds },
      }),
    retry: false,
    onSuccess: () => queries.invalidateQueries({ queryKey: ["inbox"] }),
  });
  const posts = data;
  // Explicitly unread items stay unread for the lifetime of this chat view.
  // Consult the fence at dispatch so a refetch cannot race the card action.
  const manualUnread = useRef(new Set<string>());
  const autoRead = useRef<{ ids: string[]; settled: Promise<void> }>(undefined);
  const fenceUnread = useCallback((itemId: string, fenced: boolean) => {
    if (!fenced) {
      manualUnread.current.delete(itemId);
      return;
    }
    manualUnread.current.add(itemId);
    // An already-sent batch must settle before the manual unread write.
    if (autoRead.current?.ids.includes(itemId)) return autoRead.current.settled;
  }, []);
  const eligibleIds = useMemo(() => {
    if (!autoMarkRead || !posts?.length) return [];
    let savedThrough = -Infinity;
    const advance = (timestamp: string | null | undefined) => {
      if (timestamp)
        savedThrough = Math.max(savedThrough, Date.parse(timestamp));
    };
    const savedCalls = new Set<string>();
    for (const entry of savedEntries) {
      advance(entry.timestamp);
      for (const part of entry.parts) {
        if (part.kind === "tool_result" && part.tool_call_id)
          savedCalls.add(part.tool_call_id);
      }
    }
    for (const message of publications ?? []) {
      if (!message.local) advance(message.created_at);
    }
    // The Inbox hint can beat the checkpoint. Older cards can be outside the
    // loaded transcript page; a later saved entry covers them too.
    return posts
      .filter(
        (post) =>
          !post.read_at &&
          !post.done_at &&
          (!post.has_transcript ||
            savedCalls.has(post.tool_call_id) ||
            Date.parse(post.created_at) < savedThrough),
      )
      .map((post) => post.item_id);
  }, [autoMarkRead, posts, savedEntries, publications]);
  const attempted = useRef<{ posts: typeof posts; idsKey: string }>(undefined);
  useEffect(() => {
    if (!autoMarkRead) {
      attempted.current = undefined;
      return;
    }
    const element = reader.current;
    if (
      !element ||
      shareId ||
      !enabled ||
      !canRead ||
      isPending ||
      !eligibleIds.length
    )
      return;
    return observeSavedResult(element, () => {
      const ids = eligibleIds.filter((id) => !manualUnread.current.has(id));
      const idsKey = ids.join(" ");
      if (
        !idsKey ||
        autoRead.current ||
        blocked() ||
        (attempted.current?.posts === posts &&
          attempted.current?.idsKey === idsKey)
      )
        return;
      attempted.current = { posts, idsKey };
      const clear = () => {
        autoRead.current = undefined;
      };
      autoRead.current = { ids, settled: mutateAsync(ids).then(clear, clear) };
    });
  }, [
    reader,
    autoMarkRead,
    shareId,
    enabled,
    canRead,
    blocked,
    isPending,
    posts,
    eligibleIds,
    mutateAsync,
  ]);

  return useMemo(
    () =>
      shareId || !enabled || !posts
        ? undefined
        : {
            posts: new Map(posts.map((post) => [post.tool_call_id, post])),
            fenceUnread,
          },
    [shareId, enabled, posts, fenceUnread],
  );
}
