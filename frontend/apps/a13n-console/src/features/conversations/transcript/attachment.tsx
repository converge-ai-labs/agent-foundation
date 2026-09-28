import { Button } from "a13n-ui";
import {
  DownloadSimpleIcon,
  FileIcon,
  PaperclipIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data } from "../../../shared/api";
import { CopyButton } from "../../../shared/identity";
import { downloadBlob } from "../../../shared/download";
import { ErrorNotice } from "../../../shared/feedback";
import styles from "./transcript.module.css";

/** One chip presents every attachment, in the composer and in the transcript. */
export function AttachmentChip({
  label,
  secondary,
  icon,
  actions,
}: {
  label: string;
  secondary?: string;
  icon?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <span className={styles.attachment}>
      {icon ?? <PaperclipIcon size={13} aria-hidden="true" />}
      <span className={styles.attachmentCopy}>
        <strong title={label}>{label}</strong>
        {secondary && <small>{secondary}</small>}
      </span>
      {actions}
    </span>
  );
}

export function AssetAttachment({ assetId }: { assetId: string }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const asset = useQuery({
    queryKey: ["asset", workspace.id, assetId],
    enabled: can("read"),
    staleTime: 60_000,
    retry: false,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/assets/{asset_id}", {
          params: { path: { asset_id: assetId } },
          signal,
        })
        .then(data),
  });
  const name = asset.data?.name || assetId;
  const download = useMutation({
    mutationFn: async () => {
      const blob = data(
        await client
          .workspace(workspace.id)
          .GET("/api/v1/assets/{asset_id}/content", {
            params: {
              path: { asset_id: assetId },
            },
            parseAs: "blob",
          }),
      );
      downloadBlob(blob, name);
    },
  });
  return (
    <>
      <AttachmentChip
        icon={<FileIcon size={13} aria-hidden="true" />}
        label={name}
        secondary={
          asset.data
            ? `${asset.data.content_type} · ${asset.data.size.toLocaleString()} B`
            : asset.isError
              ? t("File details unavailable")
              : undefined
        }
        actions={
          <>
            <CopyButton value={assetId} iconOnly copyLabel={t("Copy ID")} />
            {can("read") && (
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label={`${t("Download")} ${name}`}
                title={`${t("Download")} ${name}`}
                disabled={!!asset.data?.retired_at}
                loading={download.isPending}
                onClick={() => download.mutate()}
              >
                <DownloadSimpleIcon size={14} />
              </Button>
            )}
          </>
        }
      />
      <ErrorNotice error={download.error} retry={() => download.mutate()} />
    </>
  );
}
