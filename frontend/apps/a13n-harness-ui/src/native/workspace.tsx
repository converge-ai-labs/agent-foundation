import {
  useContext,
  useEffect,
  useRef,
  useState,
  useReducer,
  type ReactNode,
  type CSSProperties,
} from "react";
import { matchPath, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import {
  Folder,
  GitDiff,
  X,
  ArrowClockwise,
  TerminalWindow,
  ArrowLeft,
  ArrowUp,
  CaretRight,
  ArrowsOutSimple,
  ArrowsInSimple,
  LinkSimple,
} from "@phosphor-icons/react";
import { ApiError, result, type Schema } from "../transport/client";
import { useProjects, useStatus, useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import {
  FileBuffers,
  basename,
  breadcrumbs,
  parentPath,
  withinRoot,
} from "./buffer";
import { Files } from "./files";
import { FileView } from "./file-view";
import { Changes, DiffView, type DiffSelection } from "./changes";
import styles from "./native.module.css";
import { TerminalPanel, type TerminalRequest } from "./terminal";
import { TerminalAction } from "./terminal-action";
import { terminalShortcut, terminalShortcutLabels } from "./terminal-shortcuts";
import { usePanelSize } from "./panel-size";
import { ReturnToChat } from "./capture";
import { useThread } from "../conversations/queries";
import { ComposerDrafts } from "../conversations/composer";
import { conversationTitle } from "../conversations/local-input";
import { nativeLink, pageLink } from "../shell/page-links";
import { OpenHostFile } from "../conversations/tool-call";
import { CoordinatorMark } from "../conversations/coordinator-icon";

export function NativeWorkspace({
  children,
  onFocus,
  unauthorized,
  navigation,
  profile,
}: {
  children: ReactNode;
  navigation?: ReactNode;
  profile?: ReactNode;
  onFocus: (target: Schema<"PageTarget"> | null) => void;
  unauthorized: () => void;
}) {
  const { client } = useTransport();
  const projects = useProjects();
  const status = useStatus();
  const queries = useQueryClient();
  const buffers = useContext(FileBuffers);
  const location = useLocation();
  const threadId = matchPath("/threads/:threadId", location.pathname)?.params
    .threadId;
  const thread = useThread(threadId ?? "");
  const composers = useContext(ComposerDrafts);
  const isNew =
    location.pathname === "/" || !!matchPath("/new", location.pathname);
  const memoryMode =
    location.pathname === "/memory" || !!thread.data?.thread.memory_scope;
  const isWorkspace =
    !memoryMode && (location.pathname === "/" || !!threadId || isNew);
  const projectLoading = !!threadId && (thread.isPending || projects.isPending);
  const resolvedProject = projects.data?.find(
    (item) =>
      item.project_id ===
      (isNew
        ? new URLSearchParams(location.search).get("project")
        : thread.data?.thread.configuration?.project_id),
  );
  // Settings hides native views without replacing their current Project context.
  const retainedProject = useRef<Schema<"ProjectSummary"> | undefined>(
    undefined,
  );
  if (isWorkspace && !projectLoading) retainedProject.current = resolvedProject;
  const project = isWorkspace ? resolvedProject : retainedProject.current;
  const projectId = project?.project_id;
  const retainedRoots = useRef<string[]>([]);
  const selectedRoots = threadId
    ? (thread.data?.thread.configuration.local_roots ?? [])
    : (resolvedProject?.roots ?? []);
  if (isWorkspace && !projectLoading) retainedRoots.current = selectedRoots;
  const localRoots = isWorkspace ? selectedRoots : retainedRoots.current;
  const projectRoot = localRoots[0] ?? "";
  const [expanded, setExpanded] = useState(false);
  const [width, setWidth] = usePanelSize("a13n.native.width", 44, 28, 70);
  const resizeStart = useRef<{
    x: number;
    width: number;
    available: number;
  } | null>(null);
  const [, renderBuffers] = useReducer((n: number) => n + 1, 0);
  const [fileLine, setFileLine] = useState<number>();
  const [terminalRequest, setTerminalRequest] = useState<TerminalRequest>();
  const [view, setView] = useState<"page" | "file" | "diff">("page");
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [focusedArea, setFocusedArea] = useState<
    "page" | "native" | "explorer" | "terminal"
  >("page");
  const [terminalId, setTerminalId] = useState("");
  const [activeTerminalId, setActiveTerminalId] = useState("");
  const [terminalOpened, setTerminalOpened] = useState(false);
  const pageElement = useRef<HTMLDivElement>(null);
  const paneElement = useRef<HTMLElement>(null);
  const editorElement = useRef<HTMLDivElement>(null);
  const folderTrail = useRef<HTMLElement>(null);
  const [paneLink, setPaneLink] = useState("");
  const [pane, setPane] = useState<"files" | "changes" | null>(null);
  const [directory, setDirectory] = useState("");
  const [root, setRoot] = useState("");
  const [path, setPath] = useState("");
  const [fileTabs, setFileTabs] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [opening, setOpening] = useState(false);
  const [selected, select] = useState<DiffSelection | null>(null);
  const [diffChoices, setDiffChoices] = useState<DiffSelection[]>([]);
  const projectViews = useRef(
    new Map<
      string,
      {
        root: string;
        directory: string;
        path: string;
        tabs: string[];
        selected: DiffSelection | null;
        terminalId: string;
      }
    >(),
  );
  const openingRequest = useRef<AbortController | null>(null);
  const appliedProject = useRef<string | null>(null);
  const fileRoot =
    localRoots.includes(root) && withinRoot(directory, root)
      ? root
      : localRoots.find((folder) => withinRoot(directory, folder));
  const parentDirectory = parentPath(directory);
  const canGoUp =
    !!directory && directory !== fileRoot && parentDirectory !== directory;
  const directoryTrail = breadcrumbs(directory).filter(
    (part) => !fileRoot || withinRoot(part, fileRoot),
  );
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: ["native"] });
  };
  useEffect(() => {
    const trail = folderTrail.current;
    if (trail) trail.scrollTop = trail.scrollHeight;
  }, [directory, pane, view]);
  useEffect(() => {
    if (projectLoading) return;
    const context = `${projectId ?? ""}:${projectRoot}`;
    if (appliedProject.current === context) return;
    if (appliedProject.current) {
      projectViews.current.set(appliedProject.current, {
        root,
        directory,
        path,
        tabs: fileTabs,
        selected,
        terminalId,
      });
      if (projectViews.current.size > 16)
        projectViews.current.delete(projectViews.current.keys().next().value!);
    }
    const saved = projectViews.current.get(context);
    appliedProject.current = context;
    openingRequest.current?.abort();
    setOpening(false);
    setError(null);
    setRoot(saved?.root ?? projectRoot);
    setDirectory(saved?.directory ?? projectRoot);
    setPath(saved?.path ?? "");
    setFileTabs(saved?.tabs ?? []);
    select(saved?.selected ?? null);
    setDiffChoices([]);
    setFileLine(undefined);
    setTerminalRequest(undefined);
    setView("page");
    setPaneLink("");
    setTerminalId(saved?.terminalId ?? "");
  }, [projectId, projectRoot, projectLoading]);
  useEffect(() => {
    setOpening(false);
    return () => openingRequest.current?.abort();
  }, [location.pathname, location.search]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (
        [...buffers.values()].some(
          (entry) => entry.dirty || entry.uncertain || entry.saving,
        )
      )
        event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [buffers]);
  const open = async (target: string, line?: number) => {
    openingRequest.current?.abort();
    const controller = new AbortController();
    openingRequest.current = controller;
    setOpening(true);
    setError(null);
    try {
      let isDirectory = false;
      // A deleted/moved dirty file still has recoverable private text.
      if (!buffers.get(target)?.dirty) {
        const entry = await result(
          client.GET("/api/host/files/metadata", {
            params: { query: { path: target } },
            signal: controller.signal,
          }),
        );
        isDirectory = entry.kind === "directory";
        if (entry.kind === "symlink") {
          try {
            await result(
              client.GET("/api/host/files", {
                params: { query: { path: target, limit: 1 } },
                signal: controller.signal,
              }),
            );
            isDirectory = true;
          } catch (failure) {
            if (!(
              failure instanceof ApiError &&
              failure.code === "host_files_type_invalid"
            ))
              throw failure;
          }
        }
        if (entry.kind === "other")
          throw new Error(
            "This is a special native entry, not regular file content. Inspect, move, or delete it through its directory entry actions.",
          );
      }
      if (controller.signal.aborted) return;
      const nextDirectory = isDirectory ? target : parentPath(target);
      setDirectory(nextDirectory);
      if (!root) setRoot(nextDirectory);
      if (!isDirectory) {
        setPath(target);
        setFileLine(line);
        setView("file");
        setFocusedArea("native");
        setPane("files");
      }
      if (!isDirectory)
        setFileTabs((tabs) =>
          tabs.includes(target) ? tabs : [...tabs, target],
        );
      if (isDirectory) {
        setView("page");
        setFocusedArea("explorer");
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure);
    } finally {
      if (!controller.signal.aborted) setOpening(false);
    }
  };
  useEffect(() => {
    if (!isWorkspace || projectLoading) return;
    const link = nativeLink(location.search);
    if (link.pane && link.path && status.data?.features?.host_files) {
      setPane(link.pane);
      setFocusedArea("native");
      if (window.matchMedia("(max-width: 999px)").matches)
        setTerminalOpen(false);
      if (link.pane === "changes") {
        openingRequest.current?.abort();
        setOpening(false);
        setError(null);
        setDirectory(link.path);
        setRoot(link.path);
        if (link.diffPath && link.comparison) {
          setView("diff");
        } else {
          setView("page");
          setFocusedArea("explorer");
        }
        select(
          link.diffPath && link.comparison
            ? {
                repository_path: link.path,
                path: link.diffPath,
                comparison: link.comparison,
              }
            : null,
        );
      } else void open(link.path);
    }
    if (link.terminal && status.data?.features?.host_terminal) {
      setTerminalId(link.terminal);
      setFocusedArea("terminal");
      setTerminalOpen(true);
      setTerminalOpened(true);
    }
    // Navigation never creates a session, claims control or adds model input.
    // Explicit file/Changes links override defaults; terminals remain Project-scoped.
  }, [
    isWorkspace,
    projectLoading,
    projectId,
    projectRoot,
    location.pathname,
    location.search,
    status.data?.features?.host_files,
    status.data?.features?.host_terminal,
  ]);
  useEffect(() => {
    const link = nativeLink(location.search);
    if (!(link.pane && link.path) && !link.terminal) {
      setFocusedArea("page");
      setView("page");
      if (window.matchMedia("(max-width: 999px)").matches) setPane(null);
      if (window.matchMedia("(max-width: 700px)").matches)
        setTerminalOpen(false);
    }
  }, [location.pathname, location.search]);
  const nativeTarget = (): Schema<"PageTarget"> | null =>
    pane === "files" && directory
      ? { kind: "file", path: directory }
      : pane === "changes" && root
        ? {
            kind: "changes",
            repository_root: root,
          }
        : null;
  useEffect(() => {
    onFocus(
      !isWorkspace
        ? null
        : focusedArea === "native"
          ? view === "file" && path
            ? { kind: "file", path }
            : view === "diff" && selected
              ? {
                  kind: "changes",
                  repository_root: selected.repository_path,
                  path: selected.path,
                  comparison: selected.comparison,
                }
              : nativeTarget()
          : focusedArea === "explorer"
            ? nativeTarget()
            : focusedArea === "terminal" &&
                terminalOpen &&
                activeTerminalId &&
                activeTerminalId === terminalId
              ? { kind: "terminal", terminal_id: activeTerminalId }
              : null,
    );
  }, [
    isWorkspace,
    view,
    focusedArea,
    pane,
    path,
    directory,
    root,
    selected,
    terminalOpen,
    terminalId,
    activeTerminalId,
    onFocus,
  ]);
  const focusCenter = () => {
    setFocusedArea("page");
    pageElement.current?.focus();
  };
  const openTerminal = (cwd: string) => {
    if (!projectId) return;
    setTerminalRequest({ cwd, projectId });
    setTerminalOpened(true);
    setTerminalOpen(true);
    setFocusedArea("terminal");
    if (window.matchMedia("(max-width: 999px)").matches) setPane(null);
  };
  const toggleTerminal = () => {
    cancelOpening();
    setTerminalOpen(!terminalOpen);
    if (!terminalOpen) {
      setFocusedArea("terminal");
      if (window.matchMedia("(max-width: 999px)").matches) setPane(null);
    } else focusCenter();
    setTerminalOpened(true);
    refresh();
  };
  useEffect(() => {
    if (!isWorkspace || projectLoading || !status.data?.features?.host_terminal)
      return;
    const keydown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || document.querySelector('[role="dialog"]'))
        return;
      const action = terminalShortcut(event);
      if (action !== "toggle" && action !== "create") return;
      const cwd = localRoots.includes(root) ? root : projectRoot;
      if (action === "create" && (!projectId || !cwd)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.repeat) return;
      if (action === "create") openTerminal(cwd);
      else toggleTerminal();
    };
    // Capture before xterm or an editor can translate this into input.
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  });
  const title = threadId
    ? conversationTitle(
        thread.data?.thread,
        composers.get(threadId)?.localInputs,
      )
    : "";
  useEffect(() => {
    document.title = title ? `${title} · a13n harness ui` : "a13n harness ui";
    return () => {
      document.title = "a13n harness ui";
    };
  }, [title]);
  const runState = thread.data?.thread.root_activity?.state;
  const previousRun = useRef({ threadId, state: runState });
  useEffect(() => {
    const previous = previousRun.current;
    if (
      previous.threadId === threadId &&
      previous.state &&
      previous.state !== "inactive" &&
      runState === "inactive" &&
      (pane || terminalOpen)
    )
      refresh();
    previousRun.current = { threadId, state: runState };
  }, [threadId, runState, pane, terminalOpen]);
  const previousArea = useRef(focusedArea);
  useEffect(() => {
    if (
      previousArea.current === "terminal" &&
      focusedArea !== "terminal" &&
      pane
    )
      refresh();
    previousArea.current = focusedArea;
  }, [focusedArea, pane]);
  const cancelOpening = () => {
    openingRequest.current?.abort();
    setOpening(false);
  };
  const closePane = () => {
    cancelOpening();
    setPane(null);
    focusCenter();
    refresh();
  };
  const show = (next: "files" | "changes") => {
    cancelOpening();
    if (pane === next) {
      closePane();
      return;
    }
    setPane(next);
    setView("page");
    setFocusedArea("explorer");
    if (window.matchMedia("(max-width: 999px)").matches) setTerminalOpen(false);
    requestAnimationFrame(() => paneElement.current?.focus());
    refresh();
    if (next === "changes") {
      setDirectory(root);
      select(null);
    }
  };
  return (
    <ReturnToChat
      value={() => {
        if (window.matchMedia("(max-width: 999px)").matches) {
          setPane(null);
          setTerminalOpen(false);
        }
        setFocusedArea("page");
        requestAnimationFrame(() =>
          document
            .querySelector<HTMLElement>("[data-composer-editor]")
            ?.focus(),
        );
      }}
    >
      <div
        className={`${styles.workarea} ${isWorkspace && pane ? styles.withPane : ""} ${isWorkspace && terminalOpen && status.data?.features?.host_terminal ? styles.withTerminal : ""}`}
      >
        <header className={styles.workToolbar}>
          {navigation}
          {thread.data?.thread.role === "coordinator" && <CoordinatorMark />}
          <h1 className={styles.workspaceTitle}>
            {threadId
              ? conversationTitle(
                  thread.data?.thread,
                  composers.get(threadId)?.localInputs,
                )
              : isWorkspace
                ? "New conversation"
                : memoryMode
                  ? "Memory"
                  : location.pathname === "/archived"
                    ? "Archived conversations"
                    : "Settings"}
          </h1>
          {isWorkspace && (
            <div className={styles.panelTools} aria-label="Workbench views">
              {status.data?.features?.host_files && (
                <Button
                  variant="ghost"
                  size="icon"
                  title="Files"
                  aria-label="Files"
                  aria-pressed={pane === "files"}
                  onClick={() => show("files")}
                >
                  <Folder weight={pane === "files" ? "duotone" : "regular"} />
                </Button>
              )}
              {status.data?.features?.host_git && (
                <Button
                  variant="ghost"
                  size="icon"
                  title="Changes"
                  aria-label="Changes"
                  aria-pressed={pane === "changes"}
                  onClick={() => show("changes")}
                >
                  <GitDiff
                    weight={pane === "changes" ? "duotone" : "regular"}
                  />
                </Button>
              )}
              {status.data?.features?.host_terminal && (
                <TerminalAction
                  label="Terminal"
                  shortcut={terminalShortcutLabels().toggle}
                  aria-pressed={terminalOpen}
                  onClick={toggleTerminal}
                >
                  <TerminalWindow
                    weight={terminalOpen ? "duotone" : "regular"}
                  />
                </TerminalAction>
              )}
            </div>
          )}
          {profile}
        </header>
        <div className={styles.layout}>
          <div className={styles.center}>
            <div
              ref={pageElement}
              tabIndex={-1}
              onFocusCapture={() => setFocusedArea("page")}
              onPointerDown={() => setFocusedArea("page")}
              className={`${styles.page} a13n-scrollbar ${!threadId && !memoryMode ? styles.documentPage : ""}`}
            >
              <OpenHostFile
                value={
                  status.data?.features?.host_files && !projectLoading
                    ? (target) => {
                        setPane("files");
                        if (window.matchMedia("(max-width: 999px)").matches)
                          setTerminalOpen(false);
                        void open(target);
                      }
                    : undefined
                }
              >
                {children}
              </OpenHostFile>
            </div>
          </div>
          {isWorkspace && pane && (
            <div
              className={styles.resizeHandle}
              role="separator"
              aria-label="Resize supporting panel"
              aria-orientation="vertical"
              aria-valuemin={28}
              aria-valuemax={70}
              aria-valuenow={expanded ? 70 : width}
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                  event.preventDefault();
                  setExpanded(false);
                  setWidth(
                    Math.max(
                      28,
                      Math.min(
                        70,
                        width + (event.key === "ArrowLeft" ? 4 : -4),
                      ),
                    ),
                  );
                }
              }}
              onPointerDown={(event) => {
                event.currentTarget.setPointerCapture(event.pointerId);
                resizeStart.current = {
                  x: event.clientX,
                  width: expanded ? 70 : width,
                  available: event.currentTarget.parentElement!.clientWidth,
                };
                setExpanded(false);
              }}
              onPointerMove={(event) => {
                const start = resizeStart.current;
                if (start?.available)
                  setWidth(
                    Math.max(
                      28,
                      Math.min(
                        70,
                        start.width +
                          ((start.x - event.clientX) / start.available) * 100,
                      ),
                    ),
                  );
              }}
              onPointerUp={() => {
                resizeStart.current = null;
              }}
              onLostPointerCapture={() => {
                resizeStart.current = null;
              }}
            />
          )}
          {isWorkspace && pane && (
            <aside
              style={{ "--panel-width": `${width}%` } as CSSProperties}
              ref={paneElement}
              tabIndex={-1}
              onFocusCapture={() =>
                setFocusedArea(view === "page" ? "explorer" : "native")
              }
              onPointerDown={() =>
                setFocusedArea(view === "page" ? "explorer" : "native")
              }
              className={`${styles.pane} ${expanded ? styles.expandedPane : ""} a13n-scrollbar`}
              aria-label="File explorer"
            >
              <header className={styles.paneHeader}>
                <div className={styles.panelTools}>
                  {view !== "page" ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      title={pane === "files" ? directory : root}
                      onClick={() => {
                        cancelOpening();
                        setView("page");
                        setFocusedArea("explorer");
                      }}
                    >
                      <ArrowLeft />
                      {pane === "files" ? "Back to files" : "Back to changes"}
                    </Button>
                  ) : (
                    <strong>{pane === "files" ? "Files" : "Changes"}</strong>
                  )}
                </div>
                <div className={styles.actions}>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Share explorer view"
                    title="Share explorer view"
                    disabled={!nativeTarget()}
                    onClick={() => {
                      const target = nativeTarget();
                      if (target)
                        setPaneLink(
                          new URL(
                            pageLink({ target, root_thread_id: threadId })!,
                            window.location.origin,
                          ).href,
                        );
                    }}
                  >
                    <LinkSimple />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Refresh files and changes"
                    onClick={refresh}
                  >
                    <ArrowClockwise />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={
                      expanded ? "Restore drawer width" : "Expand drawer"
                    }
                    title={expanded ? "Restore drawer width" : "Expand drawer"}
                    onClick={() => setExpanded(!expanded)}
                  >
                    {expanded ? <ArrowsInSimple /> : <ArrowsOutSimple />}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="Close file explorer"
                    onClick={closePane}
                  >
                    <X />
                  </Button>
                </div>
              </header>
              {(fileTabs.length > 0 || selected) && (
                <nav className={styles.fileTabs} aria-label="Open files">
                  {fileTabs.map((tab) => (
                    <span key={tab}>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-pressed={view === "file" && tab === path}
                        title={tab}
                        onClick={() => void open(tab)}
                      >
                        {fileTabs.filter(
                          (item) => basename(item) === basename(tab),
                        ).length > 1
                          ? tab
                          : basename(tab)}
                        {buffers.get(tab)?.dirty ? " · Unsaved" : ""}
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`Close file view: ${tab}`}
                        onClick={() => {
                          setFileTabs((tabs) =>
                            tabs.filter((item) => item !== tab),
                          );
                          if (tab === path) {
                            const remaining = fileTabs.filter(
                              (item) => item !== tab,
                            );
                            const adjacent =
                              remaining[
                                Math.min(
                                  fileTabs.indexOf(tab),
                                  remaining.length - 1,
                                )
                              ];
                            if (adjacent) void open(adjacent);
                            else {
                              setPath("");
                              setView("page");
                              setFocusedArea("explorer");
                            }
                          }
                        }}
                      >
                        <X />
                      </Button>
                    </span>
                  ))}
                  {selected && (
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-pressed={view === "diff"}
                      onClick={() => {
                        cancelOpening();
                        setView("diff");
                        setFocusedArea("native");
                        setPane("changes");
                      }}
                    >
                      {basename(selected.path)} · Changes
                    </Button>
                  )}
                </nav>
              )}
              {(error || projects.error || thread.error || opening) && (
                <div className={styles.paneFeedback}>
                  <ErrorNotice
                    error={error || projects.error || thread.error}
                  />
                  {opening && <p role="status">Opening path…</p>}
                </div>
              )}
              {!projectLoading &&
                directory &&
                (pane === "files" || view === "page") && (
                  <div className={styles.location}>
                    {pane === "changes" && (
                      <strong title={root}>{project?.name}</strong>
                    )}
                    {localRoots.length > 1 && (
                      <nav
                        className={styles.projectFolders}
                        aria-label="Working folders"
                      >
                        {localRoots.map((folder) => (
                          <Button
                            key={folder}
                            size="sm"
                            variant="ghost"
                            title={folder}
                            aria-pressed={root === folder}
                            onClick={() => {
                              cancelOpening();
                              setRoot(folder);
                              setDirectory(folder);
                              setView("page");
                              setFocusedArea("explorer");
                              select(null);
                              setError(null);
                            }}
                          >
                            <Folder />
                            {basename(folder)}
                          </Button>
                        ))}
                      </nav>
                    )}
                    {pane === "files" && (
                      <div className={styles.folderNavigation}>
                        <Button
                          size="sm"
                          variant="outline"
                          aria-label="Up one level"
                          title={
                            canGoUp
                              ? `Up to ${parentDirectory}`
                              : fileRoot
                                ? "At the project root"
                                : "At the filesystem root"
                          }
                          disabled={!canGoUp}
                          onClick={() => void open(parentDirectory)}
                        >
                          <ArrowUp />
                          Up
                        </Button>
                        <nav
                          ref={folderTrail}
                          className={styles.breadcrumbs}
                          aria-label="Current folder"
                        >
                          <ol>
                            {directoryTrail.map((part, index) => (
                              <li key={part}>
                                {index > 0 && <CaretRight aria-hidden="true" />}
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  title={part}
                                  aria-current={
                                    part === directory ? "location" : undefined
                                  }
                                  onClick={() => void open(part)}
                                >
                                  {index === 0 && <Folder aria-hidden="true" />}
                                  <span>
                                    {part === fileRoot &&
                                    localRoots.length === 1 &&
                                    project
                                      ? project.name
                                      : basename(part)}
                                  </span>
                                </Button>
                              </li>
                            ))}
                          </ol>
                        </nav>
                      </div>
                    )}
                  </div>
                )}
              {view !== "page" && (
                <div
                  ref={editorElement}
                  tabIndex={-1}
                  className={`${styles.editorPage} a13n-scrollbar`}
                  onFocusCapture={() => setFocusedArea("native")}
                  onPointerDown={() => setFocusedArea("native")}
                >
                  {view === "file" && path && (
                    <OpenHostFile value={(target) => void open(target)}>
                      <FileView
                        key={path}
                        path={path}
                        line={fileLine}
                        onBufferChange={renderBuffers}
                        threadId={threadId}
                        refresh={refresh}
                        open={(target) => void open(target)}
                      />
                    </OpenHostFile>
                  )}
                  {view === "diff" && selected && (
                    <DiffView
                      key={`${selected.repository_path}:${selected.path}:${selected.comparison}`}
                      selection={selected}
                      choices={diffChoices}
                      navigate={select}
                      threadId={threadId}
                      openFile={(target, line) => void open(target, line)}
                    />
                  )}
                </div>
              )}
              {paneLink && (
                <TextField
                  label="Same-instance view link (access key not included)"
                  value={paneLink}
                  onChange={() => {}}
                />
              )}

              {projectLoading ? (
                <p className={styles.empty} role="status">
                  Loading project…
                </p>
              ) : !(pane === "files" ? directory : root) ? (
                <p className={styles.empty}>
                  {project
                    ? "This project has no folders configured."
                    : "Open a conversation in a project to see its files and changes."}
                </p>
              ) : !status.data?.features?.host_files ? (
                <div className={styles.empty}>
                  <h3>Native sharing is disabled</h3>
                  <p>
                    The server has not enabled computer sharing. Conversations
                    remain available; this view cannot enable native access.
                  </p>
                </div>
              ) : (
                <>
                  <div
                    hidden={view !== "page"}
                    className={`${styles.paneBody} a13n-scrollbar`}
                  >
                    {pane === "files" ? (
                      <Files
                        directory={directory}
                        roots={localRoots}
                        path={path}
                        open={(target) => void open(target)}
                        refresh={refresh}
                        openTerminal={
                          status.data?.features?.host_terminal && projectId
                            ? openTerminal
                            : undefined
                        }
                      />
                    ) : (
                      <Changes
                        path={root}
                        selected={selected}
                        onChoices={setDiffChoices}
                        select={(next, choices) => {
                          cancelOpening();
                          select(next);
                          if (choices) setDiffChoices(choices);
                          if (next) {
                            setView("diff");
                            setFocusedArea("native");
                          }
                        }}
                        openFile={(target) => {
                          setPane("files");
                          void open(target);
                        }}
                      />
                    )}
                  </div>
                </>
              )}
            </aside>
          )}
        </div>
        {terminalOpened && status.data?.features?.host_terminal && (
          <div
            className={styles.terminalDock}
            onFocusCapture={() => setFocusedArea("terminal")}
            onPointerDown={() => setFocusedArea("terminal")}
          >
            <TerminalPanel
              visible={isWorkspace && terminalOpen && !projectLoading}
              directory={localRoots.includes(root) ? root : projectRoot}
              projectId={project?.project_id ?? ""}
              projectName={project?.name}
              request={terminalRequest}
              threadId={threadId}
              openFile={(target, line) => void open(target, line)}
              selected={terminalId}
              onActive={setActiveTerminalId}
              select={(id) => {
                setTerminalId(id);
                setFocusedArea(id ? "terminal" : "page");
              }}
              collapse={() => {
                setTerminalOpen(false);
                focusCenter();
                refresh();
              }}
              unauthorized={unauthorized}
            />
          </div>
        )}
      </div>
    </ReturnToChat>
  );
}
