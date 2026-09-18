import { Button } from "a13n-ui";
import { DownloadSimpleIcon, FileIcon } from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, workspaceHeaders } from "../../shared/api";
import { CopyButton } from "../../shared/identity";
import { downloadBlob } from "../../shared/download";
import { ErrorNotice } from "../../shared/feedback";
import styles from "./attachment.module.css";

export function AssetAttachment({
  assetId,
  filename,
}: {
  assetId: string;
  filename?: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const asset = useQuery({
    queryKey: ["asset", workspace.id, assetId],
    enabled: can("asset.read"),
    staleTime: 60_000,
    retry: false,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/assets/{asset_id}", {
          params: { path: { asset_id: assetId } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
  const name = asset.data?.filename || filename || assetId;
  const download = useMutation({
    mutationFn: async () => {
      const blob = data(
        await client.http.GET("/api/v1/assets/{asset_id}/content", {
          params: { path: { asset_id: assetId } },
          headers: workspaceHeaders(workspace.id),
          parseAs: "blob",
        }),
      );
      downloadBlob(blob, name);
    },
  });
  return (
    <div className={styles.attachment}>
      <FileIcon size={20} aria-hidden="true" />
      <div className={styles.identity}>
        <strong title={name}>{name}</strong>
        <small>
          {asset.data
            ? `${asset.data.media_type} · ${asset.data.size_bytes.toLocaleString()} B`
            : asset.isError
              ? t("File details unavailable")
              : t("Attachment")}
        </small>
      </div>
      <CopyButton value={assetId} iconOnly />
      {can("asset.read") && (
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label={`${t("Download")} ${name}`}
          disabled={!!asset.data?.deleted_at}
          loading={download.isPending}
          onClick={() => download.mutate()}
        >
          <DownloadSimpleIcon size={15} />
        </Button>
      )}
      <ErrorNotice error={download.error} retry={() => download.mutate()} />
    </div>
  );
}
