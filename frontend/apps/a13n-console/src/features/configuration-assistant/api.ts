import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, representation, workspaceHeaders } from "../../shared/api";

export function useConfigurationThread(threadId: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["configuration-thread", workspace.id, threadId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/configuration-threads/{thread_id}", {
          params: { path: { thread_id: threadId } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
    refetchInterval: 3000,
  });
}

export function useConfigurationDraft(draftId: string) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["configuration-draft", workspace.id, draftId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/configuration-drafts/{draft_id}", {
          params: { path: { draft_id: draftId } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(representation),
    enabled: !!draftId,
    refetchInterval: 3000,
  });
}

export function useAssistantReadiness(target?: string | null) {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["configuration-readiness", workspace.id, target],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace}/configuration-assistant/readiness",
          {
            params: {
              path: { workspace: workspace.id },
              query: { target_agent_id: target ?? undefined },
            },
            headers: workspaceHeaders(workspace.id),
            signal,
          },
        )
        .then(data),
  });
}
