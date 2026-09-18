import {
  Button,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
  SearchPicker,
  StatusPill,
} from "a13n-ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type CSSProperties } from "react";
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
  CaretLeftIcon,
  ChatIcon,
  DotsThreeOutlineVerticalIcon,
  InfoIcon,
  TreeStructureIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { CopyButton, IconTile } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import { AgentAvatar } from "../agents/avatar";
import { useAgent } from "../agents/queries";
import { memoriesPath } from "../memory/api";
import { useMemoryProviders } from "../memory/availability";
import { conversationQueries, invalidateConversation, runPath } from "./api";
import { Composer, RunOptions, useRunOptions } from "./composer";
import { inputText } from "./input";
import { SessionList } from "./list";
import { useConversationNotifications } from "./notifications";
import { RunInspector, SessionMap } from "./panels";
import { useRun } from "./queries";
import { ThreadQueue } from "./transcript/queue";
import styles from "./conversations.module.css";

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
  const enabled = (agents.data ?? []).filter((agent) => agent.enabled);
  const selected = enabled.find((agent) => agent.id === agentId);
  return (
    <div className={styles.startPage}>
      <div className={styles.startColumn}>
        <header className={styles.startHeader}>
          <IconTile size={44}>
            <ChatIcon size={20} aria-hidden="true" />
          </IconTile>
          <h1>{t("Start a session")}</h1>
          <p>{t("Send an agent a message and watch every step it takes.")}</p>
        </header>
        <ErrorNotice error={agents.error} retry={() => void agents.refetch()} />
        <SearchPicker
          label={t("Agent")}
          placeholder={t("Choose an agent")}
          emptyMessage={t("No matching agents")}
          value={agentId || undefined}
          onValueChange={setAgentId}
          groups={[
            {
              label: t("Agents"),
              options: enabled.map((agent) => ({
                value: agent.id,
                label: agent.name,
                description: agent.description ?? undefined,
                icon: (
                  <AgentAvatar
                    name={agent.name}
                    id={agent.id}
                    url={agent.image_url}
                    className={styles.pickerAvatar}
                  />
                ),
              })),
            },
          ]}
        />
        <Composer
          disabled={!can("agent.invoke") || !agentId}
          agentName={selected?.name}
          options={<RunOptions options={options} showAgent={false} />}
          submit={async (input) => {
            const body = {
              ...options.build(),
              agent_id: agentId,
              input,
              session_purpose: "debug" as const,
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
        />
      </div>
    </div>
  );
}

const PANEL_WIDTH_KEY = "a13n-session-panel-width";

export function SessionLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId, runId } = useParams(),
    { workspace, basePath, can } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const threads = useQuery(queries.threads(sessionId));
  const { visible: memoryVisible } = useMemoryProviders();
  const [mapOpen, setMapOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [mapTrigger, setMapTrigger] = useState<HTMLButtonElement | null>(null);
  const [inspectorTrigger, setInspectorTrigger] =
    useState<HTMLButtonElement | null>(null);
  const [panelWidth, setPanelWidth] = useState(() => {
    const stored = Number(localStorage.getItem(PANEL_WIDTH_KEY));
    return stored >= 360 ? Math.min(stored, 760) : 400;
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
  const first = threads.data?.[0];
  const debug = first?.session_purpose === "debug";
  function resize(width: number) {
    setPanelWidth(width);
    localStorage.setItem(PANEL_WIDTH_KEY, String(width));
  }
  return (
    <div
      className={styles.sessionDetail}
      data-panel-open={whichPanel !== null || undefined}
      style={{ "--panel-width": `${panelWidth}px` } as CSSProperties}
    >
      <div className={styles.sessionMain}>
        <header className={styles.sessionHeader}>
          <Link className={styles.back} to={`${basePath}/sessions`}>
            <CaretLeftIcon size={13} aria-hidden="true" />
            {t("Sessions")}
          </Link>
          <SessionIdentity debug={debug} />
          <div className={styles.sessionControls}>
            <Button
              ref={setMapTrigger}
              variant="ghost"
              size="sm"
              aria-pressed={mapOpen}
              disabled={threads.isPending || (!threads.data && threads.isError)}
              onClick={() => {
                setMapOpen((value) => !value);
                setInspectorOpen(false);
              }}
            >
              <TreeStructureIcon size={15} aria-hidden="true" />
              {t("Map")}
            </Button>
            <Button
              ref={setInspectorTrigger}
              variant="ghost"
              size="sm"
              aria-pressed={inspectorOpen && !!runId}
              disabled={!runId}
              onClick={() => {
                setInspectorOpen((value) => !value);
                setMapOpen(false);
              }}
            >
              <InfoIcon size={15} aria-hidden="true" />
              {t("Details")}
            </Button>
            <CopyButton
              value={sessionId}
              iconOnly
              copyLabel={t("Copy session ID")}
            />
            <Menu>
              <MenuTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    type="button"
                    aria-label={t("Session actions")}
                    title={t("Session actions")}
                  />
                }
              >
                <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
              </MenuTrigger>
              <MenuPopup align="end">
                {memoryVisible &&
                  threadId &&
                  threads.data?.some(
                    (thread) =>
                      thread.id === threadId && thread.session_id === sessionId,
                  ) && (
                    <MenuItem
                      render={
                        <a
                          href={memoriesPath(basePath, {
                            scope: "thread",
                            subject_id: threadId,
                          })}
                        />
                      }
                    >
                      <ArrowSquareOutIcon size={14} aria-hidden="true" />
                      {t("Thread memories")}
                    </MenuItem>
                  )}
                {can("trace.read") && (
                  <MenuItem
                    render={
                      <Link to={`${basePath}/traces?session_id=${sessionId}`} />
                    }
                  >
                    <ArrowSquareOutIcon size={14} aria-hidden="true" />
                    {t("Open in traces")}
                  </MenuItem>
                )}
              </MenuPopup>
            </Menu>
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
          {renderedPanel === "inspector" && runId && (
            <RunInspector
              runId={runId}
              width={panelWidth}
              onWidthChange={resize}
              onClose={() => {
                setInspectorOpen(false);
                inspectorTrigger?.focus();
              }}
            />
          )}
          {renderedPanel === "map" && (
            <SessionMap
              threads={threads.data ?? []}
              width={panelWidth}
              onWidthChange={resize}
              onClose={() => {
                setMapOpen(false);
                mapTrigger?.focus();
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

/** The session is known by what was asked and which agent answered. */
function SessionIdentity({ debug }: { debug: boolean }) {
  const { runId, sessionId, threadId } = useParams();
  const { basePath } = useWorkspace(),
    { t } = useTranslation();
  const run = useRun(runId);
  const valid =
    run.data?.session_id === sessionId && run.data?.thread_id === threadId;
  const agent = useAgent(
    valid && !run.data?.configuration_draft_id ? run.data?.agent_id : undefined,
  );
  const title = valid
    ? inputText(run.data?.input, run.data?.input_text)
        .replace(/\s+/g, " ")
        .trim()
    : undefined;
  return (
    <div className={styles.sessionIdentity}>
      <h1 title={title || t("Session")}>{title || t("Session")}</h1>
      {agent.data && (
        <Link
          className={styles.agentChip}
          to={`${basePath}/agents/${agent.data.key}`}
        >
          <AgentAvatar
            name={agent.data.name}
            id={run.data?.agent_id}
            url={agent.data.image_url}
            className={styles.chipAvatar}
          />
          {agent.data.name}
        </Link>
      )}
      {valid && run.data && <StatePill state={run.data.status} />}
      {debug && <StatusPill variant="neutral">{t("Debug")}</StatusPill>}
    </div>
  );
}

export function ThreadLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId = "", runId } = useParams(),
    { workspace, basePath } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const thread = useQuery(queries.thread(threadId));
  if (thread.data?.configuration_draft_id)
    return (
      <Navigate
        to={`${basePath}/configuration-threads/${threadId}${runId ? `?run=${runId}` : ""}`}
        replace
      />
    );
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
          <div className={styles.emptyThread}>
            <Empty
              title={t("No runs yet")}
              description={t("This thread has not started a run.")}
            />
            <ThreadQueue thread={thread.data} canConsume />
          </div>
        )
      )}
    </>
  );
}
