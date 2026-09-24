import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
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
import { archiveQuery } from "../archive";
import { previewLimit, readTextFile } from "../package-files";

/** The published package: its tree at the left, the chosen file at the right. */
export function SkillFiles({
  revision,
}: {
  revision: Schema["SkillRevision"];
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState("SKILL.md");
  const nodes = useMemo(
    () => fileTree(revision.config.files),
    [revision.config.files],
  );
  const file = revision.config.files.find((item) => item.path === selected);
  return (
    <FileBrowser
      label={t("Package files")}
      count={revision.config.files.length}
      nodes={nodes}
      selected={selected}
      onSelect={setSelected}
    >
      {file ? (
        <FileContent key={file.path} file={file} revision={revision} />
      ) : (
        <FileNotice>{t("File not found in this version.")}</FileNotice>
      )}
    </FileBrowser>
  );
}

function FileContent({
  file,
  revision,
}: {
  file: Schema["SkillFile"];
  revision: Schema["SkillRevision"];
}) {
  const client = useClient(),
    { t } = useTranslation();
  const [view, setView] = useState<FileView>("preview");
  const large = file.size > previewLimit;
  const archive = useQuery({
    ...archiveQuery(client, revision),
    enabled: !large,
  });
  const preview = useMemo(() => {
    if (!archive.data || large) return {};
    try {
      return { text: readTextFile(archive.data, revision.config.root, file) };
    } catch (error) {
      return { error };
    }
  }, [archive.data, file, large, revision.config.root]);
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
            retry={() => void archive.refetch()}
          />
        ) : preview.text === null ? (
          <FileNotice>
            {t("This file has no text preview. Download the ZIP to view it.")}
          </FileNotice>
        ) : typeof preview.text === "string" ? (
          <FileText
            text={preview.text}
            preview={markdown && view === "preview"}
          />
        ) : null}
      </FileBody>
    </>
  );
}
