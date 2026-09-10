import { Button, ChoiceField } from "a13n-ui";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Link,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";

import { ChatIcon, PlusIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data } from "../../shared/api";
import { Empty, ErrorNotice, Loading } from "../../shared/feedback";
import { SessionList } from "./list";
import { useIdempotency } from "../../shared/idempotency";
import { conversationQueries, invalidateConversation, runPath } from "./api";
import { Composer } from "./composer";
import styles from "./conversations.module.css";
import { SessionIdentity } from "./identity";
import { useConversationNotifications } from "./notifications";
import { OptionsComposer, RunOptions, useRunOptions } from "./options";
import { ThreadQueue } from "./queue";

export function ConversationsPage() {
  const { sessionId } = useParams();
  const location = useLocation();
  const nested = !!sessionId || /\/sessions\/new\/?$/.test(location.pathname);
  const notifications = useConversationNotifications();
  return nested ? (
    <div className={`${styles.sessionStage} a13n-scrollbar`} data-session-stage>
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

export function SessionLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId } = useParams(),
    { workspace, can, basePath } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const threads = useQuery(queries.threads(sessionId));
  const navigate = useNavigate();
  const first = threads.data?.[0];
  return (
    <div className={styles.sessionDetail}>
      <header className={styles.sessionHeader}>
        <SessionIdentity />
        <div className={styles.sessionControls}>
          <Link className={styles.backToSessions} to={`${basePath}/sessions`}>
            {t("Sessions")}
          </Link>
          <ChoiceField
            placeholder={t("Thread")}
            value={threadId ?? ""}
            onValueChange={(id) => navigate(`threads/${id}`)}
            label={t("Threads")}
            hideLabel
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
              to={`${basePath}/sessions/new?session=${sessionId}`}
            >
              <PlusIcon size={14} />
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
    { workspace, can, basePath } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const thread = useQuery(queries.thread(threadId));
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
            {can("run.continue") && (
              <OptionsComposer
                label={t("Start run")}
                submit={async (intent, key) => {
                  if (!intent.agent_id)
                    throw new Error(t("Choose an agent in Run options."));
                  const receipt = data(
                    await client.http.POST("/api/v1/threads/{thread_id}/runs", {
                      params: {
                        path: { thread_id: threadId },
                        header: commandHeaders(workspace.id, key),
                      },
                      body: {
                        ...intent,
                        expected_thread_version: thread.data!.version,
                      },
                    }),
                  );
                  void invalidateConversation(cache, workspace.id, {
                    sessionId,
                    threadId,
                    runId: receipt.run?.run_id,
                  });
                  if (receipt.run) navigate(runPath(basePath, receipt.run));
                }}
              />
            )}
            <ThreadQueue thread={thread.data} canConsume />
          </>
        )
      )}
    </>
  );
}

function RunHistory() {
  const { t } = useTranslation(),
    { workspace, basePath } = useWorkspace(),
    client = useClient(),
    navigate = useNavigate();
  const { sessionId = "", threadId = "", runId = "" } = useParams();
  const runs = useQuery(
    conversationQueries(client, workspace.id).runs(threadId),
  );
  if (runs.error)
    return (
      <Button
        size="sm"
        variant="ghost"
        onClick={() => void runs.refetch()}
        type="button"
      >
        {t("Retry history")}
      </Button>
    );
  return (
    <ChoiceField
      placeholder={t("Run history")}
      value={runId}
      onValueChange={(id) =>
        navigate(
          runPath(basePath, {
            session_id: sessionId,
            thread_id: threadId,
            run_id: id,
          }),
        )
      }
      label={t("Run history")}
      hideLabel
      options={(runs.data ?? []).map((run, index) => ({
        value: run.id,
        label: `${t("Run")} ${runs.data!.length - index} · ${t(`state.${run.status}`, { defaultValue: run.status })}`,
      }))}
    />
  );
}
