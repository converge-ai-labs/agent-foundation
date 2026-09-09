import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";
export function useAccountProviders() {
  const client = useClient(),
    { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["account-provider-types", workspace.id],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace}/application-account-provider-types",
          { params: { path: { workspace: workspace.id } }, signal },
        )
        .then(data),
  });
}
export function useReceptionOptions(includeServiceAccounts: boolean) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const agents = useQuery({
    queryKey: ["agent-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/agents", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const accounts = useQuery({
    queryKey: ["service-account-options", workspace.id],
    enabled: includeServiceAccounts,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/service-accounts", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  return { agents, accounts };
}
