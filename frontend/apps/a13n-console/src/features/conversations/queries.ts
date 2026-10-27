import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { conversationQueries } from "./api";

export function useRun(runId?: string | null) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    ...conversationQueries(client, workspace.id).run(runId ?? ""),
    enabled: !!runId,
  });
}

/** The agent a Run executes: its revision with the Run's overrides applied. */
export function useRunAgent(runId: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery(conversationQueries(client, workspace.id).agent(runId));
}

export function useSession(sessionId?: string | null) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    ...conversationQueries(client, workspace.id).session(sessionId ?? ""),
    enabled: !!sessionId,
  });
}

/** Read only this Run's recorded usage, independent of display pages. */
export function useRunUsage(run: Pick<Schema["RunView"], "id" | "session_id">) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    ...conversationQueries(client, workspace.id).usage({
      run_id: run.id,
    }),
    meta: { sessionId: run.session_id },
    select: (summary) => summary.models,
  });
}

/** Fetch timing only when a model request's Debug details are opened. */
export function useRequestMetrics(runId: string, recordId?: string | null) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    ...conversationQueries(client, workspace.id).requestMetrics(
      runId,
      recordId ?? "",
    ),
    enabled: !!recordId,
  });
}

export function useThreadNavigation(sessionId: string, threadId?: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    ...conversationQueries(client, workspace.id).threadPage(sessionId, {
      thread_id: threadId ? [threadId] : [],
    }),
    enabled: !!threadId,
    select: (page) => page.items[0],
  });
}
