import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, type Schema } from "../../../shared/api";
import { invalidateConversation, runPath } from "../api";

/**
 * A child Thread is driven by the parent Run that delegated to it; every other
 * Thread takes messages, answers and interrupts from here.
 */
export function isInteractive(thread: Schema["ThreadView"]) {
  return thread.origin !== "child";
}

/**
 * A wait of questions alone takes the next message as its answer; any other
 * wait continues only by resuming.
 */
export function questionsOnly(actions: readonly Schema["PendingItem"][]) {
  return actions.every((action) => action.kind === "user_input");
}

/** Where an accepted command lands: the Run it started, when it started one. */
export function useRunAcceptance(
  run: Schema["RunView"],
  thread: Schema["ThreadView"],
) {
  const cache = useQueryClient(),
    navigate = useNavigate(),
    { workspace, basePath } = useWorkspace();
  const refresh = () =>
    invalidateConversation(cache, workspace.id, {
      sessionId: run.session_id,
      threadId: thread.id,
      runId: run.id,
    });
  return {
    refresh,
    accepted(next: Schema["RunView"] | null) {
      void refresh();
      if (next)
        navigate(
          runPath(basePath, {
            session_id: next.session_id,
            thread_id: next.thread_id,
            run_id: next.id,
          }),
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
