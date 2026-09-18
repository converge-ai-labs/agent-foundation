import {
  CaretRightIcon,
  FileTextIcon,
  FolderIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import type { Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { MarkdownContent } from "../../../shared/markdown";
import { archiveQuery } from "../archive";
import {
  fileTree,
  markdownBody,
  previewLimit,
  readTextFile,
  type FileNode,
} from "../package-files";
import { SegmentedControl } from "../segmented";
import styles from "../skills.module.css";

/** The published package: its tree at the left, the chosen file at the right. */
export function SkillFiles({
  revision,
}: {
  revision: Schema["SkillRevision"];
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState("SKILL.md");
  const nodes = useMemo(
    () => fileTree(revision.manifest.files),
    [revision.manifest.files],
  );
  const file = revision.manifest.files.find((item) => item.path === selected);
  return (
    <div className={styles.browser}>
      <nav
        className={`${styles.tree} a13n-scrollbar`}
        aria-label={t("Package files")}
      >
        <p className={styles.treeHeading}>
          {t("Files")} <span>{revision.manifest.files.length}</span>
        </p>
        <FileTree nodes={nodes} selected={selected} select={setSelected} />
      </nav>
      <section className={styles.document} aria-label={selected}>
        {file ? (
          <FileContent key={file.path} file={file} revision={revision} />
        ) : (
          <p className={styles.notice}>
            {t("File not found in this version.")}
          </p>
        )}
      </section>
    </div>
  );
}

function FileTree({
  nodes,
  selected,
  select,
}: {
  nodes: FileNode[];
  selected: string;
  select: (path: string) => void;
}) {
  return (
    <ul>
      {nodes.map((node) => (
        <li key={node.path}>
          {node.children ? (
            <details open>
              <summary title={node.path}>
                <CaretRightIcon size={12} aria-hidden="true" />
                <FolderIcon size={14} aria-hidden="true" />
                <span>{node.name}</span>
              </summary>
              <FileTree
                nodes={node.children}
                selected={selected}
                select={select}
              />
            </details>
          ) : (
            <button
              type="button"
              className={styles.fileButton}
              aria-current={selected === node.path ? "true" : undefined}
              title={node.path}
              onClick={() => select(node.path)}
            >
              <FileTextIcon size={14} aria-hidden="true" />
              <span>{node.name}</span>
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

function FileContent({
  file,
  revision,
}: {
  file: Schema["SkillPackageFile"];
  revision: Schema["SkillRevision"];
}) {
  const client = useClient(),
    { t } = useTranslation();
  const [view, setView] = useState("preview");
  const large = file.size_bytes > previewLimit;
  const archive = useQuery({
    ...archiveQuery(client, revision),
    enabled: !large,
  });
  const preview = useMemo(() => {
    if (!archive.data || large) return {};
    try {
      return { text: readTextFile(archive.data, file) };
    } catch (error) {
      return { error };
    }
  }, [archive.data, file, large]);
  const readable = typeof preview.text === "string";
  const markdown = /\.md$/i.test(file.path) && !large;
  return (
    <>
      <header className={styles.documentHeader}>
        <span className={styles.documentPath}>
          <span title={file.path}>{file.path}</span>
          <small>
            {t("{{size}} bytes", { size: file.size_bytes.toLocaleString() })}
          </small>
        </span>
        {markdown && (
          <SegmentedControl
            label={t("File view")}
            value={view}
            onValueChange={setView}
            segments={[
              { value: "preview", label: t("Preview") },
              { value: "source", label: t("Source") },
            ]}
          />
        )}
      </header>
      <div className={styles.documentBody}>
        {large ? (
          <p className={styles.notice}>
            {t(
              "This file is too large to preview. Download the ZIP to view it.",
            )}
          </p>
        ) : archive.isPending ? (
          <Loading variant="code" />
        ) : archive.error || preview.error ? (
          <ErrorNotice
            error={archive.error ?? preview.error}
            retry={() => void archive.refetch()}
          />
        ) : preview.text === null ? (
          <p className={styles.notice}>
            {t("This file has no text preview. Download the ZIP to view it.")}
          </p>
        ) : readable ? (
          markdown && view === "preview" ? (
            <div className={styles.markdown}>
              <MarkdownContent text={markdownBody(preview.text!)} />
            </div>
          ) : (
            <Source text={preview.text!} />
          )
        ) : null}
      </div>
    </>
  );
}

function Source({ text }: { text: string }) {
  const { t } = useTranslation();
  return text ? (
    <pre className={`${styles.source} a13n-scrollbar`}>
      <code>{text}</code>
    </pre>
  ) : (
    <p className={styles.notice}>{t("This file is empty.")}</p>
  );
}
