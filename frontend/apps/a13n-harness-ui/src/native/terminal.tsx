import { lazy, Suspense, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { X, Plus, ArrowClockwise } from "@phosphor-icons/react";
import { useProjects, useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import styles from "./terminal.module.css";

const TerminalScreen = lazy(() => import("./terminal-screen"));
export function TerminalPanel({
  visible,
  directory,
  selected,
  select,
  collapse,
  unauthorized,
}: {
  visible: boolean;
  directory: string;
  selected: string;
  select: (id: string) => void;
  collapse: () => void;
  unauthorized: () => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const projects = useProjects();
  const sessions = useQuery({
    queryKey: ["native", "terminals"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/host/terminals", { signal })),
    enabled: visible,
  });
  const [creating, setCreating] = useState(false);
  const [closing, setClosing] = useState<Schema<"TerminalView"> | null>(null);
  const [cwd, setCwd] = useState("");
  const [project, setProject] = useState("");
  const [pending, setPending] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [height, setHeight] = useState(35);
  const created = useRef<string | null>(null);
  const resizeStart = useRef<{
    y: number;
    height: number;
    available: number;
  } | null>(null);
  const refresh = () =>
    void queries.invalidateQueries({ queryKey: ["native"] });
  const active = sessions.data?.find((item) => item.terminal_id === selected);
  const create = async (folder: string, projectId = project) => {
    if (pending) return;
    setCwd(folder);
    setAttempted(true);
    setPending(true);
    setError(null);
    try {
      const value = await result(
        client.POST("/api/host/terminals", {
          body: { cwd: folder, project_id: projectId || null },
        }),
      );
      created.current = value.terminal_id;
      queries.setQueryData<Schema<"TerminalView">[]>(
        ["native", "terminals"],
        (items = []) => [...items, value],
      );
      select(value.terminal_id);
      setCreating(false);
    } catch (failure) {
      setError(failure);
      setCreating(true);
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
  const close = async () => {
    if (!closing) return;
    setAttempted(true);
    setPending(true);
    try {
      await client.DELETE("/api/host/terminals/{terminal_id}", {
        params: { path: { terminal_id: closing.terminal_id } },
      });
      if (selected === closing.terminal_id) select("");
      setClosing(null);
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
        <strong>Terminal</strong>
        <div className={styles.actions}>
          <Button
            size="sm"
            variant="ghost"
            disabled={pending}
            title={
              directory ? `Start in ${directory}` : "Choose a starting folder"
            }
            onClick={() => {
              setProject("");
              const folder = directory || projects.data?.[0]?.roots[0] || "";
              if (folder) void create(folder, "");
              else {
                setCwd("");
                setError(null);
                setAttempted(false);
                setCreating(true);
              }
            }}
          >
            <Plus />
            New terminal
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={() => {
              setCwd(directory || projects.data?.[0]?.roots[0] || "");
              setProject("");
              setError(null);
              setAttempted(false);
              setCreating(true);
            }}
          >
            Choose folder…
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Refresh terminal sessions"
            onClick={refresh}
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
        {sessions.data?.map((item, index) => (
          <Button
            key={item.terminal_id}
            size="sm"
            variant={selected === item.terminal_id ? "outline" : "ghost"}
            aria-pressed={selected === item.terminal_id}
            title={`${item.cwd} · ${item.terminal_id}`}
            onClick={() => select(item.terminal_id)}
          >
            {item.cwd.split(/[\\/]/).filter(Boolean).at(-1) || "Terminal"}
            {sessions.data!.filter((session) => session.cwd === item.cwd)
              .length > 1
              ? ` · ${index + 1}`
              : ""}
            {item.state === "exited" ? " · Exited" : ""}
          </Button>
        ))}
      </div>
      {selected ? (
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
          <Suspense fallback={<p>Loading terminal…</p>}>
            <TerminalScreen
              key={selected}
              id={selected}
              visible={visible}
              claimCreated={() => {
                if (created.current !== selected) return false;
                created.current = null;
                return true;
              }}
              unauthorized={unauthorized}
            />
          </Suspense>
        </>
      ) : (
        <div className={styles.empty}>
          <h3>
            {sessions.data?.length ? "Choose a terminal" : "Open a terminal"}
          </h3>
          <p>
            Start a shell in your project folder, or select an existing session.
            Sessions run on the server and are shared with everyone connected.
          </p>
        </div>
      )}
      <ModalFrame
        open={creating || !!closing}
        onOpenChange={(open) => {
          if (!open && !pending) {
            setCreating(false);
            setClosing(null);
          }
        }}
        title={closing ? "End this terminal session?" : "New terminal"}
        description={
          closing
            ? "This closes the session and its native jobs for everyone. Closing only the panel does not do this."
            : "Start a shell on the server. You will control it immediately; other people can join as viewers."
        }
        closeLabel="Cancel"
      >
        {creating && (
          <div className={styles.form}>
            <ChoiceField
              label="Project context"
              value={project}
              options={[
                { value: "", label: "No Project" },
                ...(projects.data ?? []).map((item) => ({
                  value: item.project_id,
                  label: item.name,
                })),
              ]}
              onValueChange={(id) => {
                setProject(id);
                const root = projects.data?.find(
                  (item) => item.project_id === id,
                )?.roots[0];
                if (root) setCwd(root);
              }}
            />
            <TextField label="Starting folder" value={cwd} onChange={setCwd} />
          </div>
        )}
        <ErrorNotice error={error} />
        {attempted && error != null && (
          <p role="status">
            The outcome may be unknown. This action will not be repeated. Close
            this dialog and refresh the session list before deciding what to do
            next.
          </p>
        )}
        <Button
          disabled={pending || attempted || (creating && !cwd)}
          onClick={() => void (closing ? close() : create(cwd))}
        >
          {pending
            ? "Waiting…"
            : closing
              ? "End session for everyone"
              : "Start terminal"}
        </Button>
      </ModalFrame>
    </section>
  );
}
