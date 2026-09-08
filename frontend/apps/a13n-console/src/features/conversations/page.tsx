import { useState } from "react";
import {
  Link,
  Outlet,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Select } from "a13n-ui";
import { MessageSquare, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data } from "../../shared/api";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Pagination, useCursor } from "../../shared/collection";
import { conversationApi, runPath } from "./api";
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
            query: { cursor: page.cursor },
          },
          signal,
        })
        .then(data),
  });
  return (
    <Page
      title={t("Conversations")}
      description={t(
        "Every session keeps its threads, branches, and agent runs together.",
      )}
      actions={
        can("agent.invoke") && (
          <Button
            variant="primary"
            icon={<Plus size={15} />}
            onClick={() => navigate("new")}
          >
            {t("New conversation")}
          </Button>
        )
      }
    >
      <ErrorNotice
        error={notifications.error}
        retry={notifications.reconnect}
      />
      <ErrorNotice
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
      {sessions.isPending ? (
        <Loading />
      ) : sessions.data?.items.length ? (
        <>
          <div className={styles.sessions}>
            {sessions.data.items.map((session) => (
              <Link
                className={styles.sessionCard}
                key={session.id}
                to={session.id}
              >
                <MessageSquare size={20} />
                <div>
                  <strong>{session.id}</strong>
                  <small>
                    {t("Updated")} <Timestamp value={session.updated_at} />
                  </small>
                </div>
              </Link>
            ))}
          </div>
          <Pagination page={page} next={sessions.data.next_cursor} />
        </>
      ) : (
        !sessions.error && (
          <Empty
            title={t("Start a conversation")}
            description={t("Choose an agent and send its first message.")}
          />
        )
      )}
    </Page>
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
    <Page
      title={t("New conversation")}
      description={t(
        "Start with an agent. You can branch the conversation as your work develops.",
      )}
    >
      <div className={styles.newConversation}>
        <div className={styles.welcome}>
          <MessageSquare size={32} strokeWidth={1.4} />
          <h2>{t("What would you like to work on?")}</h2>
          <p>
            {t(
              "Your agent brings its model, instructions, and tools to this conversation.",
            )}
          </p>
        </div>
        <ErrorNotice error={agents.error} />
        <Select
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
          label={t("Start conversation")}
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
    </Page>
  );
}

export function SessionLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId } = useParams(),
    { workspace, can } = useWorkspace(),
    client = useClient(),
    api = conversationApi(client, workspace.id);
  const notifications = useConversationNotifications(threadId);
  const threads = useQuery({
    queryKey: ["session-threads", workspace.id, sessionId],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.threads(sessionId, signal, cursor)),
  });
  return (
    <Page
      title={t("Conversation")}
      description={sessionId}
      actions={
        can("agent.invoke") && (
          <Link
            to={`/workspaces/${workspace.id}/sessions/new?session=${sessionId}`}
          >
            {t("New thread")}
          </Link>
        )
      }
    >
      <ErrorNotice
        error={notifications.error}
        retry={notifications.reconnect}
      />
      <ErrorNotice error={threads.error} retry={() => void threads.refetch()} />
      <div className={styles.conversationLayout}>
        <nav className={styles.threads} aria-label={t("Threads")}>
          <h2>{t("Threads")}</h2>
          {threads.isPending && <Loading />}
          {threads.data?.map((thread) => (
            <Link
              key={thread.id}
              className={styles.threadLink}
              aria-current={thread.id === threadId ? "page" : undefined}
              to={`threads/${thread.id}`}
            >
              <MessageSquare size={14} />
              <span>
                {thread.id}
                <small>{t(thread.origin_kind)}</small>
              </span>
            </Link>
          ))}
        </nav>
        <div className={styles.threadContent}>
          {threadId ? (
            <Outlet />
          ) : (
            <Empty
              title={t("Choose a thread")}
              description={t(
                "Each thread has its own run history and pending messages.",
              )}
            />
          )}
        </div>
      </div>
    </Page>
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
  const runs = useQuery({
    queryKey: ["thread-runs", workspace.id, threadId],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.runs(threadId, signal, cursor)),
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
        error={thread.error ?? runs.error}
        retry={() => {
          void thread.refetch();
          void runs.refetch();
        }}
      />
      {thread.isPending || runs.isPending ? (
        <Loading />
      ) : (
        <>
          <div className={styles.runBar}>
            <Select
              label={t("Run history")}
              placeholder={t("Choose a run")}
              value={selected ?? ""}
              onValueChange={(id) =>
                navigate(
                  `/workspaces/${workspace.id}/sessions/${thread.data!.session_id}/threads/${threadId}/runs/${id}`,
                )
              }
              options={(runs.data ?? []).map((run) => ({
                value: run.id,
                label: `${run.id} · ${t(run.status)}`,
              }))}
            />
            {thread.data && (
              <span>
                {t("Thread version")} {thread.data.version}
              </span>
            )}
          </div>
          {runId ? (
            <Outlet />
          ) : selected ? (
            <Link className={styles.sessionCard} to={`runs/${selected}`}>
              {t("Open current run")}{" "}
              <StateBadge
                state={
                  runs.data?.find((run) => run.id === selected)?.status ??
                  "unknown"
                }
              />
            </Link>
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
