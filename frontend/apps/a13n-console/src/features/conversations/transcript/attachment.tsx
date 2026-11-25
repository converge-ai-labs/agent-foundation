import { Button } from "a13n-ui";
import {
  DownloadSimpleIcon,
  FileIcon,
  PaperclipIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useContext, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { CopyButton } from "../../../shared/identity";
import { ErrorNotice } from "../../../shared/feedback";
import {
  assetQuery,
  matchesPublication,
  useAssetDownload,
  type FileReference,
} from "../files/assets";
import { PreviewFileContext } from "../files/selection";
import styles from "./transcript.module.css";

/** One chip presents every attachment, in the composer and in the transcript. */
export function AttachmentChip({
  label,
  secondary,
  icon,
  actions,
  onPreview,
  previewLabel,
}: {
  label: string;
  secondary?: string;
  icon?: ReactNode;
  actions?: ReactNode;
  onPreview?: () => void;
  previewLabel?: string;
}) {
  const content = (
    <>
      {icon ?? <PaperclipIcon size={13} aria-hidden="true" />}
      <span className={styles.attachmentCopy}>
        <strong title={label}>{label}</strong>
        {secondary && <small>{secondary}</small>}
      </span>
    </>
  );
  return (
    <span className={styles.attachment}>
      {onPreview ? (
        <button
          type="button"
          className={styles.attachmentPreview}
          aria-label={previewLabel}
          aria-controls="session-files"
          onClick={onPreview}
        >
          {content}
        </button>
      ) : (
        content
      )}
      {actions}
    </span>
  );
}

export function AssetAttachment({ assetId, publication }: FileReference) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace();
  const onPreview = useContext(PreviewFileContext);
  const asset = useQuery({
    ...assetQuery(client, workspace.id, assetId),
    enabled: can("read"),
  });
  const name = asset.data?.name || assetId;
  const retired = !!asset.data?.retired_at;
  const download = useAssetDownload(client, workspace.id, assetId, name);
  if (
    publication &&
    (asset.data
      ? !matchesPublication(asset.data, publication)
      : publication.legacy)
  )
    return null;
  return (
    <>
      <AttachmentChip
        icon={<FileIcon size={13} aria-hidden="true" />}
        label={name}
        onPreview={
          onPreview && can("read") && asset.data && !retired
            ? () => onPreview({ assetId, publication })
            : undefined
        }
        previewLabel={t("Preview {{name}}", { name })}
        secondary={
          retired
            ? t("File retired")
            : asset.data
              ? `${asset.data.content_type} · ${asset.data.size.toLocaleString()} B`
              : asset.isError || !can("read")
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
                disabled={!asset.data || retired}
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
