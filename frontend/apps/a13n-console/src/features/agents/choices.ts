import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
import { modelApi } from "../models/api";

export function useAgentChoices() {
  const client = useClient(),
    { workspace } = useWorkspace();
  const api = modelApi(client, workspace.id);
  const models = useQuery({
    queryKey: ["agent-choices", workspace.id, "models"],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.models(signal, cursor, "", undefined, true)),
  });
  const providers = useQuery({
    queryKey: ["agent-choices", workspace.id, "providers"],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const skills = useQuery({
    queryKey: ["agent-choices", workspace.id, "skills"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/skills", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
  const connections = useQuery({
    queryKey: ["agent-choices", workspace.id, "connections"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/connections", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
  return {
    data: {
      models: models.data ?? [],
      providers: providers.data ?? [],
      skills: skills.data ?? [],
      connections: connections.data ?? [],
    },
    models,
    providers,
    skills,
    connections,
  };
}
