import type { Query, QueryClient } from "@tanstack/react-query";

const scheduled = new WeakMap<Query, ReturnType<typeof setTimeout>>();

// Summary and focused SSE can describe the same transition. Batch observations,
// and never cancel pagination or an in-flight read to start a duplicate request.
export function scheduleRefresh(
  client: QueryClient,
  matches: (query: Query) => boolean,
) {
  const cache = client.getQueryCache();
  for (const query of cache.getAll().filter(matches)) {
    if (scheduled.has(query)) continue;
    scheduled.set(
      query,
      setTimeout(() => {
        void (async () => {
          try {
            await query.promise;
          } catch {
            /* Reconcile after failed reads too. */
          }
          scheduled.delete(query);
          // Logout/access replacement discards this cache; never revive it.
          if (cache.get(query.queryHash) !== query) return;
          await client.invalidateQueries(
            { queryKey: query.queryKey, exact: true },
            { cancelRefetch: false },
          );
        })();
      }, 200),
    );
  }
}

export type ThreadRefresh =
  "reconcile" | "lifecycle" | "checkpoint" | "usage" | "children";
export function refreshThread(
  client: QueryClient,
  threadId: string,
  reason: ThreadRefresh,
) {
  const sections: Record<ThreadRefresh, readonly string[]> = {
    reconcile: [],
    lifecycle: [
      "detail",
      "configuration",
      "skills",
      "operation",
      "usage",
      "context-usage",
    ],
    checkpoint: ["detail", "configuration", "usage", "context-usage"],
    usage: ["usage", "context-usage"],
    children: ["children", "child-review"],
  };
  scheduleRefresh(client, (query) => {
    const [kind, id, section] = query.queryKey;
    if (kind === "threads")
      return (
        reason === "lifecycle" ||
        reason === "checkpoint" ||
        reason === "reconcile"
      );
    if (kind === "child-saved-output")
      return reason === "children" || reason === "reconcile";
    return (
      kind === "thread" &&
      id === threadId &&
      (reason === "reconcile" || sections[reason].includes(String(section)))
    );
  });
}
