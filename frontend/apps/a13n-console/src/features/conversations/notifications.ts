import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";

const runtimeQueries = new Set([
  "sessions",
  "session-threads",
  "thread",
  "thread-runs",
  "thread-queue",
  "run",
  "pending-actions",
  "run-attempts",
]);
export function useConversationNotifications(threadId?: string) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    enabled = can("notification.subscribe");
  const [error, setError] = useState<unknown>(),
    [generation, setGeneration] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    setError(undefined);
    const invalidate = () =>
      void cache.invalidateQueries({
        predicate: (query) =>
          query.queryKey[1] === workspace.id &&
          runtimeQueries.has(String(query.queryKey[0])),
      });
    const attachment = client.notifications({
      subscriptions: [
        {
          subscription_id: "console-conversation",
          scope: threadId ? "thread" : "workspace",
          resource_id: threadId ?? workspace.id,
          topics: threadId
            ? ["thread.updated", "run.updated", "pending_action.updated"]
            : ["session.updated", "thread.updated", "run.updated"],
        },
      ],
      onNotification: invalidate,
      onState: (state) => {
        if (state === "gap") invalidate();
      },
      onError: setError,
    });
    return () => attachment.close();
  }, [client, workspace.id, threadId, enabled, cache, generation]);
  return { error, reconnect: () => setGeneration((value) => value + 1) };
}
