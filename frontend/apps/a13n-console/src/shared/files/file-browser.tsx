import {
  CaretRightIcon,
  FileTextIcon,
  FolderIcon,
  DotsThreeIcon,
} from "@phosphor-icons/react";
import {
  Button,
  ContextMenu,
  ContextMenuTrigger,
  Menu,
  MenuPopup,
  MenuTrigger,
  SegmentedControl,
} from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { MarkdownContent } from "../markdown";
import { markdownBody, type FileNode } from "./tree";
import styles from "./files.module.css";

export type FileView = "preview" | "source";

/** A tree of files at the left and the chosen file at the right. */
export function FileBrowser({
  label,
  embedded = false,
  count,
  actions,
  nodes,
  selected,
  onSelect,
  marker,
  fileMenu,
  children,
}: {
  /** Names the tree for assistive technology, such as "Package files". */
  label: string;
  /** Join the surrounding page when its tabs already name and count the files. */
  embedded?: boolean;
  count: number;
  /** Controls beside the tree heading, such as New file. */
  actions?: ReactNode;
  nodes: FileNode[];
  selected: string;
  onSelect: (path: string) => void;
  /** A mark after a file's name, such as one that says it is always loaded. */
  marker?: (path: string) => ReactNode;
  /** The same actions appear on right-click and in a touch-accessible row menu. */
  fileMenu?: (path: string) => ReactNode;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.browser} data-embedded={embedded || undefined}>
      <nav className={`${styles.tree} a13n-scrollbar`} aria-label={label}>
        <div className={styles.treeHeading}>
          {!embedded && (
            <>
              <span>{t("Files")}</span>
              <span className={styles.treeCount}>{count}</span>
            </>
          )}
          {actions}
        </div>
        <div className={`${styles.treeBody} a13n-scrollbar`}>
          <FileTree
            nodes={nodes}
            selected={selected}
            select={onSelect}
            marker={marker}
            fileMenu={fileMenu}
          />
        </div>
      </nav>
      <section className={styles.document} aria-label={selected}>
        {children}
      </section>
    </div>
  );
}

function FileTree({
  nodes,
  selected,
  select,
  marker,
  fileMenu,
}: {
  nodes: FileNode[];
  selected: string;
  select: (path: string) => void;
  marker?: (path: string) => ReactNode;
  fileMenu?: (path: string) => ReactNode;
}) {
  const { t } = useTranslation();
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
                marker={marker}
                fileMenu={fileMenu}
              />
            </details>
          ) : (
            <div
              className={styles.fileRow}
              data-selected={selected === node.path || undefined}
            >
              {fileMenu ? (
                <ContextMenu>
                  <ContextMenuTrigger
                    render={
                      <button
                        type="button"
                        className={styles.fileButton}
                        aria-current={
                          selected === node.path ? "true" : undefined
                        }
                        title={node.path}
                        onClick={() => select(node.path)}
                      />
                    }
                  >
                    <FileTextIcon size={14} aria-hidden="true" />
                    <span>{node.name}</span>
                    {marker?.(node.path)}
                  </ContextMenuTrigger>
                  <MenuPopup align="start">{fileMenu(node.path)}</MenuPopup>
                </ContextMenu>
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
                  {marker?.(node.path)}
                </button>
              )}
              {fileMenu && (
                <Menu>
                  <MenuTrigger
                    render={
                      <Button
                        variant="ghost"
                        size="icon-xs"
                        className={styles.fileActions}
                        aria-label={t("Actions for {{path}}", {
                          path: node.path,
                        })}
                        title={t("Actions for {{path}}", { path: node.path })}
                      />
                    }
                  >
                    <DotsThreeIcon />
                  </MenuTrigger>
                  <MenuPopup align="start">{fileMenu(node.path)}</MenuPopup>
                </Menu>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/** The chosen file's path and size, its view switch and its actions. */
export function FileHeader({
  path,
  size,
  marker,
  view,
  onViewChange,
  actions,
}: {
  path: string;
  size: number;
  marker?: ReactNode;
  /** Offered for Markdown the reader can preview; omit it otherwise. */
  view?: FileView;
  onViewChange?: (view: FileView) => void;
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <header className={styles.documentHeader}>
      <span className={styles.documentPath}>
        <span title={path}>{path}</span>
        <small>{t("{{size}} bytes", { size: size.toLocaleString() })}</small>
        {marker}
      </span>
      {(view || actions) && (
        <span className={styles.documentActions}>
          {view && onViewChange && (
            <SegmentedControl
              label={t("File view")}
              value={view}
              onValueChange={(value) =>
                onViewChange(value === "source" ? "source" : "preview")
              }
              options={[
                { value: "preview", label: t("Preview") },
                { value: "source", label: t("Source") },
              ]}
            />
          )}
          {actions}
        </span>
      )}
    </header>
  );
}

export function FileBody({ children }: { children: ReactNode }) {
  return <div className={styles.documentBody}>{children}</div>;
}

/** Text as rendered Markdown or as its source; imported content never runs. */
export function FileText({
  text,
  preview,
}: {
  text: string;
  preview: boolean;
}) {
  const { t } = useTranslation();
  if (!text) return <FileNotice>{t("This file is empty.")}</FileNotice>;
  return preview ? (
    <div className={styles.markdown}>
      <MarkdownContent text={markdownBody(text)} />
    </div>
  ) : (
    <pre className={`${styles.source} a13n-scrollbar`}>
      <code>{text}</code>
    </pre>
  );
}

export function FileNotice({ children }: { children: ReactNode }) {
  return <p className={styles.notice}>{children}</p>;
}
