import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, workspaceHeaders } from "../../shared/api";
import { conversationQueries } from "./api";

export function useRun(runId?: string | null) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    ...conversationQueries(client, workspace.id).run(runId ?? ""),
    enabled: !!runId,
  });
}

export function useRunAgent(agentId?: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["run-agent", workspace.id, agentId],
    enabled: !!agentId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/agents/{agent_id}", {
          params: { path: { agent_id: agentId! } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
}
