import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "./i18n";
import { SendShortcutContext } from "./conversations/send-shortcut";
import {
  BrowserRouter,
  Navigate,
  NavLink,
  useMatch,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router";
import {
  QueryClient,
  QueryClientProvider,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Button, Input, ModalFrame } from "a13n-ui";
import {
  CaretDown,
  CaretRight,
  ChatCircle,
  CornersIn,
  Folder,
  LinkBreak,
  List,
  Plus,
  UserCircle,
} from "@phosphor-icons/react";
import {
  ApiError,
  createTransport,
  result,
  type Schema,
} from "./transport/client";
import {
  GuestAccessContext,
  GuestThreadIdsContext,
  TransportContext,
  useTransport,
} from "./transport/context";
import { ComposerDrafts } from "./conversations/composer";
import type { ThreadDraft } from "./conversations/draft";
import { ConversationPage } from "./conversations/conversation";
import {
  LiveThreadsProvider,
  useLiveThread,
} from "./conversations/live-threads";
import { CoordinatorIcon } from "./conversations/coordinator-icon";
import { useThread } from "./conversations/queries";
import { isUnavailableChat } from "./conversations/conversation-unavailable";
import { ChildControlsProvider } from "./conversations/child-controls";
import { useLiveWorkbench, type Profile } from "./shell/presence";
import { ParticipantAvatars } from "./shell/participant-avatars";
import { ErrorNotice, TextField } from "./shell/ui";
import { readPreference, writePreference } from "./shell/preferences";
import styles from "./sharing.module.css";
import layout from "./shell/workbench.module.css";

export function guestKey(shareId: string) {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const incoming = fragment.get("key");
  const storage = `a13n-harness-ui.share.${shareId}`;
  if (incoming !== null) {
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
    try {
      sessionStorage.setItem(storage, incoming);
    } catch {
      /* This tab can work without storage. */
    }
    return incoming;
  }
  try {
    return sessionStorage.getItem(storage) ?? "";
  } catch {
    return "";
  }
}

export function GuestApp({ shareId }: { shareId: string }) {
  const [key] = useState(() => guestKey(shareId));
  const [unavailable, setUnavailable] = useState(!key);
  const [queries] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, staleTime: 15000 },
          mutations: { retry: false, networkMode: "always" },
        },
      }),
  );
  const [drafts] = useState(() => new Map<string, ThreadDraft>());
  const unauthorized = useCallback(() => {
    setUnavailable(true);
    void queries.cancelQueries();
    queries.clear();
  }, [queries]);
  const recheckScope = useCallback(
    (realtime: boolean) => {
      void queries.invalidateQueries(
        { queryKey: ["share-access"] },
        { cancelRefetch: false },
      );
      // A missing focus can close the socket even when the grant is valid.
      if (realtime) void queries.invalidateQueries({ queryKey: ["thread"] });
    },
    [queries],
  );
  const transport = useMemo(
    () => createTransport(key, unauthorized, shareId, recheckScope),
    [key, shareId, unauthorized, recheckScope],
  );
  useEffect(() => () => transport.close(), [transport]);
  return (
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        {unavailable ? (
          <AccessUnavailable />
        ) : (
          <ComposerDrafts value={drafts}>
            <ChildControlsProvider>
              <GuestSession shareId={shareId} />
            </ChildControlsProvider>
          </ComposerDrafts>
        )}
      </TransportContext>
    </QueryClientProvider>
  );
}
function AccessUnavailable() {
  const { t } = useTranslation();
  return (
    <main className={styles.unavailable}>
      <div>
        <LinkBreak size={32} />
        <h1>{t("This link is unavailable")}</h1>
        <p>
          {t("This link expired or was revoked. Ask the sender for a new one.")}
        </p>
      </div>
    </main>
  );
}
function GuestSession({ shareId }: { shareId: string }) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const access = useQuery({
    queryKey: ["share-access"],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/shared/{share_id}/access", {
          params: { path: { share_id: shareId } },
          signal,
        }),
      ),
    refetchInterval: 5000,
  });
  const permission = access.data?.grant.permission;
  useEffect(() => {
    void queries.invalidateQueries({ queryKey: ["thread"] });
  }, [permission, queries]);
  const refresh = useCallback(() => {
    void queries.invalidateQueries({ queryKey: ["share-access"] });
  }, [queries]);
  if (!access.data)
    return (
      <main className={styles.unavailable}>
        <div>
          <ChatCircle size={32} />
          <h1>
            {access.isError ? t("Couldn’t connect") : t("Opening shared link…")}
          </h1>
          <ErrorNotice
            error={access.error}
            retry={() => void access.refetch()}
          />
        </div>
      </main>
    );
  return (
    <GuestAccessContext value={access.data}>
      <BrowserRouter basename={`/share/${shareId}`}>
        <LiveThreadsProvider>
          <SendShortcutContext value={access.data.send_shortcut}>
            <GuestShell access={access.data} refreshAccess={refresh} />
          </SendShortcutContext>
        </LiveThreadsProvider>
      </BrowserRouter>
    </GuestAccessContext>
  );
}
function GuestShell({
  access,
  refreshAccess,
}: {
  access: Schema<"ShareAccess">;
  refreshAccess: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [search, setSearch] = useState("");
  const [sidebar, setSidebar] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profile, setProfile] = useState<Profile>(() => ({
    display_name:
      readPreference("guest-name", "") ||
      `Guest ${crypto.randomUUID().slice(0, 4)}`,
    color: "#64748b",
  }));
  const [name, setName] = useState(profile.display_name);
  const selectedId = useMatch("/threads/:threadId")?.params.threadId;
  const selection = useThread(selectedId ?? "");
  const unavailable = isUnavailableChat(selection.error);
  const live = useLiveWorkbench(profile, !unavailable, refreshAccess);
  const selected = unavailable ? undefined : selection.data?.thread;
  const project = access.grant.scope === "project";
  const targetId = access.grant.target_id;
  const coordinatorId =
    !project && access.target_role === "coordinator" ? targetId : "";
  const coordinatorQuery = useThread(coordinatorId);
  const coordinatorLive = useLiveThread(coordinatorId, true);
  const coordinator =
    coordinatorLive.display.snapshot?.thread.thread ??
    coordinatorQuery.data?.thread;
  const selectedWorker =
    coordinatorId && selected?.coordinator_thread_id === coordinatorId;
  const workers = useSharedThreads(access.grant.share_id, {
    coordinatorId,
    enabled: !!coordinatorId,
    count: coordinator?.unarchived_worker_count,
  });
  const workerRows = useMemo(
    () => sharedThreadRows(workers.data?.pages),
    [workers.data],
  );
  // Keep a known selected worker in navigation even if it is archived or off-page.
  const showSidebar =
    project ||
    !!(
      coordinatorId &&
      (selectedWorker ||
        workerRows.length ||
        coordinator?.unarchived_worker_count)
    );
  const [expandedWorkers, setExpandedWorkers] = useState<
    ReadonlyMap<string, ThreadSummary[]>
  >(new Map());
  const registerWorkers = useCallback((id: string, rows: ThreadSummary[]) => {
    setExpandedWorkers((previous) => new Map(previous).set(id, rows));
  }, []);
  const canPrompt = access.grant.permission === "prompt";
  useEffect(() => setSidebar(false), [location.pathname]);
  const list = useSharedThreads(access.grant.share_id, {
    enabled: project,
    search: search.trim(),
  });
  const threads = useMemo(
    () => sharedThreadRows(list.data?.pages),
    [list.data],
  );
  const knownThreads = useMemo(() => {
    const ids = new Set(!project ? [targetId] : []);
    if (selected) {
      ids.add(selected.thread_id);
      if (selected.coordinator_thread_id && project)
        ids.add(selected.coordinator_thread_id);
    }
    for (const { thread_id } of [
      ...threads,
      ...workerRows,
      ...[...expandedWorkers.values()].flat(),
    ]) {
      ids.add(thread_id);
    }
    return ids;
  }, [project, targetId, selected, threads, workerRows, expandedWorkers]);
  const [newThreadId, setNewThreadId] = useState(
    () => `thread_${crypto.randomUUID().replaceAll("-", "")}`,
  );
  const create = useMutation({
    mutationFn: async () => {
      try {
        return await result(
          client.POST("/api/shared/{share_id}/threads", {
            params: { path: { share_id: access.grant.share_id } },
            body: { thread_id: newThreadId },
          }),
        );
      } catch (error) {
        if (
          !(error instanceof ApiError) ||
          error.status >= 500 ||
          error.status === 409
        ) {
          // Resolve uncertain creation through its retained identity, never a
          // replacement create. A later explicit attempt keeps that identity.
          try {
            return (
              await result(
                client.GET("/api/threads/{thread_id}", {
                  params: { path: { thread_id: newThreadId } },
                }),
              )
            ).thread;
          } catch {
            /* Keep the original creation error visible. */
          }
        }
        throw error;
      }
    },
    onSuccess: (thread) => {
      setNewThreadId(`thread_${crypto.randomUUID().replaceAll("-", "")}`);
      queries.setQueryData(["thread", thread.thread_id, "detail"], undefined);
      void queries.invalidateQueries({ queryKey: ["shared-threads"] });
      navigate(`/threads/${thread.thread_id}?compose=1`);
    },
  });
  return (
    <main className={styles.guest}>
      <header className={styles.header}>
        <div className={styles.identity}>
          {showSidebar && (
            <Button
              className={styles.mobileToggle}
              variant="ghost"
              size="icon-sm"
              aria-label={t("Show chats")}
              aria-expanded={sidebar}
              onClick={() => setSidebar(!sidebar)}
            >
              <List />
            </Button>
          )}
          <span className={styles.mark}>
            {project ? (
              <Folder size={18} />
            ) : coordinatorId ? (
              <CoordinatorIcon size={18} />
            ) : (
              <ChatCircle size={18} />
            )}
          </span>
          <div>
            <h1 title={access.title}>{access.title}</h1>
            <p>
              {coordinatorId && (
                <span className={styles.mobileCoordinator}>
                  <CoordinatorIcon size={14} />
                </span>
              )}
              {project
                ? t("Shared project")
                : coordinatorId
                  ? t("Shared coordinator")
                  : t("Shared chat")}{" "}
              -{" "}
              <span>{canPrompt ? t("Can send messages") : t("View only")}</span>
            </p>
          </div>
        </div>
        <div className={styles.access}>
          <span
            className={styles.expiry}
            title={new Date(access.grant.expires_at).toLocaleString(
              i18n.resolvedLanguage,
            )}
          >
            {t("Until")}{" "}
            {new Date(access.grant.expires_at).toLocaleDateString(
              i18n.resolvedLanguage,
              {
                month: "short",
                day: "numeric",
              },
            )}
          </span>
          <ParticipantAvatars
            participants={(live.presence?.participants ?? []).filter(
              (p) => p.participant_id !== live.presence?.participant_id,
            )}
            ownId={live.presence?.participant_id}
            threadTitle={t("this chat")}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("Your display name")}
            title={profile.display_name}
            onClick={() => setProfileOpen(true)}
          >
            <UserCircle size={20} />
          </Button>
        </div>
      </header>
      <div className={styles.body}>
        {project && (
          <aside
            className={`${styles.sidebar} ${sidebar ? styles.sidebarOpen : ""}`}
            aria-label={t("Project chats")}
          >
            <div className={styles.sidebarTop}>
              <h2>{t("Chats")}</h2>
              {canPrompt && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={t("New chat")}
                  loading={create.isPending}
                  onClick={() => create.mutate()}
                >
                  <Plus />
                </Button>
              )}
            </div>
            <Input
              type="search"
              aria-label={t("Search shared chats")}
              placeholder={t("Search chats…")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <ErrorNotice
              error={list.error || create.error}
              retry={() => void list.refetch()}
            />
            {create.isError && (
              <p className={styles.hint}>
                {t(
                  "The chat may already exist. Check the list before trying again.",
                )}
              </p>
            )}
            <nav className={`${styles.threadList} a13n-scrollbar`}>
              {threads.map((thread) =>
                !search.trim() && thread.role === "coordinator" ? (
                  <GuestCoordinatorEntry
                    key={thread.thread_id}
                    thread={thread}
                    shareId={access.grant.share_id}
                    selected={selected}
                    register={registerWorkers}
                  />
                ) : (
                  <GuestThreadRow key={thread.thread_id} thread={thread} />
                ),
              )}
              {list.isPending && (
                <p className={styles.hint}>{t("Loading chats…")}</p>
              )}
              {list.data?.pages[0]?.total === 0 && (
                <p className={styles.hint}>
                  {search ? t("No matching chats.") : t("No chats yet.")}
                </p>
              )}
              {list.hasNextPage && (
                <Button
                  variant="ghost"
                  loading={list.isFetchingNextPage}
                  onClick={() => void list.fetchNextPage()}
                >
                  {t("Show more")}
                </Button>
              )}
            </nav>
          </aside>
        )}
        {!project && showSidebar && (
          <aside
            className={`${styles.sidebar} ${sidebar ? styles.sidebarOpen : ""}`}
            aria-label={t("Shared coordinator")}
          >
            <nav className={`${styles.threadList} a13n-scrollbar`}>
              <GuestThreadRow
                thread={
                  coordinator ?? {
                    thread_id: targetId,
                    title: access.title,
                    role: "coordinator",
                  }
                }
              />
              <GuestWorkerRows
                ownerId={targetId}
                workers={workers}
                rows={workerRows}
                selected={selected}
              />
            </nav>
          </aside>
        )}
        <section className={styles.conversation}>
          <Routes>
            <Route
              path="/threads/:threadId"
              element={
                <GuestThreadIdsContext value={knownThreads}>
                  <ConversationPage
                    profile={profile}
                    unauthorized={refreshAccess}
                  />
                </GuestThreadIdsContext>
              }
            />
            <Route
              path="*"
              element={
                project ? (
                  <div className={styles.welcome}>
                    <Folder size={32} style={{ margin: "auto" }} />
                    <h2>{t("Shared project")}</h2>
                    <p>
                      {canPrompt
                        ? t(
                            "Choose a chat to catch up, or start a new one with the project’s agent.",
                          )
                        : t(
                            "Choose a chat to catch up and follow along as the work unfolds.",
                          )}
                    </p>
                    {canPrompt && (
                      <Button
                        loading={create.isPending}
                        onClick={() => create.mutate()}
                      >
                        <Plus size={16} />
                        {t("New chat")}
                      </Button>
                    )}
                  </div>
                ) : (
                  <Navigate replace to={`/threads/${access.grant.target_id}`} />
                )
              }
            />
          </Routes>
        </section>
      </div>
      <ModalFrame
        open={profileOpen}
        onOpenChange={setProfileOpen}
        title={t("Your display name")}
        description={t("Shown to people in this chat. Names are not verified.")}
        closeLabel={t("Close")}
      >
        <form
          className={layout.stack}
          onSubmit={(e) => {
            e.preventDefault();
            const value = name.trim().slice(0, 80) || "Guest";
            setProfile({ ...profile, display_name: value });
            writePreference("guest-name", value);
            setProfileOpen(false);
          }}
        >
          <TextField label={t("Name")} value={name} onChange={setName} />
          <div className={layout.dialogActions} data-a13n-form-actions>
            <Button
              type="button"
              variant="outline"
              onClick={() => setProfileOpen(false)}
            >
              {t("Cancel")}
            </Button>
            <Button type="submit">{t("Save name")}</Button>
          </div>
        </form>
      </ModalFrame>
    </main>
  );
}

