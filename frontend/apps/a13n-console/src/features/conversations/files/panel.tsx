import { FileIcon, FolderOpenIcon, XIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useQueries } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { Empty } from "../../../shared/collection";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { assetQuery, matchesPublication, type FileReference } from "./assets";
import { useConversationFiles } from "./catalog";
import { FilePreview } from "./preview";
import styles from "./files.module.css";

export default function FilesPanel({
  runId,
  selected,
  onSelect,
  onClose,
}: {
  runId: string;
  selected?: FileReference;
  onSelect: (file: FileReference) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const catalog = useConversationFiles(runId);
  const groups = new Map<string, FileReference[]>();
  for (const file of [...catalog.files, ...(selected ? [selected] : [])]) {
    const group = groups.get(file.assetId);
    if (group) group.push(file);
    else groups.set(file.assetId, [file]);
  }
  const candidates = [...groups];
  const assets = useQueries({
    queries: candidates.map(([id]) => assetQuery(client, workspace.id, id)),
  });
  const files = candidates.flatMap(([id, refs], index) => {
    const query = assets[index]!;
    const asset = query.data;
    const file = refs.find(({ publication }) =>
      asset ? matchesPublication(asset, publication) : !publication?.legacy,
    );
    return file ? [{ id, file, asset, query }] : [];
  });
  const current = files.find(({ id }) => id === selected?.assetId);
  const loading = catalog.loading || assets.some((query) => query.isPending);
  return (
    <div className={styles.panel}>
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
        <FolderOpenIcon size={16} aria-hidden="true" />
        <h2 className="flex-1 text-sm font-medium">{t("Files")}</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Close files panel")}
          onClick={onClose}
        >
          <XIcon size={16} />
        </Button>
      </div>
      {(files.length > 0 || catalog.hasOlder) && (
        <div className={styles.catalog}>
          {files.length > 0 && (
            <ul
              aria-label={t("Conversation files")}
              className={`${styles.list} a13n-scrollbar`}
            >
              {files.map(({ id, file, asset }) => (
                <li key={id}>
                  <button
                    className={styles.file}
                    type="button"
                    aria-current={id === selected?.assetId ? "true" : undefined}
                    onClick={() => onSelect(file)}
                  >
                    <FileIcon size={14} aria-hidden="true" />
                    <span title={asset?.name ?? id}>{asset?.name ?? id}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {catalog.hasOlder && (
            <Button
              variant="ghost"
              size="sm"
              loading={catalog.loading}
              onClick={() => void catalog.loadOlder()}
            >
              {t("Find files in older runs")}
            </Button>
          )}
        </div>
      )}
      <div className={`${styles.body} a13n-scrollbar`}>
        <ErrorNotice error={catalog.error} retry={catalog.retry} />
        {current?.asset ? (
          <FilePreview key={current.id} asset={current.asset} />
        ) : current?.query.isError ? (
          <ErrorNotice
            error={current.query.error}
            retry={() => void current.query.refetch()}
          />
        ) : loading ? (
          <Loading variant="list" rows={2} />
        ) : selected ? (
          <p className={styles.notice}>{t("File details unavailable")}</p>
        ) : files.length ? (
          <p className={styles.notice}>{t("Select a file to preview it.")}</p>
        ) : (
          !catalog.error && (
            <Empty
              icon={<FolderOpenIcon aria-hidden="true" />}
              title={t("No files found")}
              description={t(
                "Attachments and files published by the agent appear here.",
              )}
            />
          )
        )}
      </div>
    </div>
  );
}
