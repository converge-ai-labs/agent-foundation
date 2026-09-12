import { lazy, Suspense, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { X, Plus, ArrowClockwise } from "@phosphor-icons/react";
import { useProjects, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
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
  const [height, setHeight] = useState(40);
  const refresh = () =>
    void queries.invalidateQueries({ queryKey: ["native"] });
  const active = sessions.data?.find((item) => item.terminal_id === selected);
  const create = async () => {
    setAttempted(true);
    setPending(true);
    setError(null);
    try {
      const value = await result(
        client.POST("/api/host/terminals", {
          body: { cwd, project_id: project || null },
        }),
      );
      select(value.terminal_id);
      setCreating(false);
    } catch (failure) {
      setError(failure);
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
      style={{ height: `${height}dvh` }}
      aria-label="Shared native terminals"
    >
      <header className={styles.header}>
        <strong>Terminal</strong>
        <div className={styles.actions}>
          <label className={styles.height}>
            Height
            <input
              aria-label="Terminal panel height"
              type="range"
              min={25}
              max={75}
              value={height}
              onChange={(event) => setHeight(Number(event.target.value))}
            />
          </label>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setCwd(directory || projects.data?.[0]?.roots[0] || "");
              setProject("");
              setError(null);
              setAttempted(false);
              setCreating(true);
            }}
          >
            <Plus />
            New terminal
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
        {sessions.data?.map((item) => (
          <Button
            key={item.terminal_id}
            size="sm"
            variant={selected === item.terminal_id ? "outline" : "ghost"}
            aria-pressed={selected === item.terminal_id}
            title={`${item.cwd} · ${item.terminal_id}`}
            onClick={() => select(item.terminal_id)}
          >
            {item.terminal_id.slice(-8)} · {item.state}
          </Button>
        ))}
      </div>
      {selected ? (
        <>
          <div className={styles.identity}>
            <span>
              {active
                ? `Initial cwd: ${active.cwd} · ${active.project_id ? `Project: ${active.project_id}` : "No Project"} · ${active.shell}`
                : `Session: ${selected}. Refresh to check whether it still exists; it is never recreated automatically.`}
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
                Close process
              </Button>
            )}
          </div>
          <Suspense fallback={<p>Loading terminal…</p>}>
            <TerminalScreen
              key={selected}
              id={selected}
              visible={visible}
              unauthorized={unauthorized}
            />
          </Suspense>
        </>
      ) : (
        <div className={styles.empty}>
          <h3>
            {sessions.data?.length
              ? "Select a session to observe"
              : "No terminal selected"}
          </h3>
          <p>
            Start deliberately at an absolute server path, or reattach to an
            existing session. Changing conversations does not retarget it.
            Collapse and detach do not close processes; server restart does.
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
        title={
          closing ? "Close shared terminal process?" : "New native terminal"
        }
        description={
          closing
            ? "This closes the session and its native jobs for everyone. Closing only the panel does not do this."
            : "Starts an interactive shell as the server OS user, not in the Agent's selected Environment. The initial directory is not a live cwd probe."
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
            <TextField
              label="Initial native working directory"
              value={cwd}
              onChange={setCwd}
            />
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
          onClick={() => void (closing ? close() : create())}
        >
          {pending
            ? "Waiting…"
            : closing
              ? "Close process for everyone"
              : "Start terminal"}
        </Button>
      </ModalFrame>
    </section>
  );
}
