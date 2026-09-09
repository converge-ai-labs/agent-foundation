import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";

export function useAgentChoices() {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["agent-choices", workspace.id],
    queryFn: async ({ signal }) => {
      const path = { workspace_id: workspace.id };
      const [models, skills, mcp, connectors] = await Promise.all([
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/models", {
              params: { path, query: { cursor, limit: 100, enabled: true } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/skills", {
              params: { path, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/mcp-connections", {
              params: { path, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client.http
            .GET("/api/v1/workspaces/{workspace_id}/connector-connections", {
              params: { path, query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
      ]);
      return { models, skills, mcp, connectors };
    },
  });
}
