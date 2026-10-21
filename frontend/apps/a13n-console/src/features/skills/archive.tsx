import {
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { downloadBlob } from "../../shared/download";

/** Everything needed to reach one published package. */
export interface RevisionRef {
  id: string;
  skill_id: string;
  workspace_id: string;
}

export function archiveQuery(
  client: ReturnType<typeof useClient>,
  revision: RevisionRef,
) {
  return queryOptions({
    queryKey: [
      "skills",
      revision.workspace_id,
      revision.skill_id,
      "archive",
      revision.id,
    ],
    queryFn: async ({ signal }) =>
      new Uint8Array(
        data(
          await client
            .workspace(revision.workspace_id)
            .GET("/api/v1/skills/{skill_id}/revisions/{revision_id}/content", {
              params: {
                path: {
                  skill_id: revision.skill_id,
                  revision_id: revision.id,
                },
              },
              parseAs: "arrayBuffer",
              signal,
            }),
        ),
      ),
    staleTime: Infinity,
    gcTime: 60_000,
  });
}

/**
 * The package bytes are already cached for the file browser, so a download
 * reuses them instead of asking for the archive twice. The file is named
 * after the package and its version.
 */
export function useArchiveDownload() {
  const client = useClient(),
    cache = useQueryClient();
  return useMutation({
    mutationFn: async (revision: Schema["SkillRevision"]) => {
      const bytes = await cache.fetchQuery(archiveQuery(client, revision));
      downloadBlob(
        new Blob([bytes], { type: "application/zip" }),
        `${revision.config.name}-v${revision.number}.zip`,
      );
    },
  });
}
