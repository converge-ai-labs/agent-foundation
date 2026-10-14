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

/** Share the Session's recorded usage, independent of loaded display pages. */
export function useRunUsage(run: Pick<Schema["RunView"], "id" | "session_id">) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    ...conversationQueries(client, workspace.id).usage({
      session_id: run.session_id,
    }),
    select: (summary) =>
      summary.runs.find((usage) => usage.run_id === run.id)?.models ?? [],
  });
}
