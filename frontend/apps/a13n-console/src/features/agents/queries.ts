import { queryOptions, useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Client } from "../../service-client";
import { data, workspaceHeaders } from "../../shared/api";

/** One Agent by ID, for a single read or for several at once. */
export function agentQuery(
  client: Client,
  workspaceId: string,
  agentId?: string,
) {
  return queryOptions({
    queryKey: ["agent-by-id", workspaceId, agentId],
    enabled: !!agentId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/agents/{agent}", {
          params: { path: { workspace: workspaceId, agent: agentId! } },
          headers: workspaceHeaders(workspaceId),
          signal,
        })
        .then(data),
  });
}

export function useAgent(agentId?: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery(agentQuery(client, workspace.id, agentId));
}
