import { FolderOpenIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useQueries } from "@tanstack/react-query";
import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { Empty } from "../../../shared/collection";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { assetQuery, matchesPublication, type FileReference } from "./assets";
import { useConversationFiles } from "./catalog";
import { FilePreview } from "./preview";
import { BackToFiles, CloseFiles, FileSize } from "./file-controls";
import { FileTypeIcon } from "./file-type-icon";
import styles from "./files.module.css";

export default function FilesPanel({
  runId,
  selected,
  onSelect,
  onClose,
}: {
  runId: string;
  selected?: FileReference;
  onSelect: (file: FileReference | undefined) => void;
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
  const panel = useRef<HTMLDivElement>(null);
  const returnTo = useRef<string>(undefined);
  const heading = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => {
    if (selected) panel.current?.querySelector("button")?.focus();
    else if (returnTo.current) {
      const row = Array.from(
        panel.current?.querySelectorAll<HTMLButtonElement>("[data-file-id]") ??
          [],
      ).find((button) => button.dataset.fileId === returnTo.current);
      returnTo.current = undefined;
      (row ?? heading.current)?.focus();
    }
  }, [selected?.assetId, !!current?.asset]);
  const back = () => {
    returnTo.current = selected?.assetId;
    onSelect(undefined);
  };
  return (
    <div ref={panel} className={styles.panel}>
      {selected ? (
        current?.asset ? (
          <FilePreview
            key={current.id}
            asset={current.asset}
            onBack={back}
            onClose={onClose}
          />
        ) : (
          <>
            <div className={styles.toolbar}>
              <BackToFiles onClick={back} />
              <h2 className={`${styles.identity} ${styles.name}`}>
                {t("Files")}
              </h2>
              <CloseFiles onClick={onClose} />
            </div>
            <div className={`${styles.body} a13n-scrollbar`}>
              {current?.query.isError ? (
                <ErrorNotice
                  error={current.query.error}
                  retry={() => void current.query.refetch()}
                />
              ) : loading ? (
                <Loading variant="list" rows={2} />
              ) : (
                <p className={styles.notice}>{t("File details unavailable")}</p>
              )}
            </div>
          </>
        )
      ) : (
        <>
          <div className={styles.toolbar}>
            <FolderOpenIcon
              className={styles.typeIcon}
              size={16}
              aria-hidden="true"
            />
            <h2
              className={`${styles.identity} ${styles.name}`}
              ref={heading}
              tabIndex={-1}
            >
              {t("Files")}
            </h2>
            <CloseFiles onClick={onClose} />
          </div>
          <div className={`${styles.body} ${styles.catalog} a13n-scrollbar`}>
            <ErrorNotice error={catalog.error} retry={catalog.retry} />
            {catalog.incomplete && (
              <p className={styles.notice} role="status">
                {t(
                  "Some run content is unavailable, so this file list may be incomplete.",
                )}
              </p>
            )}
            {files.length > 0 ? (
              <ul aria-label={t("Conversation files")} className={styles.list}>
                {files.map(({ id, file, asset }) => (
                  <li key={id}>
                    <button
                      className={styles.file}
                      type="button"
                      data-file-id={id}
                      onClick={() => onSelect(file)}
                    >
                      <FileTypeIcon asset={asset} className={styles.typeIcon} />
                      <span
                        className={styles.fileName}
                        title={asset?.name ?? id}
                      >
                        {asset?.name ?? id}
                      </span>
                      {asset && <FileSize size={asset.size} />}
                    </button>
                  </li>
                ))}
              </ul>
            ) : loading ? (
              <Loading variant="list" rows={2} />
            ) : (
              !catalog.error &&
              !catalog.incomplete && (
                <Empty
                  icon={<FolderOpenIcon aria-hidden="true" />}
                  title={t("No files found")}
                  description={t(
                    "Attachments and files published by the agent appear here.",
                  )}
                />
              )
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
        </>
      )}
    </div>
  );
}
