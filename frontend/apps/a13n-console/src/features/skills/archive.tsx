import { Button } from "a13n-ui";
import { DownloadSimpleIcon } from "@phosphor-icons/react";
import {
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, workspaceHeaders } from "../../shared/api";
import { downloadBlob } from "../../shared/download";
import { ErrorToast } from "../../shared/feedback";

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

/**
 * The package bytes are already cached for the file browser, so a download
 * reuses them instead of asking for the archive twice.
 */
export function useArchiveDownload(revision: RevisionRef, filename: string) {
  const client = useClient(),
    cache = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const bytes = await cache.fetchQuery(archiveQuery(client, revision));
      downloadBlob(new Blob([bytes], { type: "application/zip" }), filename);
    },
  });
}

export function DownloadRevision({
  revision,
  filename,
}: {
  revision: RevisionRef;
  filename: string;
}) {
  const { t } = useTranslation();
  const download = useArchiveDownload(revision, filename);
  return (
    <>
      <Button
        size="sm"
        variant="ghost"
        loading={download.isPending}
        onClick={() => download.mutate()}
      >
        <DownloadSimpleIcon size={14} aria-hidden="true" />
        {t("Download ZIP")}
      </Button>
      <ErrorToast error={download.error} />
    </>
  );
}
