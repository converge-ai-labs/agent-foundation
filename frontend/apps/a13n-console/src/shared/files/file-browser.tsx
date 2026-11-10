import {
  ArrowLeftIcon,
  SidebarSimpleIcon,
  CodeIcon,
  EyeIcon,
} from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { MarkdownContent } from "../markdown";
import { FileExplorer } from "./file-explorer";
import { TextReader } from "./text-reader";
import { markdownBody, type FileNode } from "./tree";
import styles from "./files.module.css";

export type FileView = "preview" | "source";
const FilePath = createContext("");
const FileNavigation = createContext<ReactNode>(null);

/** Shared workspace: independently scrolling panes, mobile list → file navigation. */
export function FileBrowser({
  label,
  count,
  actions,
  nodes,
  selected,
  onSelect,
  marker,
  fileMenu,
  children,
  fullHeight = false,
  initialFile = false,
}: {
  label: string;
  count: number;
  actions?: ReactNode;
  nodes: FileNode[];
  selected: string;
  onSelect: (path: string) => void;
  marker?: (path: string) => ReactNode;
  fileMenu?: (path: string) => ReactNode;
  children: ReactNode;
  fullHeight?: boolean;
  initialFile?: boolean;
}) {
  const { t } = useTranslation();
  const [mobileFile, setMobileFile] = useState(initialFile);
  const previous = useRef(selected);
  useEffect(() => {
    if (previous.current !== selected) setMobileFile(true);
    previous.current = selected;
  }, [selected]);
  const [collapsed, setCollapsed] = useState(false);
  const [width, setWidth] = useState(260);
  const root = useRef<HTMLDivElement>(null);
  const tree = useRef<HTMLElement>(null);
  const document = useRef<HTMLElement>(null);
  const treeId = useId();
  function select(path: string) {
    onSelect(path);
    setMobileFile(true);
    // On a phone the selected row leaves the screen; keep keyboard focus in the reader.
    if (window.matchMedia("(max-width: 767px)").matches)
      requestAnimationFrame(() => document.current?.focus());
  }
  return (
    <div
      ref={root}
      className={styles.browser}
      data-full-height={fullHeight || undefined}
      data-mobile-file={mobileFile || undefined}
      data-collapsed={collapsed || undefined}
      style={{ "--explorer-width": `${width}px` } as CSSProperties}
    >
      <nav
        ref={tree}
        className={styles.tree}
        id={treeId}
        aria-label={label}
        tabIndex={-1}
      >
        <div className={styles.treeHeading}>
          <span>{t("Files")}</span>
          <span className={styles.treeCount}>{count}</span>
          {actions}
        </div>
        <div className={`${styles.treeBody} a13n-scrollbar`}>
          <FileExplorer
            label={label}
            nodes={nodes}
            selected={selected}
            select={select}
            marker={marker}
            fileMenu={fileMenu}
          />
        </div>
      </nav>
      <div
        className={styles.resize}
        role="separator"
        aria-label={t("Resize file explorer")}
        aria-orientation="vertical"
        aria-valuenow={width}
        aria-valuemin={220}
        aria-valuemax={420}
        tabIndex={collapsed ? -1 : 0}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            setWidth((value) =>
              Math.max(
                220,
                Math.min(420, value + (event.key === "ArrowRight" ? 20 : -20)),
              ),
            );
          }
        }}
        onPointerDown={(event) =>
          event.currentTarget.setPointerCapture(event.pointerId)
        }
        onPointerMove={(event) => {
          if (
            event.currentTarget.hasPointerCapture(event.pointerId) &&
            root.current
          )
            setWidth(
              Math.max(
                220,
                Math.min(
                  420,
                  event.clientX - root.current.getBoundingClientRect().left,
                ),
              ),
            );
        }}
        onPointerUp={(event) =>
          event.currentTarget.releasePointerCapture(event.pointerId)
        }
      />
      <section
        ref={document}
        tabIndex={-1}
        className={styles.document}
        aria-label={selected || t("Select a file")}
      >
        <FileNavigation
          value={
            <>
              <Button
                variant="ghost"
                size="sm"
                className={styles.backFiles}
                onClick={() => {
                  setMobileFile(false);
                  requestAnimationFrame(() =>
                    tree.current
                      ?.querySelector<HTMLElement>(
                        '[role="treeitem"][tabindex="0"]',
                      )
                      ?.focus(),
                  );
                }}
              >
                <ArrowLeftIcon />
                {t("Files")}
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                className={styles.toggleExplorer}
                aria-label={t(
                  collapsed ? "Show file explorer" : "Hide file explorer",
                )}
                title={t(
                  collapsed ? "Show file explorer" : "Hide file explorer",
                )}
                aria-expanded={!collapsed}
                aria-controls={treeId}
                onClick={() => setCollapsed(!collapsed)}
              >
                <SidebarSimpleIcon />
              </Button>
            </>
          }
        >
          <FilePath value={selected}>{children}</FilePath>
        </FileNavigation>
      </section>
    </div>
  );
}

export function FileHeader({
  path,
  size,
  marker,
  view,
  onViewChange,
  actions,
}: {
  path: string;
  size?: number;
  marker?: ReactNode;
  view?: FileView;
  onViewChange?: (view: FileView) => void;
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
  const navigation = useContext(FileNavigation);
  return (
    <header className={styles.documentHeader}>
      <span className={styles.documentIdentity}>
        {navigation}
        <span className={styles.documentPath}>
          <span title={path}>{path}</span>
          {size !== undefined && (
            <small>
              {t("{{size}} bytes", { size: size.toLocaleString() })}
            </small>
          )}
          {marker}
        </span>
      </span>
      {(view || actions) && (
        <span className={styles.documentActions}>
          {view && onViewChange && (
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t(view === "preview" ? "Source" : "Preview")}
              title={t(view === "preview" ? "Source" : "Preview")}
              onClick={() =>
                onViewChange(view === "source" ? "preview" : "source")
              }
            >
              {view === "preview" ? <CodeIcon /> : <EyeIcon />}
            </Button>
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

/** Imported Markdown keeps the existing safe preview; full source loads lazily. */
export function FileText({
  text,
  preview,
  onOpenLink,
}: {
  text: string;
  preview: boolean;
  onOpenLink?: (href: string) => boolean;
}) {
  const path = useContext(FilePath);
  return preview ? (
    <div
      className={styles.markdown}
      onClick={
        onOpenLink
          ? (event) => {
              const link =
                event.target instanceof Element
                  ? event.target.closest("a")
                  : null;
              const href = link?.getAttribute("href");
              if (href && onOpenLink(href)) event.preventDefault();
            }
          : undefined
      }
    >
      <MarkdownContent text={markdownBody(text)} />
    </div>
  ) : (
    <TextReader path={path} text={text} />
  );
}

export function FileNotice({ children }: { children: ReactNode }) {
  return <p className={styles.notice}>{children}</p>;
}
