import type { InfiniteData, QueryClient } from "@tanstack/react-query";
import type { Schema } from "../transport/client";

type Thread = Schema<"ThreadSummary">;

// Metadata and configuration have independent server versions. A late read or
// mutation response must not roll either back, nor replace unversioned run state.
export function mergeThreadSelections(thread: Thread, known?: Thread): Thread {
  if (!known || thread.thread_id !== known.thread_id) return thread;
  const metadata = known.metadata_version > thread.metadata_version;
  const configuration =
    known.configuration.version > thread.configuration.version;
  if (!metadata && !configuration) return thread;
  return {
    ...thread,
    ...(metadata
      ? {
          title: known.title,
          archived: known.archived,
          metadata_version: known.metadata_version,
        }
      : {}),
    ...(configuration ? { configuration: known.configuration } : {}),
    updated_at:
      known.updated_at > thread.updated_at
        ? known.updated_at
        : thread.updated_at,
  };
}

export function retainThreadSelections(client: QueryClient, thread: Thread) {
  return mergeThreadSelections(
    thread,
    client.getQueryData<Schema<"ThreadDetail">>([
      "thread",
      thread.thread_id,
      "detail",
    ])?.thread,
  );
}

// Publish only confirmed, versioned fields. Activity, continuations, pagination,
// membership and configuration provenance remain owned by their ordinary reads.
export function applyThreadMutation(client: QueryClient, updated: Thread) {
  client.setQueryData<Schema<"ThreadDetail">>(
    ["thread", updated.thread_id, "detail"],
    (current) =>
      current
        ? { ...current, thread: mergeThreadSelections(current.thread, updated) }
        : current,
  );
  for (const query of client
    .getQueryCache()
    .findAll({ queryKey: ["threads"] })) {
    const current = query.state.data as
      InfiniteData<Schema<"ThreadActivityPage">> | Thread[] | undefined;
    if (Array.isArray(current)) {
      client.setQueryData(
        query.queryKey,
        current.map((thread) => mergeThreadSelections(thread, updated)),
      );
    } else if (current?.pages) {
      const update = (row: Schema<"ThreadActivityView">) => {
        const thread = mergeThreadSelections(row.thread, updated);
        return thread === row.thread ? row : { ...row, thread };
      };
      client.setQueryData(query.queryKey, {
        ...current,
        pages: current.pages.map((page) => ({
          ...page,
          rows: page.rows.map(update),
          active_rows: page.active_rows?.map(update),
        })),
      });
    }
  }
}
