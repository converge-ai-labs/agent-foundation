import { Button } from "a13n-ui";
import {
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, workspaceHeaders, type Schema } from "../../shared/api";
import { downloadBlob } from "../../shared/download";
import { ErrorToast } from "../../shared/feedback";

export function archiveQuery(
  client: ReturnType<typeof useClient>,
  revision: Schema["SkillRevision"],
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
          await client.http.GET(
            "/api/v1/skill-revisions/{skill_revision_id}/content",
            {
              params: { path: { skill_revision_id: revision.id } },
              headers: workspaceHeaders(revision.workspace_id),
              parseAs: "arrayBuffer",
              signal,
            },
          ),
        ),
      ),
    staleTime: Infinity,
    gcTime: 60_000,
  });
}

export function DownloadRevision({
  revision,
  filename,
}: {
  revision: Schema["SkillRevision"];
  filename: string;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const download = useMutation({
    mutationFn: async () => {
      const bytes = await cache.fetchQuery(archiveQuery(client, revision));
      downloadBlob(new Blob([bytes], { type: "application/zip" }), filename);
    },
  });
  return (
    <div>
      <Button
        size="sm"
        variant="outline"
        loading={download.isPending}
        onClick={() => download.mutate()}
      >
        {t("Download ZIP")}
      </Button>
      <ErrorToast error={download.error} />
    </div>
  );
}
