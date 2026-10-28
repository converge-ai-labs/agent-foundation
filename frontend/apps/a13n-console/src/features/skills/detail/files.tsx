import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import {
  FileBody,
  FileBrowser,
  FileHeader,
  FileNotice,
  FileText,
  fileTree,
  isMarkdown,
  type FileView,
} from "../../../shared/files";
import type { archiveQuery } from "../archive";
import {
  linkedPackageFile,
  previewLimit,
  readTextFile,
} from "../package-files";

type ArchiveQuery = ReturnType<typeof archiveQuery>;

/** The published package: its tree at the left, the chosen file at the right. */
export function SkillFiles({
  manifest,
  archive,
  onRetry,
}: {
  manifest: Pick<Schema["SkillManifest"], "files" | "root">;
  archive: ArchiveQuery;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState("SKILL.md");
  const nodes = useMemo(() => fileTree(manifest.files), [manifest.files]);
  const file = manifest.files.find((item) => item.path === selected);
  return (
    <FileBrowser
      label={t("Package files")}
      count={manifest.files.length}
      nodes={nodes}
      selected={selected}
      onSelect={setSelected}
    >
      {file ? (
        <FileContent
          key={file.path}
          file={file}
          root={manifest.root}
          archiveQuery={archive}
          onRetry={onRetry}
          onOpenLink={(href) => {
            const path = linkedPackageFile(file.path, href, manifest.files);
            if (!path) return false;
            setSelected(path);
            return true;
          }}
        />
      ) : (
        <FileNotice>{t("File not found in this version.")}</FileNotice>
      )}
    </FileBrowser>
  );
}

function FileContent({
  file,
  root,
  archiveQuery,
  onRetry,
  onOpenLink,
}: {
  file: Schema["SkillFile"];
  root: string;
  archiveQuery: ArchiveQuery;
  onRetry?: () => void;
  onOpenLink: (href: string) => boolean;
}) {
  const { t } = useTranslation();
  const [view, setView] = useState<FileView>("preview");
  const large = file.size > previewLimit;
  const archive = useQuery({
    ...archiveQuery,
    enabled: !large,
  });
  const preview = useMemo(() => {
    if (!archive.data || large) return {};
    try {
      return { text: readTextFile(archive.data, root, file) };
    } catch (error) {
      return { error };
    }
  }, [archive.data, file, large, root]);
  const markdown = isMarkdown(file.path) && !large;
  return (
    <>
      <FileHeader
        path={file.path}
        size={file.size}
        view={markdown ? view : undefined}
        onViewChange={setView}
      />
      <FileBody>
        {large ? (
          <FileNotice>
            {t(
              "This file is too large to preview. Download the ZIP to view it.",
            )}
          </FileNotice>
        ) : archive.isPending ? (
          <Loading variant="code" />
        ) : archive.error || preview.error ? (
          <ErrorNotice
            error={archive.error ?? preview.error}
            retry={onRetry ?? (() => void archive.refetch())}
          />
        ) : preview.text === null ? (
          <FileNotice>
            {t("This file has no text preview. Download the ZIP to view it.")}
          </FileNotice>
        ) : typeof preview.text === "string" ? (
          <FileText
            text={preview.text}
            preview={markdown && view === "preview"}
            onOpenLink={onOpenLink}
          />
        ) : null}
      </FileBody>
    </>
  );
}
