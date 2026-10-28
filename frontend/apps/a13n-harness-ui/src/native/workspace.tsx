import { ViewLink } from "./view-link";
import {
  useContext,
  useId,
  useEffect,
  useRef,
  useState,
  useReducer,
  type ReactNode,
  type CSSProperties,
} from "react";
import { flushSync } from "react-dom";
import { matchPath, useLocation, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
  useMediaQuery,
} from "a13n-ui";
import {
  DotsThree,
  Folder,
  FileText,
  Plus,
  Globe,
  GitDiff,
  X,
  ArrowClockwise,
  TerminalWindow,
  ArrowLeft,
  ArrowUp,
  CaretRight,
  SidebarSimple,
  PencilSimple,
  SlidersHorizontal,
  ShareNetwork,
  ChatCircle,
} from "@phosphor-icons/react";
import { ApiError, result, type Schema } from "../transport/client";
import { useProjects, useStatus, useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import {
  FileBuffers,
  basename,
  breadcrumbs,
  parentPath,
  withinRoot,
} from "./buffer";
import { Files } from "./files";
import { EnvironmentMonitor } from "./environment-monitor";
import { FileView } from "./file-view";
import { Changes, DiffView, type DiffSelection } from "./changes";
import styles from "./native.module.css";
import { TerminalPanel, type TerminalRequest } from "./terminal";
import { terminalShortcut } from "./terminal-shortcuts";
import { BrowserPanel } from "../browser/panel";
import { BrowserActivity } from "../browser/activity";
import { usePanelSize } from "./panel-size";
import { panelResize } from "./panel-resize";
import {
  emptyTabs,
  workspaceTabs,
  type WorkspaceTabs,
  type WorkspaceTab,
  type TabContent,
} from "./workspace-tabs";
import { ReturnToChat } from "./capture";
import { useThread } from "../conversations/queries";
import { ComposerDrafts } from "../conversations/composer";
import { conversationTitle } from "../conversations/local-input";
import { nativeLink, pageLink } from "../shell/page-links";
import { OpenHostFile } from "../conversations/tool-call";
import { DebugOnly } from "../shell/debug";
import { CoordinatorMark } from "../conversations/coordinator-icon";
import { readPreference, writePreference } from "../shell/preferences";

const PAGE_TITLES: Record<string, string> = {
  "/": "New chat",
  "/new": "New chat",
  "/memory": "Memory",
  "/usage": "Usage",
  "/archived": "Archived chats",
  "/setup": "Set up Harness",
  "/projects": "Projects",
  "/settings": "General settings",
  "/settings/agents": "Agents",
  "/settings/models": "Models",
  "/settings/environments": "Environments",
  "/settings/accounts": "Accounts & API keys",
  "/settings/connections": "Connections",
  "/settings/resources": "Configuration files",
  "/settings/source": "Edit configuration",
};

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
  const navigate = useNavigate();
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
  const projectLoading = projects.isPending || (!!threadId && thread.isPending);
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
  const hasSupportingViews = !!(
    status.data?.features?.host_files ||
    status.data?.features?.host_git ||
    status.data?.features?.browser ||
    status.data?.features?.host_terminal
  );
  const [tabLayout, setTabLayout] = useState(
    () => readPreference("native-layout", "sidebar") === "tabs",
  );
  const [nativeActive, setNativeActive] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => readPreference("native-panel", "open") === "closed",
  );
  const narrow = useMediaQuery("(max-width: 999px)");
  const [narrowSidebar, setNarrowSidebar] = useState(false);
  const singleView =
    hasSupportingViews &&
    !sidebarCollapsed &&
    (tabLayout || (narrow && !narrowSidebar));
  const supportingExpanded =
    hasSupportingViews && !sidebarCollapsed && (!singleView || nativeActive);
  const layoutTransition = useRef<ViewTransition | null>(null);
  const layoutRevision = useRef(0);
  const animateLayout = (
    change: () => void,
    motion?: "panel-close" | "panel-open",
  ) => {
    const revision = ++layoutRevision.current;
    layoutTransition.current?.skipTransition();
    if (
      !document.startViewTransition ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      change();
      return;
    }
    const transition = document.startViewTransition(() => {
      if (layoutRevision.current === revision) flushSync(change);
    });
    if (motion) transition.types?.add(motion);
    layoutTransition.current = transition;
    // Skipping snapshots does not cancel an older transition's update callback.
    void transition.ready.catch(() => {});
  };
  const toggleSupportingPanel = () => {
    animateLayout(
      () => {
        setTabLayout(false);
        setNarrowSidebar(narrow);
        setSidebarCollapsed(supportingExpanded);
        if (supportingExpanded) focusCenter();
        else {
          applyTab(activeTab);
          setNativeActive(true);
          requestAnimationFrame(() => paneElement.current?.focus());
        }
      },
      supportingExpanded ? "panel-close" : "panel-open",
    );
  };
  const workspaceId = useId();
  useEffect(() => {
    writePreference("native-layout", tabLayout ? "tabs" : "sidebar");
  }, [tabLayout]);
  useEffect(() => {
    writePreference("native-panel", sidebarCollapsed ? "closed" : "open");
  }, [sidebarCollapsed]);
  const [width, setWidth] = usePanelSize("a13n.native.width", 44, 0, 100);
  const resizeStart = useRef<{
    pointerId: number;
    x: number;
    width: number;
    available: number;
    kind: ReturnType<typeof panelResize>["kind"];
  } | null>(null);
  const [resizing, setResizing] = useState(false);
  const [, renderBuffers] = useReducer((n: number) => n + 1, 0);
  const [fileLine, setFileLine] = useState<number>();
  const [terminalRequest, setTerminalRequest] = useState<TerminalRequest>();
  const [view, setView] = useState<"page" | "file" | "diff">("page");
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
  const [pane, setPane] = useState<
    "files" | "changes" | "browser" | "terminal" | null
  >(null);
  const tabSequence = useRef(0);
  const nextTabId = () => `view-${++tabSequence.current}`;
  const [tabs, dispatchTabs] = useReducer(workspaceTabs, emptyTabs("new-0"));
  const activeTab = tabs.items.find((tab) => tab.id === tabs.active)!;
  const terminalVisible =
    isWorkspace &&
    pane === "terminal" &&
    !sidebarCollapsed &&
    (!singleView || nativeActive) &&
    !projectLoading;
  const viewTabsElement = useRef<HTMLDivElement>(null);
  const [tabsOverflow, setTabsOverflow] = useState({
    left: false,
    right: false,
  });
  useEffect(() => {
    const element = viewTabsElement.current;
    if (!element) return;
    const measure = () => {
      const left = element.scrollLeft > 1;
      const right =
        element.scrollWidth - element.clientWidth - element.scrollLeft > 1;
      setTabsOverflow((previous) =>
        previous.left === left && previous.right === right
          ? previous
          : { left, right },
      );
    };
    const observer =
      typeof ResizeObserver === "undefined"
        ? undefined
        : new ResizeObserver(measure);
    observer?.observe(element);
    for (const child of element.children) observer?.observe(child);
    element.addEventListener("scroll", measure, { passive: true });
    measure();
    return () => {
      observer?.disconnect();
      element.removeEventListener("scroll", measure);
    };
  }, [
    isWorkspace,
    singleView,
    sidebarCollapsed,
    status.data?.features?.host_files,
    status.data?.features?.host_git,
    status.data?.features?.browser,
    status.data?.features?.host_terminal,
    tabs.items,
  ]);
  useEffect(() => {
    viewTabsElement.current
      ?.querySelector<HTMLElement>(
        '[aria-selected="true"], [aria-pressed="true"]',
      )
      ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [nativeActive, tabs.active, singleView, narrow]);
  const [directory, setDirectory] = useState("");
  const [root, setRoot] = useState("");
  const [path, setPath] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [opening, setOpening] = useState(false);
  const [selected, setSelected] = useState<DiffSelection | null>(null);
  const [diffChoices, setDiffChoices] = useState<DiffSelection[]>([]);
  const projectViews = useRef(
    new Map<
      string,
      {
        root: string;
        directory: string;
        path: string;
        tabs: WorkspaceTabs;
        terminalId: string;
      }
    >(),
  );
  const openingRequest = useRef<AbortController | null>(null);
  const [appliedProject, setAppliedProject] = useState<string | null>(null);
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
    if (!trail) return;
    const revealCurrentFolder = () => {
      trail.scrollLeft = trail.scrollWidth;
      trail.dataset.clipped = String(trail.scrollLeft > 0);
    };
    revealCurrentFolder();
    const observer = new ResizeObserver(revealCurrentFolder);
    observer.observe(trail);
    return () => observer.disconnect();
  }, [directory, pane, view]);
  useEffect(() => {
    if (projectLoading) return;
    const context = `${projectId ?? ""}:${projectRoot}`;
    if (appliedProject === context) return;
    if (appliedProject) {
      projectViews.current.set(appliedProject, {
        root,
        directory,
        path,
        tabs,
        terminalId,
      });
      if (projectViews.current.size > 16)
        projectViews.current.delete(projectViews.current.keys().next().value!);
    }
    const saved = projectViews.current.get(context);
    setAppliedProject(context);
    openingRequest.current?.abort();
    setOpening(false);
    setError(null);
    setRoot(saved?.root ?? projectRoot);
    setDirectory(saved?.directory ?? projectRoot);
    setPath(saved?.path ?? "");
    const restored = saved?.tabs ?? emptyTabs(nextTabId());
    dispatchTabs({ type: "restore", state: restored });
    applyTab(restored.items.find((tab) => tab.id === restored.active)!);
    setFileLine(undefined);
    setTerminalRequest(undefined);
    setTerminalId(saved?.terminalId ?? "");
  }, [projectId, projectRoot, projectLoading]);
  useEffect(() => {
    setOpening(false);
    return () => openingRequest.current?.abort();
  }, [location.pathname, location.search]);
  const applyTab = (tab: TabContent) => {
    cancelOpening();
    setError(null);
    setFileLine(undefined);
    if (tab.kind === "new") {
      setPane(null);
      setSelected(null);
      setDiffChoices([]);
      setView("page");
      setFocusedArea("explorer");
    } else if (tab.kind === "terminal") {
      setPane("terminal");
      setTerminalOpened(true);
      setView("page");
      setFocusedArea("terminal");
    } else if (tab.kind === "browser") {
      setPane("browser");
      setView("page");
      setFocusedArea("native");
    } else if (tab.kind === "changes") {
      setPane("changes");
      setRoot(tab.root);
      setDirectory(tab.root);
      setSelected(tab.selection);
      setDiffChoices(tab.choices);
      setView(tab.selection ? "diff" : "page");
      setFocusedArea(tab.selection ? "native" : "explorer");
    } else {
      setPane("files");
      setRoot(tab.root);
      if (tab.kind === "file") {
        setPath(tab.path);
        setDirectory(parentPath(tab.path));
        setView("file");
        setFocusedArea("native");
      } else {
        setDirectory(tab.directory);
        setView("page");
        setFocusedArea("explorer");
      }
    }
  };
  const navigateTab = (content: TabContent) => {
    dispatchTabs({ type: "navigate", content });
    applyTab(content);
  };
  const revealTab = (
    content: TabContent,
    replace = activeTab.kind === "new",
  ) => {
    dispatchTabs({
      type: "open",
      tab: { ...content, id: nextTabId() },
      replace,
    });
    applyTab(content);
    setNativeActive(true);
    setSidebarCollapsed(false);
  };
  const activateTab = (tab: WorkspaceTab) => {
    dispatchTabs({ type: "select", id: tab.id });
    applyTab(tab);
    setNativeActive(true);
    setSidebarCollapsed(false);
  };
  const addTab = () => {
    revealTab({ kind: "new" }, false);
  };
  const closeTab = (id: string) => {
    const action = { type: "close" as const, id, emptyId: nextTabId() };
    const next = workspaceTabs(tabs, action);
    dispatchTabs(action);
    if (tabs.active === id) {
      applyTab(next.items.find((tab) => tab.id === next.active)!);
      requestAnimationFrame(() => {
        viewTabsElement.current
          ?.querySelector<HTMLElement>('[aria-selected="true"]')
          ?.focus();
      });
    }
  };
  const select = (selection: DiffSelection | null, choices = diffChoices) => {
    setSelected(selection);
    setDiffChoices(choices);
    if (activeTab.kind === "changes")
      dispatchTabs({
        type: "update",
        id: activeTab.id,
        content: { ...activeTab, selection, choices },
      });
  };
  const open = async (target: string, line?: number) => {
    setSidebarCollapsed(false);
    openingRequest.current?.abort();
    if (!pane) setPane("files");
    setNativeActive(true);
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
            "This isn't a regular file. Use its menu in the file list to inspect, move or delete it.",
          );
      }
      if (controller.signal.aborted) return;
      const nextRoot = root || (isDirectory ? target : parentPath(target));
      if (isDirectory)
        navigateTab({ kind: "files", directory: target, root: nextRoot });
      else
        revealTab(
          { kind: "file", path: target, root: nextRoot },
          activeTab.kind === "new" || activeTab.kind === "files",
        );
      setFileLine(line);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure);
    } finally {
      if (!controller.signal.aborted) setOpening(false);
    }
  };
  useEffect(() => {
    // Project restoration must commit before a link captures its root and active tab.
    if (
      !isWorkspace ||
      projectLoading ||
      appliedProject !== `${projectId ?? ""}:${projectRoot}`
    )
      return;
    const link = nativeLink(location.search);
    if (link.pane && link.path && status.data?.features?.host_files) {
      setSidebarCollapsed(false);
      setNativeActive(true);
      setPane(link.pane);
      setFocusedArea("native");
      if (link.pane === "changes") {
        revealTab({
          kind: "changes",
          root: link.path,
          selection:
            link.diffPath && link.comparison
              ? {
                  repository_path: link.path,
                  path: link.diffPath,
                  comparison: link.comparison,
                }
              : null,
          choices: [],
        });
      } else void open(link.path);
    }
    if (link.terminal && status.data?.features?.host_terminal) {
      setTerminalId(link.terminal);
      setFocusedArea("terminal");
      revealTab({ kind: "terminal" });
    }
    // Navigation never creates a session, claims control or adds model input.
    // Explicit file/Changes links override defaults; terminals remain Project-scoped.
  }, [
    isWorkspace,
    projectLoading,
    appliedProject,
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
      setNativeActive(false);
      setFocusedArea("page");
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
                terminalVisible &&
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
    terminalVisible,
    terminalId,
    activeTerminalId,
    onFocus,
  ]);
  const focusCenter = () => {
    openingRequest.current?.abort();
    setOpening(false);
    setNativeActive(false);
    setFocusedArea("page");
    requestAnimationFrame(() => pageElement.current?.focus());
  };
  const openTerminal = (cwd: string) => {
    if (!projectId) return;
    setTerminalRequest({ cwd, projectId });
    revealTab({ kind: "terminal" });
  };
  const toggleTerminal = () => {
    cancelOpening();
    if (terminalVisible) {
      setSidebarCollapsed(true);
      focusCenter();
    } else revealTab({ kind: "terminal" });
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
    : (PAGE_TITLES[location.pathname] ??
      (matchPath("/projects/:projectId", location.pathname)
        ? "Project settings"
        : "Page not found"));
  const ToolbarTitle = isWorkspace || threadId ? "h1" : "span";
  useEffect(() => {
    document.title = `${title} · Harness UI`;
    return () => {
      document.title = "Harness UI";
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
      (pane || terminalVisible)
    )
      refresh();
    previousRun.current = { threadId, state: runState };
  }, [threadId, runState, pane, terminalVisible]);
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
  const show = (next: "files" | "changes" | "browser" | "terminal") => {
    revealTab(
      next === "files"
        ? {
            kind: "files",
            directory: directory || projectRoot,
            root: root || projectRoot,
          }
        : next === "changes"
          ? {
              kind: "changes",
              root: root || projectRoot,
              selection: null,
              choices: [],
            }
          : { kind: next },
    );
    refresh();
  };
  const resizePanel = (
    nextWidth: number,
    available: number,
    restoreWidth: number,
  ) => {
    const next = panelResize(nextWidth, available);
    const start = resizeStart.current;
    if (next.kind === "resize") {
      setWidth(next.width);
      if (!start || start.kind === "resize") return;
    }
    if (start) {
      if (start.kind === next.kind) return;
      start.kind = next.kind;
    }
    animateLayout(
      () => {
        if (next.kind !== "resize") setWidth(restoreWidth);
        if (next.kind === "tabs") {
          setSidebarCollapsed(false);
          setTabLayout(true);
          setNarrowSidebar(false);

          setNativeActive(true);
          setFocusedArea(
            pane === "terminal"
              ? "terminal"
              : view === "page"
                ? "explorer"
                : "native",
          );
          requestAnimationFrame(() => paneElement.current?.focus());
        } else {
          setSidebarCollapsed(next.kind === "close");
          setTabLayout(false);
          setNarrowSidebar(narrow);
          if (next.kind === "close") focusCenter();
        }
      },
      next.kind === "close"
        ? "panel-close"
        : sidebarCollapsed
          ? "panel-open"
          : undefined,
    );
  };
  const nativeFeedback = (error || projects.error || thread.error) && (
    <div className={styles.paneFeedback}>
      <ErrorNotice error={error || projects.error || thread.error} />
    </div>
  );
  const viewChoices = [
    {
      key: "files" as const,
      label: "Files",
      description: "Browse and open a file",
      icon: Folder,
      available: status.data?.features?.host_files,
    },
    {
      key: "changes" as const,
      label: "Changes",
      description: "Review working tree changes",
      icon: GitDiff,
      available: status.data?.features?.host_git,
    },
    {
      key: "terminal" as const,
      label: "Terminal",
      description: "Open shared terminal sessions",
      icon: TerminalWindow,
      available: status.data?.features?.host_terminal,
    },
    {
      key: "browser" as const,
      label: "Browser",
      description: "View the host browser",
      icon: Globe,
      available: status.data?.features?.browser,
    },
  ].filter((choice) => choice.available);
  const workspaceViews = [
    ...(singleView
      ? [
          {
            key: "conversation",
            label: "Chat",
            icon: ChatCircle,
            tab: null,
          },
        ]
      : []),
    ...tabs.items.map((tab) => ({
      key: tab.id,
      label:
        tab.kind === "new"
          ? "New tab"
          : tab.kind === "file"
            ? `${tabs.items.some((other) => other.kind === "file" && other.path !== tab.path && basename(other.path) === basename(tab.path)) ? tab.path : basename(tab.path)}${buffers.get(tab.path)?.dirty ? " · Unsaved" : ""}`
            : tab.kind === "changes" && tab.selection
              ? `${basename(tab.selection.path)} · Changes`
              : tab.kind === "files"
                ? "Files"
                : tab.kind === "changes"
                  ? "Changes"
                  : tab.kind === "terminal"
                    ? "Terminal"
                    : "Browser",
      icon:
        tab.kind === "new"
          ? Plus
          : tab.kind === "file"
            ? FileText
            : tab.kind === "files"
              ? Folder
              : tab.kind === "changes"
                ? GitDiff
                : tab.kind === "terminal"
                  ? TerminalWindow
                  : Globe,
      tab,
    })),
  ];
  const activeView = nativeActive || !singleView ? tabs.active : "conversation";
  const splitLayout =
    isWorkspace && !singleView && !sidebarCollapsed && hasSupportingViews;
  return (
    <ReturnToChat
      value={() => {
        setNativeActive(false);
        setFocusedArea("page");
        requestAnimationFrame(() =>
          document
            .querySelector<HTMLElement>("[data-composer-editor]")
            ?.focus(),
        );
      }}
    >
      <div
        className={`${styles.workarea} ${singleView ? styles.tabLayout : ""}`}
      >
        <header
          className={`${styles.workToolbar} ${!isWorkspace && !threadId ? styles.documentToolbar : ""}`}
        >
          {navigation}
          {thread.data?.thread.role === "coordinator" && <CoordinatorMark />}
          <div className={styles.workspaceHeading}>
            {resolvedProject && (
              <span className={styles.projectBreadcrumb}>
                {resolvedProject.name}
              </span>
            )}
            <ToolbarTitle className={styles.workspaceTitle}>
              {title}
            </ToolbarTitle>
          </div>
          {isWorkspace && threadId && status.data?.features?.browser && (
            <BrowserActivity
              key={threadId}
              threadId={threadId}
              occupied={
                sidebarCollapsed ||
                singleView ||
                pane !== null ||
                view !== "page"
              }
              visible={
                !sidebarCollapsed &&
                pane === "browser" &&
                (!singleView || nativeActive)
              }
              reveal={() => revealTab({ kind: "browser" })}
              open={() => show("browser")}
            />
          )}
          {profile}
          {threadId && (
            <Menu>
              <MenuTrigger
                render={<Button variant="ghost" size="icon-sm" />}
                aria-label="Chat actions"
                title="Chat actions"
              >
                <DotsThree />
              </MenuTrigger>
              <MenuPopup align="end">
                {(
                  [
                    ["rename", "Rename", PencilSimple],
                    ["details", "Chat details", SlidersHorizontal],
                    ["share", "Share", ShareNetwork],
                  ] as const
                ).map(([action, label, Icon]) => (
                  <MenuItem
                    key={action}
                    onClick={() => {
                      const search = new URLSearchParams(location.search);
                      search.set("dialog", action);
                      navigate({
                        pathname: location.pathname,
                        search: search.toString(),
                      });
                    }}
                  >
                    <Icon />
                    {label}
                  </MenuItem>
                ))}
              </MenuPopup>
            </Menu>
          )}
          {isWorkspace && hasSupportingViews && (
            <Button
              variant="ghost"
              size="icon"
              aria-label={
                supportingExpanded
                  ? "Close supporting panel"
                  : "Open supporting panel"
              }
              title={
                supportingExpanded
                  ? "Close supporting panel"
                  : "Open supporting panel"
              }
              aria-expanded={supportingExpanded}
              onClick={toggleSupportingPanel}
            >
              <SidebarSimple mirrored />
            </Button>
          )}
        </header>
        <div
          className={`${styles.layout} ${splitLayout ? styles.splitLayout : ""}`}
          style={{ "--panel-width": `${width}%` } as CSSProperties}
          data-resizing={resizing || undefined}
          onPointerMove={(event) => {
            const start = resizeStart.current;
            if (start && start.pointerId === event.pointerId && start.available)
              resizePanel(
                start.width +
                  ((start.x - event.clientX) / start.available) * 100,
                start.available,
                start.width,
              );
          }}
          onPointerUp={(event) => {
            if (resizeStart.current?.pointerId !== event.pointerId) return;
            resizeStart.current = null;
            setResizing(false);
          }}
          onPointerCancel={(event) => {
            if (resizeStart.current?.pointerId !== event.pointerId) return;
            resizeStart.current = null;
            setResizing(false);
          }}
          onLostPointerCapture={(event) => {
            if (resizeStart.current?.pointerId !== event.pointerId) return;
            resizeStart.current = null;
            setResizing(false);
          }}
        >
          {isWorkspace && hasSupportingViews && (
            <nav
              className={styles.workspaceTabs}
              aria-label="Workbench views"
              hidden={sidebarCollapsed}
            >
              <div
                ref={viewTabsElement}
                className={styles.panelTools}
                role="tablist"
                aria-label="Workspace views"
                aria-busy={projectLoading}
                data-overflow-left={tabsOverflow.left || undefined}
                data-overflow-right={tabsOverflow.right || undefined}
                onKeyDown={(event) => {
                  const tabs = Array.from(
                    event.currentTarget.querySelectorAll<HTMLButtonElement>(
                      '[role="tab"]',
                    ),
                  );
                  const index = tabs.indexOf(event.target as HTMLButtonElement);
                  if (index < 0) return;
                  const next =
                    event.key === "ArrowRight"
                      ? (index + 1) % tabs.length
                      : event.key === "ArrowLeft"
                        ? (index + tabs.length - 1) % tabs.length
                        : event.key === "Home"
                          ? 0
                          : event.key === "End"
                            ? tabs.length - 1
                            : -1;
                  if (next < 0) return;
                  event.preventDefault();
                  tabs[next].click();
                  tabs[next].focus();
                }}
              >
                {workspaceViews.map(({ key, label, icon: Icon, tab }) => (
                  <span
                    className={styles.workspaceTab}
                    key={key}
                    data-active={activeView === key || undefined}
                  >
                    <Button
                      variant="ghost"
                      size="sm"
                      role="tab"
                      id={`${workspaceId}-${key}-tab`}
                      aria-controls={`${workspaceId}-${key === "conversation" ? "conversation" : "native"}`}
                      aria-selected={activeView === key}
                      disabled={!!tab && projectLoading}
                      tabIndex={activeView === key ? 0 : -1}
                      title={tab?.kind === "file" ? tab.path : label}
                      onClick={() => {
                        if (tab) activateTab(tab);
                        else {
                          cancelOpening();
                          setNativeActive(false);
                          setFocusedArea("page");
                        }
                      }}
                    >
                      <Icon /> <span>{label}</span>
                    </Button>
                    {tab && (
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Close tab: ${label}`}
                        title={`Close ${label}`}
                        disabled={projectLoading}
                        onClick={() => closeTab(tab.id)}
                      >
                        <X />
                      </Button>
                    )}
                  </span>
                ))}
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label="New tab"
                title="New tab"
                disabled={projectLoading}
                onClick={addTab}
              >
                <Plus />
              </Button>
              <div className={styles.workspaceTools}>
                <DebugOnly>
                  <EnvironmentMonitor />
                </DebugOnly>
              </div>
            </nav>
          )}
          <div
            className={styles.center}
            id={`${workspaceId}-conversation`}
            role={isWorkspace && singleView ? "tabpanel" : undefined}
            aria-labelledby={
              isWorkspace && singleView
                ? `${workspaceId}-conversation-tab`
                : undefined
            }
            hidden={isWorkspace && singleView && nativeActive}
          >
            <div
              ref={pageElement}
              tabIndex={-1}
              onFocusCapture={() => setFocusedArea("page")}
              onPointerDown={() => {
                cancelOpening();
                setFocusedArea("page");
              }}
              className={`${styles.page} a13n-scrollbar ${isWorkspace ? styles.chatPage : location.pathname === "/memory" || /^\/(settings|setup|projects)(\/|$)/.test(location.pathname) ? styles.flushPage : !threadId && !memoryMode ? styles.documentPage : ""}`}
            >
              <OpenHostFile
                value={
                  status.data?.features?.host_files && !projectLoading
                    ? (target) => {
                        void open(target);
                      }
                    : undefined
                }
              >
                {children}
              </OpenHostFile>
            </div>
          </div>
          {isWorkspace && !pane && supportingExpanded && (
            <section
              className={`${styles.pane} ${styles.newTabPage}`}
              id={`${workspaceId}-native`}
              role="tabpanel"
              aria-labelledby={`${workspaceId}-${tabs.active}-tab`}
            >
              <div className={styles.newTabChoices}>
                <h2>Open a new tab</h2>
                <p>Choose what you want to work with.</p>
                {viewChoices.map(({ key, label, description, icon: Icon }) => (
                  <Button
                    key={key}
                    variant="ghost"
                    aria-label={label}
                    disabled={projectLoading}
                    onClick={() => show(key)}
                  >
                    <Icon />
                    <span>
                      <strong>{label}</strong>
                      <small>{description}</small>
                    </span>
                    <CaretRight />
                  </Button>
                ))}
              </div>
            </section>
          )}
          {splitLayout && (
            <div
              className={styles.resizeHandle}
              role="separator"
              aria-label="Resize supporting panel"
              aria-orientation="vertical"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={width}
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                  event.preventDefault();
                  resizePanel(
                    width + (event.key === "ArrowLeft" ? 4 : -4),
                    event.currentTarget.parentElement!.clientWidth,
                    width,
                  );
                }
              }}
              onPointerDown={(event) => {
                if (event.button !== 0 || resizeStart.current) return;
                event.preventDefault();
                // The separator unmounts when snapping; the layout owns the
                // capture until release so the same gesture can reverse it.
                event.currentTarget.parentElement!.setPointerCapture(
                  event.pointerId,
                );
                resizeStart.current = {
                  pointerId: event.pointerId,
                  x: event.clientX,
                  width,
                  available: event.currentTarget.parentElement!.clientWidth,
                  kind: "resize",
                };
                setResizing(true);
              }}
            />
          )}
          {isWorkspace && pane === "browser" && (
            <aside
              id={`${workspaceId}-native`}
              role="tabpanel"
              aria-labelledby={`${workspaceId}-${tabs.active}-tab`}
              hidden={!supportingExpanded}
              style={{ "--panel-width": `${width}%` } as CSSProperties}
              ref={paneElement}
              tabIndex={-1}
              className={styles.pane}
              aria-label="Browser panel"
            >
              {nativeFeedback}
              {opening && (
                <span role="status" className={styles.directoryLoading}>
                  Opening…
                </span>
              )}
              {supportingExpanded && (
                <BrowserPanel
                  key={threadId ?? "new"}
                  threadId={threadId}
                  fullControl={
                    thread.data?.thread.configuration
                      ?.environment_profile_id === "environment-native"
                  }
                />
              )}
            </aside>
          )}
          {isWorkspace && pane && pane !== "browser" && pane !== "terminal" && (
            <aside
              id={`${workspaceId}-native`}
              role="tabpanel"
              aria-labelledby={`${workspaceId}-${tabs.active}-tab`}
              hidden={!supportingExpanded}
              style={{ "--panel-width": `${width}%` } as CSSProperties}
              ref={paneElement}
              tabIndex={-1}
              onFocusCapture={() =>
                setFocusedArea(view === "page" ? "explorer" : "native")
              }
              onPointerDown={() =>
                setFocusedArea(view === "page" ? "explorer" : "native")
              }
              className={`${styles.pane} a13n-scrollbar`}
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
                        if (pane === "files") {
                          navigateTab({ kind: "files", directory, root });
                        } else {
                          cancelOpening();
                          select(null);
                          setView("page");
                          setFocusedArea("explorer");
                        }
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
                  <ViewLink
                    getLink={() => {
                      const target = nativeTarget();
                      return target
                        ? new URL(
                            pageLink({ target, root_thread_id: threadId })!,
                            window.location.origin,
                          ).href
                        : undefined;
                    }}
                  />
                  {view !== "file" && (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Refresh files and changes"
                      onClick={refresh}
                    >
                      <ArrowClockwise />
                    </Button>
                  )}
                </div>
              </header>
              {nativeFeedback}
              {opening && (
                <span role="status" className={styles.directoryLoading}>
                  Opening…
                </span>
              )}
              {!projectLoading &&
                directory &&
                ((view === "page" &&
                  (pane === "files" || localRoots.length > 1)) ||
                  (pane === "files" &&
                    (localRoots.length > 1 || directory !== fileRoot))) && (
                  <div className={styles.location}>
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
                              navigateTab(
                                pane === "changes"
                                  ? {
                                      kind: "changes",
                                      root: folder,
                                      selection: null,
                                      choices: [],
                                    }
                                  : {
                                      kind: "files",
                                      directory: folder,
                                      root: folder,
                                    },
                              );
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
                          onScroll={(event) => {
                            const trail = event.currentTarget;
                            trail.dataset.clipped = String(
                              trail.scrollLeft > 0,
                            );
                          }}
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

              {projectLoading ? (
                <p className={styles.empty} role="status">
                  Loading project…
                </p>
              ) : !(pane === "files" ? directory : root) ? (
                <p className={styles.empty}>
                  {project
                    ? "This project has no folders configured."
                    : "Open a chat in a project to see its files and changes."}
                </p>
              ) : !status.data?.features?.host_files ? (
                <div className={styles.empty}>
                  <h3>Computer sharing is off</h3>
                  <p>
                    Computer sharing is off on the server. Chats still work.
                    Turn it on in server settings to access files and terminals.
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
                          select(next, choices ?? diffChoices);
                          if (next) {
                            setView("diff");
                            setFocusedArea("native");
                          }
                        }}
                        openFile={(target) => void open(target)}
                      />
                    )}
                  </div>
                </>
              )}
            </aside>
          )}
          {terminalOpened && status.data?.features?.host_terminal && (
            <aside
              className={`${styles.pane} ${styles.terminalPane}`}
              ref={pane === "terminal" ? paneElement : undefined}
              tabIndex={-1}
              id={pane === "terminal" ? `${workspaceId}-native` : undefined}
              role="tabpanel"
              aria-labelledby={
                pane === "terminal"
                  ? `${workspaceId}-${tabs.active}-tab`
                  : undefined
              }
              aria-label="Terminal"
              hidden={!terminalVisible}
              onFocusCapture={() => setFocusedArea("terminal")}
              onPointerDown={() => setFocusedArea("terminal")}
            >
              {pane === "terminal" && (
                <>
                  {nativeFeedback}
                  {opening && (
                    <span role="status" className={styles.directoryLoading}>
                      Opening…
                    </span>
                  )}
                </>
              )}
              <TerminalPanel
                visible={terminalVisible}
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
                unauthorized={unauthorized}
              />
            </aside>
          )}
        </div>
      </div>
    </ReturnToChat>
  );
}
