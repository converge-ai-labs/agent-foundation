import type { Query, QueryClient } from "@tanstack/react-query";
import { invalidateWorkRead } from "./work";

const scheduled = new WeakMap<Query, ReturnType<typeof setTimeout>>();

// Summary and focused channels can describe the same transition. Batch observations,
// and never cancel pagination or an in-flight read to start a duplicate request.
export function scheduleRefresh(
  client: QueryClient,
  matches: (query: Query) => boolean,
) {
  const cache = client.getQueryCache();
  for (const query of cache.getAll().filter(matches)) {
    const [kind, , section, continuation] = query.queryKey;
    // Exact saved sources do not change on reconnect, lifecycle or configuration
    // hints. Detail owns cutover to a new source; failed reads remain retryable.
    if (
      kind === "thread" &&
      (typeof continuation === "string" || continuation === null) &&
      ["history", "turn-history", "inputs", "tasks", "notes"].includes(
        String(section),
      ) &&
      (query.state.data !== undefined || query.state.fetchStatus === "fetching")
    )
      continue;
    invalidateWorkRead(query);
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
  "reconcile" | "lifecycle" | "checkpoint" | "usage" | "children" | "work";
export function refreshThread(
  client: QueryClient,
  threadId: string,
  reason: ThreadRefresh,
) {
  const sections: Record<ThreadRefresh, readonly string[]> = {
    reconcile: [],
    lifecycle: [
      "work",
      "detail",
      "configuration",
      "skills",
      "operation",
      "usage",
      "context-usage",
    ],
    checkpoint: ["work", "detail", "configuration", "usage", "context-usage"],
    usage: ["usage", "context-usage"],
    children: ["work", "children", "child-review"],
    work: ["work"],
  };
  scheduleRefresh(client, (query) => {
    const [kind, id, section] = query.queryKey;
    // Collection changes are coalesced into scoped activity lookups by summary.
    if (kind === "threads") return false;
    if (kind === "child-saved-output")
      return reason === "children" || reason === "reconcile";
    return (
      kind === "thread" &&
      id === threadId &&
      (reason === "reconcile" || sections[reason].includes(String(section)))
    );
  });
}
