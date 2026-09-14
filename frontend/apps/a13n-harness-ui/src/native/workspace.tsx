import { useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { matchPath, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { Folder, GitDiff, X, ArrowClockwise } from "@phosphor-icons/react";
import { ApiError, result, type Schema } from "../transport/client";
import { useProjects, useStatus, useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import { readPreference, writePreference } from "../shell/preferences";
import { FileBuffers, basename, breadcrumbs, parentPath } from "./buffer";
import { Files } from "./files";
import { FileView } from "./file-view";
import { Changes, DiffView, type DiffSelection } from "./changes";
import styles from "./native.module.css";
import { TerminalPanel } from "./terminal";
import { nativeLink, pageLink } from "../shell/page-links";

export function NativeWorkspace({
  children,
  onFocus,
  unauthorized,
}: {
  children: ReactNode;
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
  const isWorkspace = location.pathname === "/" || !!threadId;
  const [view, setView] = useState<"page" | "file" | "diff">("page");
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [focusedArea, setFocusedArea] = useState<
    "page" | "native" | "explorer" | "terminal"
  >("page");
  const [terminalId, setTerminalId] = useState("");
  const [terminalOpened, setTerminalOpened] = useState(false);
  const pageElement = useRef<HTMLDivElement>(null);
  const paneElement = useRef<HTMLElement>(null);
  const editorElement = useRef<HTMLDivElement>(null);
  const [paneLink, setPaneLink] = useState("");
  const [pane, setPane] = useState<"files" | "changes" | null>(null);
  const [directory, setDirectory] = useState(() =>
    readPreference("native-directory", ""),
  );
  const [path, setPath] = useState("");
  const [fileTabs, setFileTabs] = useState<string[]>([]);
  const [address, setAddress] = useState(directory);
  const [error, setError] = useState<unknown>(null);
  const [opening, setOpening] = useState(false);
  const [selected, select] = useState<DiffSelection | null>(null);
  const openingRequest = useRef<AbortController | null>(null);
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: ["native"] });
  };
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
  const open = async (target: string) => {
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
      if (!isDirectory) {
        setPath(target);
        setView("file");
        setFocusedArea("native");
        if (window.matchMedia("(max-width: 1199px)").matches) setPane(null);
      }
      if (!isDirectory)
        setFileTabs((tabs) =>
          tabs.includes(target) ? tabs : [...tabs, target],
        );
      setAddress(nextDirectory);
      writePreference("native-directory", nextDirectory);
      if (isDirectory) setFocusedArea("explorer");
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure);
    } finally {
      if (!controller.signal.aborted) setOpening(false);
    }
  };
  useEffect(() => {
    if (!isWorkspace) return;
    const link = nativeLink(location.search);
    if (link.pane && link.path && status.data?.features?.host_files) {
      setPane(link.pane);
      setFocusedArea("native");
      if (window.matchMedia("(max-width: 1199px)").matches)
        setTerminalOpen(false);
      if (link.pane === "changes") {
        openingRequest.current?.abort();
        setOpening(false);
        setError(null);
        setDirectory(link.path);
        setAddress(link.path);
        if (link.diffPath && link.comparison) {
          setView("diff");
          if (window.matchMedia("(max-width: 1199px)").matches) setPane(null);
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
    // Personal native selection otherwise survives ordinary Thread navigation.
  }, [
    isWorkspace,
    location.search,
    status.data?.features?.host_files,
    status.data?.features?.host_terminal,
  ]);
  useEffect(() => {
    const link = nativeLink(location.search);
    if (!(link.pane && link.path) && !link.terminal) {
      setFocusedArea("page");
      setView("page");
      if (window.matchMedia("(max-width: 1199px)").matches) setPane(null);
      if (window.matchMedia("(max-width: 700px)").matches)
        setTerminalOpen(false);
    }
  }, [location.pathname, location.search]);
  const nativeTarget = (): Schema<"PageTarget"> | null =>
    pane === "files" && directory
      ? { kind: "file", path: directory }
      : pane === "changes" && directory
        ? {
            kind: "changes",
            repository_root: directory,
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
            : focusedArea === "terminal" && terminalOpen && terminalId
              ? { kind: "terminal", terminal_id: terminalId }
              : null,
    );
  }, [
    isWorkspace,
    view,
    focusedArea,
    pane,
    path,
    directory,
    selected,
    terminalOpen,
    terminalId,
    onFocus,
  ]);
  const focusCenter = () => {
    setFocusedArea(view === "page" ? "page" : "native");
    (view === "page" ? pageElement : editorElement).current?.focus();
  };
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
  const roots =
    projects.data?.flatMap((project) =>
      project.roots.map((root) => ({
        value: root,
        label: `${project.name} · ${root}`,
      })),
    ) ?? [];
  const uniqueRoots = [
    ...new Map(roots.map((root) => [root.value, root])).values(),
  ];
  const show = (next: "files" | "changes") => {
    cancelOpening();
    if (pane === next) {
      closePane();
      return;
    }
    setPane(next);
    setFocusedArea("explorer");
    if (window.matchMedia("(max-width: 1199px)").matches)
      setTerminalOpen(false);
    requestAnimationFrame(() => paneElement.current?.focus());
    refresh();
    if (!directory && uniqueRoots[0]) void open(uniqueRoots[0].value);
  };
  return (
    <div
      className={`${styles.workarea} ${isWorkspace && pane ? styles.withPane : ""} ${isWorkspace && terminalOpen && status.data?.features?.host_terminal ? styles.withTerminal : ""}`}
    >
      {isWorkspace && (
        <div className={styles.workToolbar} aria-label="Workbench views">
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={view === "page"}
            onClick={() => {
              cancelOpening();
              setView("page");
              setFocusedArea("page");
              if (window.matchMedia("(max-width: 1199px)").matches)
                setPane(null);
              if (window.matchMedia("(max-width: 700px)").matches)
                setTerminalOpen(false);
            }}
          >
            {threadId ? "Chat" : "Overview"}
          </Button>
          {status.data?.features?.host_files && (
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={pane === "files"}
              onClick={() => show("files")}
            >
              <Folder />
              Files
            </Button>
          )}
          {status.data?.features?.host_git && (
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={pane === "changes"}
              onClick={() => show("changes")}
            >
              <GitDiff />
              Changes
            </Button>
          )}
          {status.data?.features?.host_terminal && (
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={terminalOpen}
              onClick={() => {
                cancelOpening();
                setTerminalOpen(!terminalOpen);
                if (!terminalOpen) setFocusedArea("terminal");
                else focusCenter();
                setTerminalOpened(true);
                refresh();
              }}
            >
              Terminal
            </Button>
          )}
          {status.data?.features?.host_files &&
            !status.data?.features?.host_git && (
              <small
                className={styles.authority}
                title="Install Git on the server to inspect Changes. Native Files remains available."
              >
                Git unavailable
              </small>
            )}
          {!status.data?.features?.host_terminal && (
            <small
              className={styles.authority}
              title="Native PTY requires POSIX support. Computer sharing is selected at server startup; --no-share-computer disables native access."
            >
              {status.data?.features?.host_files
                ? "Native PTY unavailable on this server"
                : "Native sharing disabled by this server"}
            </small>
          )}
        </div>
      )}
      {isWorkspace && (fileTabs.length > 0 || selected) && (
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
                {basename(tab)}
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label={`Close file view: ${tab}`}
                onClick={() => {
                  setFileTabs((tabs) => tabs.filter((item) => item !== tab));
                  if (tab === path) {
                    setPath("");
                    setView("page");
                    setFocusedArea("page");
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
                if (window.matchMedia("(max-width: 1199px)").matches)
                  setPane(null);
              }}
            >
              {basename(selected.path)} · Changes
            </Button>
          )}
        </nav>
      )}
      <div className={styles.layout}>
        <div className={styles.center}>
          <div
            hidden={isWorkspace && view !== "page"}
            ref={pageElement}
            tabIndex={-1}
            onFocusCapture={() => setFocusedArea("page")}
            onPointerDown={() => setFocusedArea("page")}
            className={`${styles.page} a13n-scrollbar ${!threadId ? styles.documentPage : ""}`}
          >
            {children}
          </div>
          {isWorkspace && view !== "page" && (
            <div
              ref={editorElement}
              tabIndex={-1}
              className={`${styles.editorPage} a13n-scrollbar`}
              onFocusCapture={() => setFocusedArea("native")}
              onPointerDown={() => setFocusedArea("native")}
            >
              {view === "file" && path && (
                <FileView
                  key={path}
                  path={path}
                  threadId={threadId}
                  refresh={refresh}
                  open={(target) => void open(target)}
                />
              )}
              {view === "diff" && selected && (
                <DiffView
                  key={`${selected.repository_path}:${selected.path}:${selected.comparison}`}
                  selection={selected}
                  threadId={threadId}
                  openFile={(target) => {
                    setPane("files");
                    void open(target);
                  }}
                />
              )}
            </div>
          )}
        </div>
        {isWorkspace && pane && (
          <aside
            ref={paneElement}
            tabIndex={-1}
            onFocusCapture={() => setFocusedArea("explorer")}
            onPointerDown={() => setFocusedArea("explorer")}
            className={`${styles.pane} a13n-scrollbar`}
            aria-label="File explorer"
          >
            <header className={styles.paneHeader}>
              <div>
                <strong>{pane === "files" ? "Files" : "Changes"}</strong>
                <small title="Files on the server or container, independent of the agent's environment.">
                  Server files
                </small>
              </div>
              <div className={styles.actions}>
                <Button
                  variant="ghost"
                  size="sm"
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
                  Share view
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
                  aria-label="Close file explorer"
                  onClick={closePane}
                >
                  <X />
                </Button>
              </div>
            </header>
            {paneLink && (
              <TextField
                label="Same-instance view link (access key not included)"
                value={paneLink}
                onChange={() => {}}
              />
            )}

            {!status.data?.features?.host_files ? (
              <div className={styles.empty}>
                <h3>Native sharing is disabled</h3>
                <p>
                  The server has not enabled computer sharing. Conversations
                  remain available; this view cannot enable native access.
                </p>
              </div>
            ) : (
              <>
                <details className={styles.location} open={!directory}>
                  <summary title={directory}>
                    {directory || "Choose a server location"}
                  </summary>
                  <div className={styles.locationFields}>
                    {!!uniqueRoots.length && (
                      <ChoiceField
                        label="Project root"
                        value={
                          uniqueRoots.some((root) => root.value === directory)
                            ? directory
                            : ""
                        }
                        options={[
                          { value: "", label: "Choose a project root…" },
                          ...uniqueRoots,
                        ]}
                        onValueChange={(value) => {
                          if (value) void open(value);
                        }}
                      />
                    )}
                    <form
                      className={styles.address}
                      onSubmit={(event) => {
                        event.preventDefault();
                        void open(address);
                      }}
                    >
                      <TextField
                        label="Folder or file path"
                        value={address}
                        onChange={setAddress}
                      />
                      <Button
                        variant="outline"
                        type="submit"
                        size="sm"
                        disabled={!address || opening}
                      >
                        Open
                      </Button>
                    </form>
                    {!!directory && (
                      <nav
                        className={styles.breadcrumbs}
                        aria-label="Native path breadcrumbs"
                      >
                        {breadcrumbs(directory).map((part) => (
                          <button
                            type="button"
                            key={part}
                            title={part}
                            onClick={() => void open(part)}
                          >
                            {basename(part)}
                          </button>
                        ))}
                      </nav>
                    )}
                  </div>
                </details>
                <ErrorNotice error={error || projects.error} />
                {opening && <p role="status">Opening path…</p>}

                <div className={`${styles.paneBody} a13n-scrollbar`}>
                  {pane === "files" ? (
                    <Files
                      directory={directory}
                      path={path}
                      open={(target) => void open(target)}
                      refresh={refresh}
                    />
                  ) : (
                    <Changes
                      path={directory}
                      selected={selected}
                      select={(next) => {
                        cancelOpening();
                        select(next);
                        if (next) {
                          setView("diff");
                          setFocusedArea("native");
                          if (window.matchMedia("(max-width: 1199px)").matches)
                            setPane(null);
                        }
                      }}
                      openFile={(target) => {
                        setPane("files");
                        void open(target);
                      }}
                    />
                  )}
                  {!directory && (
                    <p>
                      Choose a configured root or enter an absolute server path.
                      No Project or Run is required for native access.
                    </p>
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
            visible={isWorkspace && terminalOpen}
            directory={directory}
            selected={terminalId}
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
  );
}
