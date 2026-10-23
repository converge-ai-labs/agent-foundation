import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { modelApi } from "../models/api";

export function useAgentChoices() {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["agent-choices", workspace.id],
    queryFn: async ({ signal }) => {
      const api = modelApi(client, workspace.id);
      const [models, providers, skills, connections] = await Promise.all([
        allPages((cursor) => api.models(signal, cursor, "", undefined, true)),
        // A model's provider names it in the picker and brands it where its own name does not.
        allPages((cursor) => api.providers(signal, cursor)),
        allPages((cursor) =>
          client
            .workspace(workspace.id)
            .GET("/api/v1/skills", {
              params: { query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
        allPages((cursor) =>
          client
            .workspace(workspace.id)
            .GET("/api/v1/connections", {
              params: { query: { cursor, limit: 100 } },
              signal,
            })
            .then(data),
        ),
      ]);
      return { models, providers, skills, connections };
    },
  });
}
