import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, workspaceHeaders } from "../../shared/api";
import { conversationApi } from "./api";

export function useRun(runId?: string | null) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["run", workspace.id, runId],
    enabled: !!runId,
    queryFn: ({ signal }) =>
      conversationApi(client, workspace.id).run(runId!, signal),
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
