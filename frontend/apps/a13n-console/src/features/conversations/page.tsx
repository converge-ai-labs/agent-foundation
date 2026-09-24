import { SearchPicker } from "a13n-ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
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
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import { AgentAvatar } from "../agents/avatar";
import { createManagedEnvironment } from "../environments/api";
import {
  CONSOLE_SESSION_LABELS,
  conversationQueries,
  invalidateConversation,
  runPath,
} from "./api";
import { Composer, RunOptions, useRunOptions } from "./composer";
import type { EnvironmentChoice } from "./composer/options-dialog";
import { SessionList } from "./list";
import { SessionHeader } from "./session-header";
import { RunCollapseProvider } from "./transcript/debug/collapse";
import { ThreadInbox } from "./transcript/inbox";
import styles from "./conversations.module.css";

export function ConversationsPage() {
  const { sessionId } = useParams();
  const location = useLocation();
  const nested = !!sessionId || /\/sessions\/new\/?$/.test(location.pathname);
  return nested ? (
    <div className={`${styles.sessionStage} a13n-scrollbar`}>
      <Outlet />
    </div>
  ) : (
    <SessionList />
  );
}

/** The mount a Thread's primary sandbox is known by. */
const PRIMARY_MOUNT = "workspace";

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
  // A link can prefill the first message, which the user still sends.
  const message = search.get("message");
  // What the first submission created before its Thread: a retry reuses it,
  // so it replays that submission instead of leaving another one behind.
  const prepared = useRef<{
    session?: Schema["SessionView"];
    environment?: Schema["EnvironmentView"];
  }>({});
  async function consoleSession() {
    prepared.current.session ??= data(
      await client.http.POST("/api/v1/workspaces/{workspace_id}/sessions", {
        params: { path: { workspace_id: workspace.id } },
        body: { labels: CONSOLE_SESSION_LABELS },
      }),
    );
    return prepared.current.session;
  }
  /** A template choice reserves its environment first, then mounts it. */
  async function primaryMount(
    choice: EnvironmentChoice,
  ): Promise<Schema["MountCreate"]> {
    if (!("template_id" in choice)) return { name: PRIMARY_MOUNT, ...choice };
    const reserved = prepared.current.environment;
    const environment =
      reserved?.template_id === choice.template_id
        ? reserved
        : await createManagedEnvironment(client, workspace.id, choice);
    prepared.current.environment = environment;
    return { name: PRIMARY_MOUNT, environment_id: environment.id };
  }
  const agents = useQuery({
    queryKey: ["agent-picker", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace_id}/agents", {
            params: {
              path: { workspace_id: workspace.id },
              query: { cursor, limit: 100, archived: false },
            },
            signal,
          })
          .then(data),
      ),
  });
  const enabled = agents.data ?? [];
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
          initial={
            message ? { content: [{ type: "text", text: message }] } : undefined
          }
          disabled={!can("run") || !agentId}
          agentName={selected?.name}
          options={<RunOptions options={options} showAgent={false} />}
          submit={async (payload) => {
            const { environment, ...choice } = options.build();
            const body = {
              ...choice,
              agent_id: agentId,
              payload,
              session_id: (await consoleSession()).id,
              environments: environment
                ? [await primaryMount(environment)]
                : [],
            };
            const { thread, run } = data(
              await client.http.POST(
                "/api/v1/workspaces/{workspace_id}/threads",
                {
                  params: {
                    path: { workspace_id: workspace.id },
                    header: commandHeaders(idempotency.forBody(body)),
                  },
                  body,
                },
              ),
            );
            void invalidateConversation(cache, workspace.id, {
              sessionId: thread.session_id,
              threadId: thread.id,
              runId: run?.id,
            });
            navigate(
              run
                ? runPath(basePath, {
                    session_id: run.session_id,
                    thread_id: run.thread_id,
                    run_id: run.id,
                  })
                : `${basePath}/sessions/${thread.session_id}/threads/${thread.id}`,
            );
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
    { workspace } = useWorkspace(),
    { search } = useLocation(),
    client = useClient(),
    queries = conversationQueries(client, workspace.id);
  const thread = useQuery(queries.thread(threadId));
  // The latest Run: the active one, else the one sealed last.
  const selected =
    runId ?? thread.data?.current_run_id ?? thread.data?.last_run_id;
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
            <ThreadInbox thread={thread.data} />
          </div>
        )
      )}
    </>
  );
}
