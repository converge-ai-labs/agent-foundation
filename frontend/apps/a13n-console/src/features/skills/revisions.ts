import { queryOptions } from "@tanstack/react-query";
import type { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import type { RevisionRef } from "./archive";

type Client = ReturnType<typeof useClient>;

/** One published revision, read under its skill. */
export function revisionQuery(client: Client, revision: RevisionRef) {
  return queryOptions({
    queryKey: [
      "skills",
      revision.workspace_id,
      revision.skill_id,
      "revision",
      revision.id,
    ],
    queryFn: ({ signal }) =>
      client
        .workspace(revision.workspace_id)
        .GET("/api/v1/skills/{skill_id}/revisions/{revision_id}", {
          params: {
            path: {
              skill_id: revision.skill_id,
              revision_id: revision.id,
            },
          },
          signal,
        })
        .then(data),
  });
}

/** Published revisions, newest first: the first page leads with the latest version. */
export function revisionsQuery(
  client: Client,
  workspaceId: string,
  skillId: string,
  cursor?: string,
) {
  return queryOptions({
    queryKey: ["skills", workspaceId, skillId, "revisions", cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(workspaceId)
        .GET("/api/v1/skills/{skill_id}/revisions", {
          params: {
            path: { skill_id: skillId },
            query: { cursor },
          },
          signal,
        })
        .then(data),
  });
}
