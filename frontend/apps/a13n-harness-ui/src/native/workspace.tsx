import { useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { matchPath, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { Folder, GitDiff, X, ArrowClockwise } from "@phosphor-icons/react";
import { ApiError, result } from "../transport/client";
import { useProjects, useStatus, useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import { readPreference, writePreference } from "../shell/preferences";
import { FileBuffers, basename, breadcrumbs, parentPath } from "./buffer";
import { Files } from "./files";
import { Changes, type DiffSelection } from "./changes";
import styles from "./native.module.css";

export function NativeWorkspace({ children }: { children: ReactNode }) {
  const { client } = useTransport();
  const projects = useProjects();
  const status = useStatus();
  const queries = useQueryClient();
  const buffers = useContext(FileBuffers);
  const location = useLocation();
  const threadId = matchPath("/threads/:threadId", location.pathname)?.params
    .threadId;
  const [pane, setPane] = useState<"files" | "changes" | null>(null);
  const [directory, setDirectory] = useState(() =>
    readPreference("native-directory", ""),
  );
  const [path, setPath] = useState("");
  const [address, setAddress] = useState(directory);
  const [error, setError] = useState<unknown>(null);
  const [opening, setOpening] = useState(false);
  const [selected, select] = useState<DiffSelection | null>(null);
  const openingRequest = useRef<AbortController | null>(null);
  const refresh = () => {
    void queries.invalidateQueries({ queryKey: ["native"] });
  };
  useEffect(() => () => openingRequest.current?.abort(), []);
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
      setPath(isDirectory ? "" : target);
      setAddress(nextDirectory);
      writePreference("native-directory", nextDirectory);
      if (isDirectory) select(null);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure);
    } finally {
      if (!controller.signal.aborted) setOpening(false);
    }
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
    setPane(next);
    refresh();
    if (!directory && uniqueRoots[0]) void open(uniqueRoots[0].value);
  };
  return (
    <div className={`${styles.workarea} ${pane ? styles.withPane : ""}`}>
      <div className={styles.workToolbar} aria-label="Workbench views">
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={!pane}
          onClick={() => setPane(null)}
        >
          {threadId ? "Chat" : "Page"}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={pane === "files"}
          onClick={() => show("files")}
        >
          <Folder />
          Files
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={pane === "changes"}
          onClick={() => show("changes")}
        >
          <GitDiff />
          Changes
        </Button>
      </div>
      <div className={styles.layout}>
        <div className={`${styles.page} ${pane ? styles.pageBeside : ""}`}>
          {children}
        </div>
        {pane && (
          <aside className={styles.pane} aria-label="Native computer context">
            <header className={styles.paneHeader}>
              <div>
                <strong>{pane === "files" ? "Files" : "Changes"}</strong>
                <small>Server / container · native OS user</small>
              </div>
              <div className={styles.actions}>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Refresh native observations"
                  onClick={refresh}
                >
                  <ArrowClockwise />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Close native pane"
                  onClick={() => setPane(null)}
                >
                  <X />
                </Button>
              </div>
            </header>
            <p className={styles.authority}>
              These paths are on the server, not the browser or the Agent's
              selected Environment. Reading and editing do not add model input.
            </p>
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
                        label="Native absolute path"
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
                {opening && <p role="status">Opening native path…</p>}
                <div className={styles.paneBody}>
                  {pane === "files" ? (
                    <Files
                      directory={directory}
                      path={path}
                      threadId={threadId}
                      open={(target) => void open(target)}
                      refresh={refresh}
                    />
                  ) : (
                    <Changes
                      path={directory}
                      threadId={threadId}
                      selected={selected}
                      select={select}
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
    </div>
  );
}
