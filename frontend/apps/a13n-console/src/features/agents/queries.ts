import { queryOptions, useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Client } from "../../service-client";
import { allPages, data, type Schema } from "../../shared/api";
import { modelApi } from "../models/api";

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
      client
        .workspace(workspaceId)
        .GET("/api/v1/agents/{agent_id}", {
          params: { path: { agent_id: agentId! } },
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

/** The Service changes only custom agents that are not archived. */
export function editableAgent(
  agent: Pick<Schema["Agent"], "source" | "archived_at">,
) {
  return agent.source === "custom" && !agent.archived_at;
}

/** Revisions name their model by key; views read its display name here. */
export function useModelsByKey() {
  const client = useClient(),
    { workspace } = useWorkspace();
  const query = useQuery({
    queryKey: ["agent-list-models", workspace.id],
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        modelApi(client, workspace.id).models(signal, cursor),
      ),
  });
  return new Map((query.data ?? []).map((model) => [model.key, model]));
}
