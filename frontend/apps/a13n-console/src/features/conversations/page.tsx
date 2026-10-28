import { Button, SearchPicker } from "a13n-ui";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Suspense, useEffect, useRef, useState } from "react";
import {
  Link,
  Navigate,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import { ChatsIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, commandHeaders, data, type Schema } from "../../shared/api";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import { AgentAvatar } from "../agents/avatar";
import { prepareConversationAgent } from "../agents/prepare";
import { createManagedEnvironment } from "../environments/api";
import {
  CONSOLE_SESSION_LABELS,
  conversationQueries,
  invalidateConversation,
  runPath,
} from "./api";
import { Composer, RunOptions, useRunOptions } from "./composer";
import type { EnvironmentChoice } from "./composer/options-dialog";
import { SessionBodyLoading, SessionError } from "./session-feedback";
import { SessionList } from "./list";
import { SessionAuthorsProvider } from "./message-authors";
import { SessionHeader } from "./session-header";
import { SessionStreamProvider } from "./session-stream";
import { useSession } from "./queries";
import { validSpaceSelection } from "../memories/session-settings";
import { RunCollapseProvider } from "./transcript/debug/collapse";
import { ThreadInbox } from "./transcript/inbox";
import { SessionMemoryControl } from "./composer/session-memory";
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
    agentId?: string;
    session?: Schema["SessionView"];
    environment?: Schema["EnvironmentView"];
  }>({});
  const [memoryLocked, setMemoryLocked] = useState(false);
  async function consoleSession() {
    prepared.current.session ??= data(
      await client.workspace(workspace.id).POST("/api/v1/sessions", {
        body: {
          labels: CONSOLE_SESSION_LABELS,
          origin: { application: "a13n-console", name: "Console" },
          ...(memory === null ? {} : { memory }),
        },
      }),
    );
    setMemoryLocked(true);
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
        client
          .workspace(workspace.id)
          .GET("/api/v1/agents", {
            params: {
              query: { cursor, limit: 100, archived: false },
            },
            signal,
          })
          .then(data),
      ),
  });
  const [memory, setMemory] = useState<Schema["SessionMemorySettings"] | null>(
    null,
  );
  const enabled = agents.data ?? [];
  const selected = enabled.find((agent) => agent.id === agentId);
  return (
    <div className={styles.startPage}>
      <div className={styles.startColumn}>
        <header className={styles.startHeader}>
          <IconTile size={44}>
            <ChatsIcon size={20} aria-hidden="true" />
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
          disabled={!can("run") || !selected}
          submitDisabled={memory !== null && !validSpaceSelection(memory)}
          agentName={selected?.name}
          options={
            <>
              <RunOptions
                options={options}
                showAgent={false}
                agentId={agentId}
              />
              {can("run") && (
                <SessionMemoryControl
                  value={memory}
                  onChange={setMemory}
                  locked={memoryLocked}
                />
              )}
            </>
          }
          submit={async (payload) => {
            const { environment, ...choice } = options.build();
            if (!selected) return;
            if (
              !choice.agent_revision_id &&
              prepared.current.agentId !== agentId
            ) {
              if (
                await prepareConversationAgent(
                  client,
                  workspace.id,
                  selected,
                  can("write"),
                )
              ) {
                void cache.invalidateQueries({
                  queryKey: ["agents", workspace.id],
                });
                void cache.invalidateQueries({
                  queryKey: ["agent-picker", workspace.id],
                });
              }
              prepared.current.agentId = agentId;
            }
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
              await client.workspace(workspace.id).POST("/api/v1/threads", {
                params: {
                  header: commandHeaders(idempotency.forBody(body)),
                },
                body,
              }),
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
                  }) + "?follow=latest"
                : `${basePath}/sessions/${thread.session_id}/threads/${thread.id}`,
            );
          }}
        />
      </div>
    </div>
  );
}

