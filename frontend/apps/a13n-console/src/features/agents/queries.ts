import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, workspaceHeaders } from "../../shared/api";

export function useAgent(agentId?: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["agent-by-id", workspace.id, agentId],
    enabled: !!agentId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/agents/{agent}", {
          params: { path: { workspace: workspace.id, agent: agentId! } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
}
