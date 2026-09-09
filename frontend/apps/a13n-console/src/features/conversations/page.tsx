import { useState } from "react";
import {
  Link,
  Navigate,
  useLocation,
  Outlet,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Select } from "a13n-ui";
import { MessageSquare, Plus, ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
import { Empty, ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Pagination, useCursor } from "../../shared/collection";
import { conversationApi, runPath } from "./api";
import { SessionIdentity } from "./identity";
import { Composer } from "./composer";
import { OptionsComposer, RunOptions, useRunOptions } from "./options";
import { ThreadQueue } from "./queue";
import { useIdempotency } from "../../shared/idempotency";
import { useConversationNotifications } from "./notifications";
import styles from "./conversations.module.css";

export function ConversationsPage() {
  const { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    client = useClient(),
    page = useCursor(),
    navigate = useNavigate();
  const notifications = useConversationNotifications();
  const sessions = useQuery({
    queryKey: ["sessions", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/sessions", {
          params: {
            path: { workspace_id: workspace.id },
            query: { cursor: page.cursor, limit: 20 },
          },
          signal,
        })
        .then(data),
  });
  const { sessionId } = useParams();
  const location = useLocation();
  const nested = !!sessionId || /\/sessions\/new\/?$/.test(location.pathname);
  return (
    <div className={styles.sessionsLayout} data-detail={nested}>
      <aside className={styles.sessionSidebar}>
        <Link
          className={styles.workspaceBack}
          to={`/workspaces/${workspace.id}/agents`}
        >
          <ArrowLeft size={14} />
          {t("Back to workspace")}
        </Link>
        <header>
          <h1>{t("Sessions")}</h1>
          {can("agent.invoke") && (
            <Button
              variant="ghost"
              size="sm"
              aria-label={t("New session")}
              icon={<Plus size={16} />}
              onClick={() => navigate("new")}
            />
          )}
        </header>
        <ErrorNotice
          error={notifications.error}
          retry={notifications.reconnect}
        />
        <ErrorNotice
          error={sessions.error}
          retry={() => void sessions.refetch()}
        />
        <nav aria-label={t("Sessions")}>
          {sessions.isPending ? (
            <Loading />
          ) : (
            sessions.data?.items.map((session) => (
              <SessionLink
                key={session.id}
                session={session}
                selected={session.id === sessionId}
              />
            ))
          )}
          {!sessions.isPending && !sessions.data?.items.length && (
            <p className={styles.sessionHint}>
              {t("Your sessions will appear here.")}
            </p>
          )}
        </nav>
        {sessions.data && (
          <Pagination page={page} next={sessions.data.next_cursor} />
        )}
      </aside>
      <div className={styles.sessionStage} data-session-stage>
        {nested ? <Outlet /> : <NewConversation />}
      </div>
    </div>
  );
}
function SessionLink({
  session,
  selected,
}: {
  session: Schema["SessionResource"];
  selected: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const api = conversationApi(client, workspace.id);
  const preview = useQuery({
    queryKey: ["session-preview", workspace.id, session.id, session.updated_at],
    staleTime: 60_000,
    queryFn: async ({ signal }) => {
      const threads = await api.threads(session.id, signal);
      const thread = threads.items[0];
      const id = thread?.current_run_id ?? thread?.head_run_id;
      return id ? api.run(id, signal) : null;
    },
  });
  return (
    <Link
      to={session.id}
      className={styles.sessionLink}
      aria-current={selected ? "page" : undefined}
      title={preview.data?.input_text || t("Untitled session")}
    >
      <div>
        <strong>{preview.data?.input_text || t("Untitled session")}</strong>
        <small>
          <Timestamp value={session.updated_at} relative />
        </small>
      </div>
      <p>
        {preview.data?.output_text
          ? preview.data.output_text
              .replace(/[`#*_>]/g, "")
              .replace(/\s+/g, " ")
          : preview.data
            ? t(`state.${preview.data.status}`, {
                defaultValue: preview.data.status,
              })
            : t("Loading…")}
      </p>
    </Link>
  );
}

export function NewConversation() {
  const { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
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
          .GET("/api/v1/workspaces/{workspace_id}/agents", {
            params: {
              path: { workspace_id: workspace.id },
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
          <MessageSquare size={32} strokeWidth={1.4} />
          <h2>{t("What would you like to work on?")}</h2>
          <p>
            {t("Choose an agent, share an idea, and start making progress.")}
          </p>
        </div>
        <ErrorNotice error={agents.error} />
        <Select
          variant="ghost"
          label={t("Agent")}
          placeholder={t("Choose an agent")}
          value={agentId}
          onValueChange={setAgentId}
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
              await client.http.POST("/api/v1/workspaces/{workspace_id}/runs", {
                params: {
                  path: { workspace_id: workspace.id },
                  header: commandHeaders(
                    workspace.id,
                    idempotency.forBody(body),
                  ),
                },
                body,
              }),
            );
            void cache.invalidateQueries({
              queryKey: ["sessions", workspace.id],
            });
            navigate(runPath(workspace.id, receipt));
          }}
        >
          <RunOptions options={options} showAgent={false} />
        </Composer>
      </div>
    </div>
  );
}

export function SessionLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId } = useParams(),
    { workspace, can } = useWorkspace(),
    client = useClient(),
    api = conversationApi(client, workspace.id);
  const threads = useQuery({
    queryKey: ["session-threads", workspace.id, sessionId],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.threads(sessionId, signal, cursor)),
  });
  const navigate = useNavigate();
  const first = threads.data?.[0];
  return (
    <div className={styles.sessionDetail}>
      <header className={styles.sessionHeader}>
        <SessionIdentity />
        <div className={styles.sessionControls}>
          <Link
            className={styles.backToSessions}
            to={`/workspaces/${workspace.id}/sessions`}
          >
            {t("Sessions")}
          </Link>
          <Select
            size="sm"
            variant="ghost"
            label={t("Threads")}
            placeholder={t("Thread")}
            value={threadId ?? ""}
            onValueChange={(id) => navigate(`threads/${id}`)}
            options={(threads.data ?? []).map((thread, index) => ({
              value: thread.id,
              label: `${t(thread.origin_kind === "fork" ? "Branch" : "Thread")} ${index + 1}`,
            }))}
          />
          {threadId && <RunHistory />}
          {can("agent.invoke") && (
            <Link
              className={styles.newThread}
              aria-label={t("New thread")}
              to={`/workspaces/${workspace.id}/sessions/new?session=${sessionId}`}
            >
              <Plus size={14} />
              <span>{t("New thread")}</span>
            </Link>
          )}
        </div>
      </header>
      <ErrorNotice error={threads.error} retry={() => void threads.refetch()} />
      {threadId ? (
        <Outlet />
      ) : first ? (
        <Navigate to={`threads/${first.id}`} replace />
      ) : threads.isPending ? (
        <Loading />
      ) : (
        <Empty
          title={t("No threads yet")}
          description={t("Start a thread to work with an agent.")}
        />
      )}
    </div>
  );
}

export function ThreadLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId = "", runId } = useParams(),
    { workspace, can } = useWorkspace(),
    client = useClient(),
    api = conversationApi(client, workspace.id);
  const thread = useQuery({
    queryKey: ["thread", workspace.id, threadId],
    queryFn: ({ signal }) => api.thread(threadId, signal),
  });
  const navigate = useNavigate(),
    cache = useQueryClient();
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
        <Loading />
      ) : (
        <>
          {runId ? (
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
                {can("run.continue") && (
                  <OptionsComposer
                    label={t("Start run")}
                    submit={async (intent, key) => {
                      if (!intent.agent_id)
                        throw new Error(t("Choose an agent in Run options."));
                      const receipt = data(
                        await client.http.POST(
                          "/api/v1/threads/{thread_id}/runs",
                          {
                            params: {
                              path: { thread_id: threadId },
                              header: commandHeaders(workspace.id, key),
                            },
                            body: {
                              ...intent,
                              expected_thread_version: thread.data!.version,
                            },
                          },
                        ),
                      );
                      void cache.invalidateQueries();
                      if (receipt.run)
                        navigate(runPath(workspace.id, receipt.run));
                    }}
                  />
                )}
                <ThreadQueue thread={thread.data} canConsume />
              </>
            )
          )}
        </>
      )}
    </>
  );
}

function RunHistory() {
  const { t } = useTranslation(),
    { workspace } = useWorkspace(),
    client = useClient(),
    navigate = useNavigate();
  const { sessionId = "", threadId = "", runId = "" } = useParams();
  const runs = useQuery({
    queryKey: ["thread-runs", workspace.id, threadId],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        conversationApi(client, workspace.id).runs(threadId, signal, cursor),
      ),
  });
  if (runs.error)
    return (
      <Button
        size="sm"
        variant="ghost"
        title={t("Run history could not be loaded.")}
        onClick={() => void runs.refetch()}
      >
        {t("Retry history")}
      </Button>
    );
  return (
    <Select
      label={t("Run history")}
      placeholder={t("Run history")}
      size="sm"
      variant="ghost"
      value={runId}
      onValueChange={(id) =>
        navigate(
          runPath(workspace.id, {
            session_id: sessionId,
            thread_id: threadId,
            run_id: id,
          }),
        )
      }
      options={(runs.data ?? []).map((run, index) => ({
        value: run.id,
        label: `${t("Run")} ${runs.data!.length - index} · ${t(`state.${run.status}`, { defaultValue: run.status })}`,
      }))}
    />
  );
}
