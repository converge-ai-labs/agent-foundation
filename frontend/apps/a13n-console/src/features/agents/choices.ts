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
      const [models, skills, connections] = await Promise.all([
        modelApi(client, workspace.id)
          .models(signal, undefined, undefined, undefined, true)
          .then((page) => page.items),
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
      return { models, skills, connections };
    },
  });
}
