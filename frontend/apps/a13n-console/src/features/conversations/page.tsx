import { SearchPicker } from "a13n-ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import { ChatIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import { AgentAvatar } from "../agents/avatar";
import { conversationQueries, invalidateConversation, runPath } from "./api";
import { Composer, RunOptions, useRunOptions } from "./composer";
import { SessionList } from "./list";
import { useConversationNotifications } from "./notifications";
import { SessionHeader } from "./session-header";
import { RunCollapseProvider } from "./transcript/debug/collapse";
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

export function SessionLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId } = useParams(),
    { workspace } = useWorkspace(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const threads = useQuery(queries.threads(sessionId));
  const first = threads.data?.[0];
  // The disclosure level lives in the search string; redirects keep it.
  const { search } = useLocation();
  return (
    // The header and the run sections share one owner for what is collapsed.
    <RunCollapseProvider>
      <div className={styles.sessionDetail}>
        <SessionHeader threads={threads.data ?? []} />
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
            <Navigate to={`threads/${first.id}${search}`} replace />
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
    </RunCollapseProvider>
  );
}

export function ThreadLayout() {
  const { t } = useTranslation(),
    { sessionId = "", threadId = "", runId } = useParams(),
    { workspace, basePath } = useWorkspace(),
    { search } = useLocation(),
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
        <Navigate to={`runs/${selected}${search}`} replace />
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
