import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data } from "../../shared/api";

export function useBrowser(id?: string) {
  const client = useClient();
  const { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["browsers", workspace.id, "detail", id],
    enabled: !!id,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/browsers/{browser_id}", {
          params: { path: { browser_id: id! } },
          signal,
        })
        .then(data),
    refetchInterval: 2000,
  });
}

export function useBrowserOptions(enabled = true) {
  const client = useClient();
  const { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["browsers", workspace.id, "options"],
    enabled,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/browsers", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
}