type ThreadSummary = Schema<"ThreadSummary">;
const WORKER_REFRESH_MS = 8000;

function useSharedThreads(
  shareId: string,
  {
    enabled,
    search = "",
    coordinatorId,
    count,
  }: {
    enabled: boolean;
    search?: string;
    coordinatorId?: string;
    count?: number | null;
  },
) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const previousCount = useRef(count);
  useEffect(() => {
    const previous = previousCount.current;
    previousCount.current = count;
    if (
      coordinatorId &&
      previous != null &&
      count != null &&
      previous !== count
    )
      void queries.invalidateQueries(
        { queryKey: ["shared-threads", coordinatorId] },
        { cancelRefetch: false },
      );
  }, [coordinatorId, count, queries]);
  return useInfiniteQuery({
    queryKey: ["shared-threads", coordinatorId || null, search],
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/shared/{share_id}/threads", {
          params: {
            path: { share_id: shareId },
            query: {
              coordinator_thread_id: coordinatorId || undefined,
              independent_only: !coordinatorId && !search ? true : undefined,
              query: search || undefined,
              cursor: pageParam,
              limit: coordinatorId ? 10 : 30,
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: coordinatorId ? WORKER_REFRESH_MS : 15000,
  });
}

function sharedThreadRows(pages: Schema<"ThreadPage">[] = []) {
  return [
    ...new Map(
      pages
        .flatMap((page) => page.threads)
        .map((thread) => [thread.thread_id, thread]),
    ).values(),
  ];
}

type GuestRow = Pick<ThreadSummary, "thread_id" | "title" | "role"> &
  Partial<ThreadSummary>;

function GuestThreadRow({
  thread,
  nested,
  disclosure,
}: {
  thread: GuestRow;
  nested?: boolean;
  disclosure?: { expanded: boolean; toggle: () => void };
}) {
  const { t, i18n } = useTranslation();
  const date = thread.touched_at ?? thread.created_at;
  const working =
    thread.root_activity && thread.root_activity.state !== "inactive";
  const title = thread.title || t("Untitled chat");
  const worker = thread.role === "worker" && !nested;
  return (
    <div className={styles.threadEntry}>
      <NavLink
        to={`/threads/${encodeURIComponent(thread.thread_id)}`}
        className={({ isActive }) =>
          isActive ? styles.activeThread : styles.thread
        }
        title={title}
      >
        {!nested && (
          <span className={styles.threadIcon}>
            {thread.role === "coordinator" && !disclosure ? (
              <CoordinatorIcon size={16} />
            ) : worker ? (
              <CornersIn size={14} aria-label={t("Coordinator worker")} />
            ) : null}
          </span>
        )}
        <span className={styles.threadTitle}>{title}</span>
        {(working || date) && (
          <small>
            {working
              ? t("Working…")
              : new Date(date!).toLocaleDateString(i18n.resolvedLanguage, {
                  month: "short",
                  day: "numeric",
                })}
          </small>
        )}
      </NavLink>
      {disclosure && (
        <button
          type="button"
          className={styles.workerDisclosure}
          aria-expanded={disclosure.expanded}
          aria-label={t("Workers for {{title}}", { title })}
          onClick={disclosure.toggle}
        >
          {disclosure.expanded ? (
            <CaretDown size={12} />
          ) : (
            <CaretRight size={12} />
          )}
        </button>
      )}
    </div>
  );
}

function GuestCoordinatorEntry({
  thread,
  shareId,
  selected,
  register,
}: {
  thread: ThreadSummary;
  shareId: string;
  selected?: ThreadSummary;
  register: (id: string, rows: ThreadSummary[]) => void;
}) {
  const ownerId = thread.thread_id;
  const selectedWorkerId =
    selected?.coordinator_thread_id === ownerId
      ? selected.thread_id
      : undefined;
  const hasWorkers =
    (thread.unarchived_worker_count ?? 0) > 0 || !!selectedWorkerId;
  const autoExpandId = hasWorkers
    ? selected?.thread_id === ownerId
      ? ownerId
      : selectedWorkerId
    : undefined;
  const [expanded, setExpanded] = useState(!!autoExpandId);
  const lastAutoExpanded = useRef(autoExpandId);
  useEffect(() => {
    if (autoExpandId && autoExpandId !== lastAutoExpanded.current)
      setExpanded(true);
    lastAutoExpanded.current = autoExpandId;
  }, [autoExpandId]);
  if (!hasWorkers && expanded) setExpanded(false);
  const open = expanded && hasWorkers;
  const workers = useSharedThreads(shareId, {
    coordinatorId: ownerId,
    enabled: open,
    count: thread.unarchived_worker_count,
  });
  const rows = useMemo(
    () => sharedThreadRows(workers.data?.pages),
    [workers.data],
  );
  useEffect(() => {
    if (workers.data) register(ownerId, rows);
  }, [register, ownerId, rows, workers.data]);
  return (
    <>
      <GuestThreadRow
        thread={thread}
        disclosure={
          hasWorkers
            ? { expanded: open, toggle: () => setExpanded(!open) }
            : undefined
        }
      />
      {open && (
        <GuestWorkerRows
          ownerId={ownerId}
          workers={workers}
          rows={rows}
          selected={selected}
        />
      )}
    </>
  );
}

function GuestWorkerRows({
  ownerId,
  workers,
  rows,
  selected,
}: {
  ownerId: string;
  workers: ReturnType<typeof useSharedThreads>;
  rows: ThreadSummary[];
  selected?: ThreadSummary;
}) {
  const { t } = useTranslation();
  const visible = [...rows].sort((a, b) => {
    const active =
      Number(b.root_activity.state !== "inactive") -
      Number(a.root_activity.state !== "inactive");
    return (
      active ||
      (b.touched_at ?? b.created_at).localeCompare(a.touched_at ?? a.created_at)
    );
  });
  const pinned =
    selected?.coordinator_thread_id === ownerId &&
    !rows.some((row) => row.thread_id === selected.thread_id)
      ? selected
      : undefined;
  return (
    <div className={styles.workers}>
      {pinned && <GuestThreadRow thread={pinned} nested />}
      {visible.map((thread) => (
        <GuestThreadRow key={thread.thread_id} thread={thread} nested />
      ))}
      {workers.isPending && (
        <small role="status">{t("Loading workers…")}</small>
      )}
      <ErrorNotice error={workers.error} retry={() => void workers.refetch()} />
      {workers.hasNextPage && (
        <Button
          variant="ghost"
          size="xs"
          loading={workers.isFetchingNextPage}
          onClick={() => void workers.fetchNextPage()}
        >
          {t("More workers")}
        </Button>
      )}
    </div>
  );
}
