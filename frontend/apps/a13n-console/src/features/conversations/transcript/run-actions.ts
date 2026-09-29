import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, type Schema } from "../../../shared/api";
import { conversationKeys, invalidateConversation, runPath } from "../api";

/**
 * A child Thread is driven by the parent Run that delegated to it; every other
 * Thread takes messages, answers and interrupts from here.
 */
export function isInteractive(thread: Schema["ThreadView"]) {
  return thread.origin !== "child";
}

/** Where an accepted command lands: the Run it started, when it started one. */
export function useRunAcceptance(
  run: Schema["RunView"],
  thread: Schema["ThreadView"],
) {
  const cache = useQueryClient(),
    navigate = useNavigate(),
    { search } = useLocation(),
    { workspace, basePath } = useWorkspace();
  const refresh = () =>
    invalidateConversation(cache, workspace.id, {
      sessionId: run.session_id,
      threadId: thread.id,
      runId: run.id,
    });
  return {
    refresh,
    accepted(
      next: Schema["RunView"] | null,
      updatedThread?: Schema["ThreadView"],
    ) {
      // The receipt already contains this Run; show it before background reads.
      if (next)
        cache.setQueryData(conversationKeys(workspace.id).run(next.id), next);
      if (updatedThread)
        cache.setQueryData(
          conversationKeys(workspace.id).thread(updatedThread.id),
          updatedThread,
        );
      // A successor cannot change its ancestors' lineage or sealed output.
      void invalidateConversation(cache, workspace.id, {
        sessionId: run.session_id,
        threadId: thread.id,
        runId: next?.id ?? run.id,
      });
      if (next)
        navigate(
          runPath(basePath, {
            session_id: next.session_id,
            thread_id: next.thread_id,
            run_id: next.id,
          }) + search,
          { state: { continuedFrom: run.id } },
        );
    },
  };
}

export function useInterruptRun(
  run: Schema["RunView"],
  refresh: () => unknown,
) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/runs/{run_id}/interrupt", {
          params: { path: { run_id: run.id } },
        })
        .then(data),
    onSuccess: () => void refresh(),
    onError: () => void refresh(),
  });
}
