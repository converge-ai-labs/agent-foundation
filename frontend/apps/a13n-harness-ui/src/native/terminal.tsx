import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useTranslation } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { Plus, ArrowClockwise, Trash } from "@phosphor-icons/react";
import { TerminalAction } from "./terminal-action";
import { terminalShortcut, terminalShortcutLabels } from "./terminal-shortcuts";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import styles from "./terminal.module.css";

export type TerminalRequest = { cwd: string; projectId: string };

const blockedTerminalAction = new Error(
  "Check the previous terminal action before opening another terminal.",
);
const TerminalScreen = lazy(() => import("./terminal-screen"));
export function TerminalPanel({
  visible,
  directory,
  projectId,
  projectName,
  selected,
  select,
  onActive,
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
  unauthorized: () => void;
  request?: TerminalRequest;
  threadId?: string;
  openFile?: (path: string, line?: number) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const shortcuts = terminalShortcutLabels();
  const newTerminal = useRef<HTMLButtonElement>(null);
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
  const [visited, setVisited] = useState<string[]>([]);
  const handledRequest = useRef<TerminalRequest | undefined>(undefined);
  const created = useRef<string | null>(null);
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
    if (visible && !activeId) newTerminal.current?.focus();
  }, [visible, activeId]);
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
      setError(blockedTerminalAction);
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
      onKeyDownCapture={(event) => {
        if (!visible || event.defaultPrevented) return;
        const action = terminalShortcut(event.nativeEvent);
        if (action !== "previous" && action !== "next") return;
        if (!projectSessions.length) return;
        event.preventDefault();
        event.stopPropagation();
        const index = projectSessions.findIndex(
          (item) => item.terminal_id === activeId,
        );
        const next =
          index < 0
            ? 0
            : (index + (action === "next" ? 1 : -1) + projectSessions.length) %
              projectSessions.length;
        select(projectSessions[next].terminal_id);
      }}
      hidden={!visible}
      className={styles.panel}
      aria-label={t("Shared native terminals")}
    >
      <header className={styles.header}>
        <strong>
          {t("Terminal")}
          {projectName ? ` - ${projectName}` : ""}
        </strong>
        <div className={styles.actions}>
          <TerminalAction
            ref={newTerminal}
            label={t("New terminal")}
            shortcut={shortcuts.create}
            disabled={pending || attempted || !projectId || !directory}
            description={
              directory
                ? t("Start in {{directory}}", { directory })
                : t("Open a project chat first")
            }
            onClick={() => void create()}
          >
            <Plus />
          </TerminalAction>
          <TerminalAction
            label={t("Refresh terminal sessions")}
            onClick={() => {
              refresh();
              setAttempted(false);
              setError(null);
            }}
          >
            <ArrowClockwise />
          </TerminalAction>
          {active && (
            <TerminalAction
              label={t("End session")}
              description={t("Closes this terminal for everyone")}
              onClick={() => {
                setError(null);
                setAttempted(false);
                setClosing(active);
              }}
            >
              <Trash />
            </TerminalAction>
          )}
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
            title={t(
              "{{cwd}} - {{id}} - Switch sessions: {{previous}} / {{next}}",
              {
                cwd: item.cwd,
                id: item.terminal_id,
                previous: shortcuts.previous,
                next: shortcuts.next,
              },
            )}
            onClick={() => select(item.terminal_id)}
          >
            {item.cwd.split(/[\\/]/).filter(Boolean).at(-1) || t("Terminal")}
            {projectSessions.filter((session) => session.cwd === item.cwd)
              .length > 1
              ? ` - ${index + 1}`
              : ""}
            {item.state === "exited" ? ` - ${t("Exited")}` : ""}
          </Button>
        ))}
      </div>
      {active ? (
        <>
          <div className={styles.identity}>
            <span>
              {active
                ? active.cwd
                : t(
                    "This terminal is no longer listed. Refresh to check its status.",
                  )}
            </span>
          </div>
        </>
      ) : (
        <div className={styles.empty}>
          <h3>
            {!projectId
              ? t("No project selected")
              : projectSessions.length
                ? t("Choose a terminal")
                : t("Open a terminal")}
          </h3>
          <p>
            {selected && sessions.isSuccess
              ? t(
                  "This terminal belongs to another project. Open a chat in that project to view it.",
                )
              : projectId
                ? t(
                    "Start a terminal in this project's folder, or open an existing session.",
                  )
                : t("Open a chat in a project to see its terminals.")}
          </p>
        </div>
      )}
      {retained.map((id) => (
        <div
          key={id}
          className={styles.retainedScreen}
          hidden={!visible || id !== activeId}
        >
          <Suspense fallback={<p>{t("Loading terminal…")}</p>}>
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
      <ErrorNotice
        error={error}
        message={
          error === blockedTerminalAction
            ? t(
                "Check the previous terminal action before opening another terminal.",
              )
            : undefined
        }
      />
      {attempted && error != null && !closing && (
        <p role="status">
          {t(
            "Couldn't confirm the result. Refresh the session list before starting another terminal.",
          )}
        </p>
      )}
      <ModalFrame
        bottomStickOnMobile
        open={!!closing}
        onOpenChange={(open) => {
          if (!open && !pending) {
            setClosing(null);
          }
        }}
        title={t("End this terminal session?")}
        description={t(
          "Ends this session and its jobs for everyone. To keep it running, close only the panel.",
        )}
        closeLabel={t("Cancel")}
        footer={
          <>
            <Button
              variant="outline"
              disabled={pending}
              onClick={() => setClosing(null)}
            >
              {t("Cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={pending || attempted}
              onClick={() => void close()}
            >
              {pending ? t("Waiting…") : t("End session")}
            </Button>
          </>
        }
      >
        <ErrorNotice
          error={error}
          message={
            error === blockedTerminalAction
              ? t(
                  "Check the previous terminal action before opening another terminal.",
                )
              : undefined
          }
        />
        {attempted && error != null && (
          <p role="status">
            {t(
              "Couldn't confirm the result. Nothing was retried. Close this dialog and refresh the session list before continuing.",
            )}
          </p>
        )}
      </ModalFrame>
    </section>
  );
}