export function SessionLayout() {
  const { t } = useTranslation();
  const { sessionId = "", threadId, runId } = useParams();
  const { workspace } = useWorkspace();
  const { search } = useLocation();
  const client = useClient(),
    cache = useQueryClient();
  const queries = conversationQueries(client, workspace.id);
  const session = useSession(sessionId);
  const chat = new URLSearchParams(search).get("view") === "chat";
  const roots = useQuery({
    ...queries.threads(
      sessionId,
      chat ? { origin: ["new"], limit: 2 } : { limit: 1 },
    ),
    enabled: !!session.data && !threadId,
  });
  const fallback = useQuery({
    ...queries.threads(sessionId, { limit: 1 }),
    enabled:
      !!session.data && !threadId && chat && roots.data?.items.length === 0,
  });
  const first =
    chat && roots.data?.items.length !== 1 ? undefined : roots.data?.items[0];
  const targetThread = threadId ?? first?.id;
  const selectedThread = useQuery({
    ...queries.thread(targetThread ?? ""),
    enabled: !!targetThread,
    initialData: first?.id === targetThread ? first : undefined,
    initialDataUpdatedAt: roots.dataUpdatedAt,
  });
  const target =
    selectedThread.data ?? (first?.id === targetThread ? first : undefined);
  const selectedRun = runId ?? target?.current_run_id ?? target?.last_run_id;
  useEffect(() => {
    // Start loading the transcript module while the target is being resolved.
    void import("./transcript").catch(() => undefined);
  }, [sessionId]);
  useEffect(() => {
    if (selectedRun) {
      void cache.prefetchQuery(queries.run(selectedRun));
      void cache.prefetchQuery(queries.items(selectedRun));
      if (targetThread)
        void cache.prefetchQuery(
          queries.runs(targetThread, { anchor: selectedRun }),
        );
    }
  }, [client, cache, workspace.id, targetThread, selectedRun]);
  return (
    <SessionAuthorsProvider
      key={`${workspace.id}:${sessionId}`}
      sessionId={sessionId}
    >
      <SessionStreamProvider
        sessionId={sessionId}
        enabled={!!session.data}
        onEvent={(event) => {
          if (
            session.data?.schedule_id &&
            event.type === "connection" &&
            event.state === "live"
          )
            void invalidateConversation(cache, workspace.id, {
              threadId,
              runId: selectedRun,
            });
        }}
      >
        <RunCollapseProvider>
          <div className={styles.sessionDetail}>
            <SessionHeader />
            <div
              className={`${styles.sessionContent} a13n-scrollbar`}
              data-session-stage
            >
              <SessionError
                error={
                  session.error ??
                  (!threadId ? (roots.error ?? fallback.error) : null)
                }
                retry={() => {
                  void session.refetch();
                  if (!threadId) {
                    void roots.refetch();
                    if (chat) void fallback.refetch();
                  }
                }}
              />
              <Suspense fallback={<SessionBodyLoading />}>
                {session.isPending ? (
                  <SessionBodyLoading />
                ) : !session.data ? null : threadId ? (
                  <Outlet />
                ) : first ? (
                  <Navigate to={`threads/${first.id}${search}`} replace />
                ) : roots.isPending ||
                  (chat &&
                    roots.data?.items.length === 0 &&
                    fallback.isPending) ? (
                  <SessionBodyLoading />
                ) : roots.data?.items.length || fallback.data?.items.length ? (
                  <ThreadChoices sessionId={sessionId} />
                ) : !roots.error && !fallback.error ? (
                  <Empty
                    icon={<ChatsIcon aria-hidden="true" />}
                    title={t("No threads yet")}
                    description={t(
                      "Threads created by the host application appear here.",
                    )}
                  />
                ) : null}
              </Suspense>
            </div>
          </div>
        </RunCollapseProvider>
      </SessionStreamProvider>
    </SessionAuthorsProvider>
  );
}

function ThreadChoices({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation(),
    { search } = useLocation(),
    { workspace } = useWorkspace(),
    client = useClient();
  const threads = useInfiniteQuery(
    conversationQueries(client, workspace.id).threadPages(sessionId),
  );
  return (
    <div className="grid gap-3 p-6">
      <h2>{t("Choose a thread")}</h2>
      <p>{t("Choose which thread history to open.")}</p>
      <ErrorNotice error={threads.error} retry={() => void threads.refetch()} />
      {threads.isPending && <SessionBodyLoading />}
      <ul className="grid gap-2">
        {threads.data?.pages
          .flatMap((page) => page.items)
          .map((thread) => (
            <li key={thread.id}>
              <Button
                variant="outline"
                render={<Link to={`threads/${thread.id}${search}`} />}
              >
                {thread.id} · {t(`origin.${thread.origin}`)}
              </Button>
            </li>
          ))}
      </ul>
      {threads.hasNextPage && (
        <Button
          variant="outline"
          disabled={threads.isFetchingNextPage}
          onClick={() => void threads.fetchNextPage()}
        >
          {t("Load more")}
        </Button>
      )}
    </div>
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
      <SessionError
        error={new Error(t("This thread does not belong to this session."))}
      />
    );
  return (
    <>
      <SessionError
        error={thread.error}
        retry={() => {
          void thread.refetch();
        }}
      />
      {thread.isPending ? (
        <SessionBodyLoading />
      ) : runId ? (
        <Outlet />
      ) : selected ? (
        <Navigate
          to={`runs/${selected}?${new URLSearchParams({ ...Object.fromEntries(new URLSearchParams(search)), follow: "latest" })}`}
          replace
        />
      ) : (
        thread.data && (
          <div className={styles.emptyThread}>
            <Empty
              icon={<ChatsIcon aria-hidden="true" />}
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
