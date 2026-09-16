import type { Notification } from "../../service-client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { conversationKeys, invalidateConversation } from "./api";

export function useConversationNotifications(threadId?: string) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    enabled = can("notification.subscribe");
  const [error, setError] = useState<unknown>(),
    [generation, setGeneration] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let closed = false;
    setError(undefined);
    const gap = () => {
      if (!closed)
        void cache.invalidateQueries({
          queryKey: conversationKeys(workspace.id).root,
        });
    };
    // A user-initiated replacement attachment also has an observation gap.
    if (generation > 0) gap();
    const invalidate = (notification: Notification) => {
      if (closed || notification.workspace_id !== workspace.id) return;
      void invalidateConversation(cache, workspace.id, {
        sessionId:
          notification.session_id ??
          (notification.resource_type === "session"
            ? notification.resource_id
            : undefined),
        threadId:
          notification.thread_id ??
          (notification.resource_type === "thread"
            ? notification.resource_id
            : undefined),
        runId:
          notification.run_id ??
          (notification.resource_type === "run"
            ? notification.resource_id
            : undefined),
      });
    };
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
        if (state === "gap") gap();
      },
      onError: (error) => {
        if (!closed) setError(error);
      },
    });
    return () => {
      closed = true;
      attachment.close();
    };
  }, [client, workspace.id, threadId, enabled, cache, generation]);
  return { error, reconnect: () => setGeneration((value) => value + 1) };
}
