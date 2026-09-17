import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { X, Plus, ArrowClockwise } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import styles from "./terminal.module.css";
import { usePanelSize } from "./panel-size";

export type TerminalRequest = { cwd: string; projectId: string };

const TerminalScreen = lazy(() => import("./terminal-screen"));
export function TerminalPanel({
  visible,
  directory,
  projectId,
  projectName,
  selected,
  select,
  onActive,
  collapse,
  unauthorized,
  request,
  threadId,
  openFile,
}: {
  visible: boolean;
  directory: string;
  projectId: string;
  projectName?: string;
  selected: string;
  select: (id: string) => void;
  onActive?: (id: string) => void;
  collapse: () => void;
  unauthorized: () => void;
  request?: TerminalRequest;
  threadId?: string;
  openFile?: (path: string, line?: number) => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const currentProject = useRef(projectId);
  currentProject.current = projectId;
  const sessions = useQuery({
    queryKey: ["native", "terminals"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/host/terminals", { signal })),
    enabled: visible,
  });
  const [closing, setClosing] = useState<Schema<"TerminalView"> | null>(null);
  const [pending, setPending] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [height, setHeight] = usePanelSize(
    "a13n.native.terminal-height",
    35,
    20,
    70,
  );
  const [visited, setVisited] = useState<string[]>([]);
  const handledRequest = useRef<TerminalRequest | undefined>(undefined);
  const created = useRef<string | null>(null);
  const resizeStart = useRef<{
    y: number;
    height: number;
    available: number;
  } | null>(null);
  const refresh = () =>
    void queries.invalidateQueries({ queryKey: ["native"] });
  const projectSessions =
    sessions.data?.filter(
      (item) => !!projectId && item.project_id === projectId,
    ) ?? [];
  const active = projectSessions.find((item) => item.terminal_id === selected);
  const activeId = active?.terminal_id ?? "";
  const retained = [
    ...visited.filter(
      (id) =>
        id !== activeId &&
        projectSessions.some((item) => item.terminal_id === id),
    ),
    ...(activeId ? [activeId] : []),
  ].slice(-3);
  useEffect(() => {
    if (activeId)
      setVisited((items) =>
        [...items.filter((id) => id !== activeId), activeId].slice(-3),
      );
  }, [activeId]);
  useEffect(() => {
    onActive?.(visible ? activeId : "");
  }, [activeId, visible, onActive]);
  useEffect(() => {
    setError(null);
    setAttempted(false);
    setClosing(null);
  }, [projectId]);
  const create = async (cwd = directory) => {
    if (pending || attempted || !cwd || !projectId) return;
    setAttempted(true);
    setPending(true);
    setError(null);
    try {
      const value = await result(
        client.POST("/api/host/terminals", {
          body: { cwd, project_id: projectId },
        }),
      );
      created.current = value.terminal_id;
      queries.setQueryData<Schema<"TerminalView">[]>(
        ["native", "terminals"],
        (items = []) => [...items, value],
      );
      if (currentProject.current === projectId) {
        select(value.terminal_id);
        setAttempted(false);
      }
    } catch (failure) {
      if (currentProject.current !== projectId) return;
      setError(failure);
      if (
        failure instanceof ApiError &&
        failure.status >= 400 &&
        failure.status < 500
      )
        setAttempted(false);
    } finally {
      setPending(false);
      refresh();
    }
  };
  useEffect(() => {
    if (
      !request ||
      handledRequest.current === request ||
      request.projectId !== projectId
    )
      return;
    handledRequest.current = request;
    if (pending || attempted) {
      setError(
        new Error(
          "A terminal action is unresolved. Inspect its outcome before opening another terminal.",
        ),
      );
      return;
    }
    void create(request.cwd);
  }, [request, projectId]);
  const close = async () => {
    if (!closing) return;
    setAttempted(true);
    setPending(true);
    try {
      await result(
        client.DELETE("/api/host/terminals/{terminal_id}", {
          params: { path: { terminal_id: closing.terminal_id } },
        }),
      );
      if (selected === closing.terminal_id) select("");
      setClosing(null);
      setAttempted(false);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
      refresh();
    }
  };
  return (
    <section
      hidden={!visible}
      className={styles.panel}
      style={{ flexBasis: `${height}%` }}
      aria-label="Shared native terminals"
    >
      <div
        className={styles.resizeHandle}
        role="separator"
        aria-label="Resize terminal"
        aria-orientation="horizontal"
        aria-valuemin={20}
        aria-valuemax={70}
        aria-valuenow={height}
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            event.preventDefault();
            setHeight((value) =>
              Math.max(
                20,
                Math.min(70, value + (event.key === "ArrowUp" ? 5 : -5)),
              ),
            );
          }
        }}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          resizeStart.current = {
            y: event.clientY,
            height,
            available:
              event.currentTarget.closest("section")!.parentElement!
                .parentElement!.clientHeight,
          };
        }}
        onPointerMove={(event) => {
          const start = resizeStart.current;
          if (start && start.available)
            setHeight(
              Math.round(
                Math.max(
                  20,
                  Math.min(
                    70,
                    start.height +
                      ((start.y - event.clientY) / start.available) * 100,
                  ),
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
      <header className={styles.header}>
        <strong>Terminal{projectName ? ` · ${projectName}` : ""}</strong>
        <div className={styles.actions}>
          <Button
            size="sm"
            variant="ghost"
            disabled={pending || attempted || !projectId || !directory}
            title={
              directory
                ? `Start in ${directory}`
                : "Open a project conversation first"
            }
            onClick={() => void create()}
          >
            <Plus />
            New terminal
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Refresh terminal sessions"
            onClick={() => {
              refresh();
              setAttempted(false);
              setError(null);
            }}
          >
            <ArrowClockwise />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Collapse terminal panel"
            onClick={collapse}
          >
            <X />
          </Button>
        </div>
      </header>
      <ErrorNotice
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
      <div className={styles.sessionBar}>
        {projectSessions.map((item, index) => (
          <Button
            key={item.terminal_id}
            size="sm"
            variant={selected === item.terminal_id ? "outline" : "ghost"}
            aria-pressed={selected === item.terminal_id}
            title={`${item.cwd} · ${item.terminal_id}`}
            onClick={() => select(item.terminal_id)}
          >
            {item.cwd.split(/[\\/]/).filter(Boolean).at(-1) || "Terminal"}
            {projectSessions.filter((session) => session.cwd === item.cwd)
              .length > 1
              ? ` · ${index + 1}`
              : ""}
            {item.state === "exited" ? " · Exited" : ""}
          </Button>
        ))}
      </div>
      {active ? (
        <>
          <div className={styles.identity}>
            <span>
              {active
                ? active.cwd
                : "This terminal is no longer listed. Refresh to check its status."}
            </span>
            {active && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setError(null);
                  setAttempted(false);
                  setClosing(active);
                }}
              >
                End session
              </Button>
            )}
          </div>
        </>
      ) : (
        <div className={styles.empty}>
          <h3>
            {!projectId
              ? "No project selected"
              : projectSessions.length
                ? "Choose a terminal"
                : "Open a terminal"}
          </h3>
          <p>
            {selected && sessions.isSuccess
              ? "This terminal is not available in the current project. Open a conversation in its project to view it."
              : projectId
                ? "Start a shell in this project's folder, or select one of its existing sessions."
                : "Open a conversation in a project to see its terminals."}
          </p>
        </div>
      )}
      {retained.map((id) => (
        <div
          key={id}
          className={styles.retainedScreen}
          hidden={!visible || id !== activeId}
        >
          <Suspense fallback={<p>Loading terminal…</p>}>
            <TerminalScreen
              id={id}
              visible={visible && id === activeId}
              threadId={threadId}
              openFile={openFile}
              claimCreated={() => {
                if (created.current !== id) return false;
                created.current = null;
                return true;
              }}
              unauthorized={unauthorized}
            />
          </Suspense>
        </div>
      ))}
      <ErrorNotice error={error} />
      {attempted && error != null && !closing && (
        <p role="status">
          The outcome may be unknown. Refresh the session list before starting
          another terminal.
        </p>
      )}
      <ModalFrame
        open={!!closing}
        onOpenChange={(open) => {
          if (!open && !pending) {
            setClosing(null);
          }
        }}
        title="End this terminal session?"
        description="This closes the session and its native jobs for everyone. Closing only the panel does not do this."
        closeLabel="Cancel"
      >
        <ErrorNotice error={error} />
        {attempted && error != null && (
          <p role="status">
            The outcome may be unknown. This action will not be repeated. Close
            this dialog and refresh the session list before deciding what to do
            next.
          </p>
        )}
        <Button disabled={pending || attempted} onClick={() => void close()}>
          {pending ? "Waiting…" : "End session for everyone"}
        </Button>
      </ModalFrame>
    </section>
  );
}
