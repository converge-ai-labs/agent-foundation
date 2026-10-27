import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";

export function useBuiltinSkills(enabled = true) {
  const client = useClient();
  const { workspace } = useWorkspace();
  return useQuery({
    enabled,
    queryKey: ["builtin-skills", workspace.id],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/builtin-skills", { signal })
        .then(data),
  });
}
