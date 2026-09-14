import { Button, Tabs, TabsList, TabsPanel, TabsTab } from "a13n-ui";
import {
  CaretRightIcon,
  FileTextIcon,
  FolderIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { data, workspaceHeaders, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { MarkdownContent } from "../../shared/markdown";
import { archiveQuery, DownloadRevision } from "./archive";
import {
  fileTree,
  markdownBody,
  previewLimit,
  readTextFile,
  type FileNode,
} from "./package-files";
import styles from "./skills.module.css";

export function SkillFiles({
  skill,
  revisionId,
}: {
  skill: Schema["Skill"];
  revisionId: string;
}) {
  const client = useClient(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["skills", skill.workspace_id, skill.id, "revision", revisionId],
    queryFn: async ({ signal }) => {
      const revision = data(
        await client.http.GET("/api/v1/skill-revisions/{skill_revision_id}", {
          params: { path: { skill_revision_id: revisionId } },
          headers: workspaceHeaders(skill.workspace_id),
          signal,
        }),
      );
      if (revision.skill_id !== skill.id)
        throw new Error(t("This version does not belong to this skill."));
      return revision;
    },
  });
  if (query.isPending) return <Loading variant="detail" />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const revision = query.data;
  return (
    <div className={styles.files}>
      <div className={styles.versionBar}>
        <div className={styles.versionMeta}>
          <span>v{revision.version}</span>
          {revision.id === skill.current_revision_id ? (
            <span className={styles.current}>{t("Current")}</span>
          ) : (
            <Link to="?tab=files">{t("View current version")}</Link>
          )}
          <Timestamp value={revision.created_at} />
          <span>
            {revision.imported_from.kind === "github"
              ? "GitHub"
              : t("ZIP file")}
          </span>
        </div>
        <DownloadRevision
          revision={revision}
          filename={`${skill.key}-v${revision.version}.zip`}
        />
      </div>
      <PackageFiles key={revision.id} revision={revision} />
    </div>
  );
}

export function PackageFiles({
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
        <div className={styles.treeHeading}>
          {t("Files")} <span>{revision.manifest.files.length}</span>
        </div>
        <FileTree nodes={nodes} selected={selected} select={setSelected} />
      </nav>
      <section className={styles.file} aria-label={selected}>
        {file ? (
          <FileContent key={file.path} file={file} revision={revision} />
        ) : (
          <p>{t("File not found in this version.")}</p>
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
                <CaretRightIcon size={12} />
                <FolderIcon size={15} />
                <span>{node.name}</span>
              </summary>
              <FileTree
                nodes={node.children}
                selected={selected}
                select={select}
              />
            </details>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className={styles.fileLink}
              aria-current={selected === node.path ? "true" : undefined}
              title={node.path}
              onClick={() => select(node.path)}
            >
              <FileTextIcon size={15} />
              <span>{node.name}</span>
            </Button>
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
  const markdown = /\.md$/i.test(file.path);
  return (
    <>
      <div className={styles.fileHeading}>
        <span>{file.path}</span>
        <small>{file.size_bytes.toLocaleString()} B</small>
      </div>
      {large ? (
        <p className={styles.notice}>
          {t("This file is too large to preview. Download the ZIP to view it.")}
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
      ) : typeof preview.text === "string" ? (
        markdown ? (
          <Tabs defaultValue="preview" className={styles.fileTabs}>
            <TabsList size="sm" aria-label={t("File view")}>
              <TabsTab value="preview">{t("Preview")}</TabsTab>
              <TabsTab value="source">{t("Source")}</TabsTab>
            </TabsList>
            <TabsPanel value="preview">
              <div className={styles.document}>
                <MarkdownContent text={markdownBody(preview.text)} />
              </div>
            </TabsPanel>
            <TabsPanel value="source">
              <Source text={preview.text} />
            </TabsPanel>
          </Tabs>
        ) : (
          <Source text={preview.text} />
        )
      ) : null}
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
