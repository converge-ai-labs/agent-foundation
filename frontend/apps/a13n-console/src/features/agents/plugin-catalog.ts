import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";

export function usePluginCatalog() {
  const client = useClient();
  const { workspace } = useWorkspace();
  return useQuery({
    queryKey: ["plugin-catalog", workspace.id],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/plugins", { signal })
        .then(data),
  });
}
