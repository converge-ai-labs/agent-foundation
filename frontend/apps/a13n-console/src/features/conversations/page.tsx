import { Button, ChoiceField } from "a13n-ui";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  Link,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";

import {
  ArrowSquareOutIcon,
  CaretRightIcon,
  ChatIcon,
  InfoIcon,
  TreeStructureIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data } from "../../shared/api";
import { Empty, ErrorNotice, Loading } from "../../shared/feedback";
import { SessionList } from "./list";
import { CopyableId } from "../../shared/copy";
import { useIdempotency } from "../../shared/idempotency";
import { conversationQueries, invalidateConversation, runPath } from "./api";
import { Composer } from "./composer";
import styles from "./conversations.module.css";
import { RunInspector } from "./inspector";
import { SessionMap } from "./session-map";
import { SessionIdentity } from "./identity";
import { useConversationNotifications } from "./notifications";
import { RunOptions, useRunOptions } from "./options";
import { ThreadQueue } from "./queue";
import { memoriesPath } from "../memory/api";
import { useMemoryProviders } from "../memory/availability";

export function ConversationsPage() {
  const { sessionId } = useParams();
  const location = useLocation();
  const nested = !!sessionId || /\/sessions\/new\/?$/.test(location.pathname);
  const notifications = useConversationNotifications();
  return nested ? (
    <div className={`${styles.sessionStage} a13n-scrollbar`}>
      <ErrorNotice
        error={notifications.error}
        retry={notifications.reconnect}
      />
      <Outlet />
    </div>
  ) : (
    <SessionList
      notificationError={notifications.error}
      reconnect={notifications.reconnect}
    />
  );
}

