import { useEffect, useMemo, useRef, type RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import type { PublishedMessage } from "./transcript";
import { observeSavedResult } from "./result-visibility";

export function useThreadInbox({
  threadId,
  enabled,
  canRead,
  reader,
  blocked,
  savedEntries,
  publications,
}: {
  threadId: string;
  enabled: boolean;
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
    queryKey: ["inbox", "thread-unread", threadId],
    enabled: enabled && !shareId,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/inbox/threads/{thread_id}/unread", {
          params: { path },
          signal,
        }),
      ),
  });
  const { mutate, isPending } = useMutation({
    mutationFn: (itemIds: string[]) =>
      client.POST("/api/inbox/threads/{thread_id}/mark-read", {
        params: { path },
        body: { item_ids: itemIds },
      }),
    retry: false,
    onSuccess: () => queries.invalidateQueries({ queryKey: ["inbox"] }),
  });
  const posts = Array.isArray(data) ? data : undefined;
  const eligibleIds = useMemo(() => {
    if (!posts?.length) return [];
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
          !post.has_transcript ||
          savedCalls.has(post.tool_call_id) ||
          Date.parse(post.created_at) < savedThrough,
      )
      .map((post) => post.item_id);
  }, [posts, savedEntries, publications]);
  const idsKey = eligibleIds.join(" ");
  const attempted = useRef<{ posts: typeof posts; idsKey: string }>(undefined);
  useEffect(() => {
    const element = reader.current;
    if (!element || shareId || !enabled || !canRead || isPending || !idsKey)
      return;
    return observeSavedResult(element, () => {
      if (
        blocked() ||
        (attempted.current?.posts === posts &&
          attempted.current?.idsKey === idsKey)
      )
        return;
      attempted.current = { posts, idsKey };
      mutate(eligibleIds);
    });
  }, [
    reader,
    shareId,
    enabled,
    canRead,
    blocked,
    isPending,
    posts,
    idsKey,
    eligibleIds,
    mutate,
  ]);

  return useMemo(
    () =>
      shareId ? undefined : new Set(posts?.map((post) => post.tool_call_id)),
    [shareId, posts],
  );
}