export function NewConversation() {
  const { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    client = useClient(),
    navigate = useNavigate(),
    cache = useQueryClient(),
    [search] = useSearchParams();
  const [agentId, setAgentId] = useState(search.get("agent") ?? ""),
    options = useRunOptions(),
    idempotency = useIdempotency();
  const agents = useQuery({
    queryKey: ["agent-picker", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/agents", {
            params: {
              path: { workspace: workspace.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  return (
    <div className={styles.startPage}>
      <div className={styles.newConversation}>
        <div className={styles.welcome}>
          <ChatIcon size={32} weight="light" />
          <h2>{t("What would you like to work on?")}</h2>
          <p>
            {t("Choose an agent, share an idea, and start making progress.")}
          </p>
        </div>
        <ErrorNotice error={agents.error} />
        <ChoiceField
          placeholder={t("Choose an agent")}
          value={agentId}
          onValueChange={setAgentId}
          label={t("Agent")}
          hideLabel
          options={(agents.data ?? [])
            .filter((agent) => agent.enabled)
            .map((agent) => ({ value: agent.id, label: agent.name }))}
        />
        <Composer
          disabled={!can("agent.invoke") || !agentId}
          label={t("Start session")}
          submit={async (input) => {
            const body = {
              ...options.build(),
              agent_id: agentId,
              input,
              ...(search.get("session")
                ? { session_id: search.get("session")! }
                : {}),
            };
            const receipt = data(
              await client.http.POST("/api/v1/workspaces/{workspace}/runs", {
                params: {
                  path: { workspace: workspace.id },
                  header: commandHeaders(
                    workspace.id,
                    idempotency.forBody(body),
                  ),
                },
                body,
              }),
            );
            void invalidateConversation(cache, workspace.id, {
              sessionId: receipt.session_id,
              threadId: receipt.thread_id,
              runId: receipt.run_id,
            });
            navigate(runPath(basePath, receipt));
          }}
        >
          <RunOptions options={options} showAgent={false} />
        </Composer>
      </div>
    </div>
  );
}

const PANEL_WIDTH_KEY = "a13n-session-panel-width";
const PANEL_MIN = 280;
const PANEL_MAX = 720;

export function SessionLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId, runId } = useParams(),
    { workspace, basePath } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const threads = useQuery(queries.threads(sessionId));
  const { visible: memoryVisible } = useMemoryProviders();
  const [mapOpen, setMapOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const mapTrigger = useRef<HTMLButtonElement>(null);
  const inspectorTrigger = useRef<HTMLButtonElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const [panelWidth, setPanelWidth] = useState(() => {
    const stored = Number(localStorage.getItem(PANEL_WIDTH_KEY));
    return stored >= PANEL_MIN ? Math.min(stored, PANEL_MAX) : 360;
  });
  const whichPanel: "inspector" | "map" | null =
    inspectorOpen && runId ? "inspector" : mapOpen ? "map" : null;
  const [renderedPanel, setRenderedPanel] = useState<
    "inspector" | "map" | null
  >(null);
  useEffect(() => {
    if (whichPanel) {
      setRenderedPanel(whichPanel);
      return;
    }
    if (!renderedPanel) return;
    const timer = setTimeout(() => setRenderedPanel(null), 200);
    return () => clearTimeout(timer);
  }, [whichPanel, renderedPanel]);
  const panelOpen = whichPanel !== null;
  const first = threads.data?.[0];
  function resizeTo(width: number) {
    const max = Math.min(
      PANEL_MAX,
      Math.floor((body.current?.clientWidth ?? 1200) * 0.55),
    );
    const next = Math.round(Math.min(Math.max(width, PANEL_MIN), max));
    setPanelWidth(next);
    return next;
  }
  function startResize(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const handle = event.currentTarget,
      startX = event.clientX,
      startWidth = panelWidth;
    let latest = startWidth;
    const select = document.body.style.userSelect;
    handle.dataset.dragging = "";
    if (body.current) body.current.dataset.resizing = "";
    document.body.style.userSelect = "none";
    const move = (e: PointerEvent) => {
      latest = resizeTo(startWidth + (startX - e.clientX));
    };
    const up = () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      delete handle.dataset.dragging;
      if (body.current) delete body.current.dataset.resizing;
      document.body.style.userSelect = select;
      localStorage.setItem(PANEL_WIDTH_KEY, String(latest));
    };
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
  }
  function resizeKeys(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 64 : 16;
    const next = resizeTo(
      panelWidth + (event.key === "ArrowLeft" ? step : -step),
    );
    localStorage.setItem(PANEL_WIDTH_KEY, String(next));
  }
  return (
    <div
      ref={body}
      className={styles.sessionDetail}
      data-panel-open={panelOpen || undefined}
      style={{ "--panel-width": `${panelWidth}px` } as CSSProperties}
    >
      <div className={styles.sessionMain}>
        <header className={styles.sessionHeader}>
          <div className={styles.sessionBreadcrumb}>
            <Link className={styles.backToSessions} to={`${basePath}/sessions`}>
              {t("Sessions")}
            </Link>
            <CaretRightIcon size={12} aria-hidden="true" />
            <CopyableId value={sessionId} />
          </div>
          <SessionIdentity />
          <div className={styles.sessionControls}>
            {memoryVisible &&
              threadId &&
              threads.data?.some(
                (thread) =>
                  thread.id === threadId && thread.session_id === sessionId,
              ) && (
                <Button
                  variant="outline"
                  size="sm"
                  render={
                    <a
                      href={memoriesPath(basePath, {
                        scope: "thread",
                        subject_id: threadId,
                      })}
                      target="_blank"
                      rel="noopener noreferrer"
                    />
                  }
                >
                  <ArrowSquareOutIcon size={16} aria-hidden="true" />
                  {t("Thread memories")}
                </Button>
              )}
            <Button
              ref={mapTrigger}
              variant={mapOpen ? "secondary" : "outline"}
              size="sm"
              aria-expanded={mapOpen}
              aria-controls={mapOpen ? "session-map" : undefined}
              disabled={threads.isPending || (!threads.data && threads.isError)}
              onClick={() => {
                setMapOpen((value) => !value);
                setInspectorOpen(false);
              }}
            >
              <TreeStructureIcon size={16} />
              {t("Session map")}
            </Button>
            <Button
              ref={inspectorTrigger}
              variant={inspectorOpen ? "secondary" : "outline"}
              size="sm"
              aria-expanded={inspectorOpen && !!runId}
              aria-controls={
                inspectorOpen && runId ? "run-inspector" : undefined
              }
              disabled={!runId}
              onClick={() => {
                setInspectorOpen((value) => !value);
                setMapOpen(false);
              }}
            >
              <InfoIcon size={16} />
              {t("Run details")}
            </Button>
          </div>
        </header>
        <ErrorNotice
          error={threads.error}
          retry={() => void threads.refetch()}
        />
        <div
          className={`${styles.sessionContent} a13n-scrollbar`}
          data-session-stage
        >
          {threadId ? (
            <Outlet />
          ) : first ? (
            <Navigate to={`threads/${first.id}`} replace />
          ) : threads.isPending ? (
            <Loading variant="list" rows={3} />
          ) : (
            <Empty
              title={t("No threads yet")}
              description={t(
                "Threads created by the host application appear here.",
              )}
            />
          )}
        </div>
      </div>
      {renderedPanel && (
        <div className={styles.panelSlot}>
          {panelOpen && (
            <div
              className={styles.panelResizer}
              role="separator"
              aria-orientation="vertical"
              aria-label={t("Resize panel")}
              aria-valuenow={panelWidth}
              aria-valuemin={PANEL_MIN}
              aria-valuemax={PANEL_MAX}
              tabIndex={0}
              onPointerDown={startResize}
              onKeyDown={resizeKeys}
            />
          )}
          {renderedPanel === "inspector" && runId && (
            <RunInspector
              runId={runId}
              onClose={() => {
                setInspectorOpen(false);
                inspectorTrigger.current?.focus();
              }}
            />
          )}
          {renderedPanel === "map" && (
            <SessionMap
              threads={threads.data ?? []}
              onClose={() => {
                setMapOpen(false);
                mapTrigger.current?.focus();
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

export function ThreadLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId = "", runId } = useParams(),
    { workspace } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const thread = useQuery(queries.thread(threadId));
  const selected =
    runId ?? thread.data?.current_run_id ?? thread.data?.head_run_id;
  if (thread.data && thread.data.session_id !== sessionId)
    return (
      <ErrorNotice
        error={
          new Error(t("This thread does not belong to this conversation."))
        }
      />
    );
  return (
    <>
      <ErrorNotice
        error={thread.error}
        retry={() => {
          void thread.refetch();
        }}
      />
      {thread.isPending ? (
        <Loading variant="detail" />
      ) : runId ? (
        <Outlet />
      ) : selected ? (
        <Navigate to={`runs/${selected}`} replace />
      ) : (
        thread.data && (
          <>
            <Empty
              title={t("No runs yet")}
              description={t("This thread has not started a run.")}
            />
            <ThreadQueue thread={thread.data} canConsume />
          </>
        )
      )}
    </>
  );
}
