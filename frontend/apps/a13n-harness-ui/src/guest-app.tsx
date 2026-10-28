import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  NavLink,
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
  ChatCircle,
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
  TransportContext,
  useTransport,
} from "./transport/context";
import { ComposerDrafts } from "./conversations/composer";
import type { ThreadDraft } from "./conversations/draft";
import { ConversationPage } from "./conversations/conversation";
import { LiveThreadsProvider } from "./conversations/live-threads";
import { ChildControlsProvider } from "./conversations/child-controls";
import { useLiveWorkbench, type Profile } from "./shell/presence";
import { ParticipantAvatars } from "./shell/participant-avatars";
import { ErrorNotice, TextField } from "./shell/ui";
import { readPreference, writePreference } from "./shell/preferences";
import styles from "./sharing.module.css";

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
  const transport = useMemo(
    () => createTransport(key, unauthorized, shareId),
    [key, shareId, unauthorized],
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
  return (
    <main className={styles.unavailable}>
      <div>
        <LinkBreak size={32} />
        <h1>This link is unavailable</h1>
        <p>This link expired or was revoked. Ask the sender for a new one.</p>
      </div>
    </main>
  );
}
function GuestSession({ shareId }: { shareId: string }) {
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
            {access.isError ? "Couldn’t connect" : "Opening shared link…"}
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
          <GuestShell access={access.data} refreshAccess={refresh} />
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
  const live = useLiveWorkbench(profile, true, refreshAccess);
  const project = access.grant.scope === "project";
  const canPrompt = access.grant.permission === "prompt";
  useEffect(() => setSidebar(false), [location.pathname]);
  const list = useInfiniteQuery({
    queryKey: ["shared-threads", search],
    enabled: project,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/shared/{share_id}/threads", {
          params: {
            path: { share_id: access.grant.share_id },
            query: { query: search || undefined, cursor: pageParam, limit: 30 },
          },
          signal,
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: 15000,
  });
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
          {project && (
            <Button
              className={styles.mobileToggle}
              variant="ghost"
              size="icon-sm"
              aria-label="Show chats"
              aria-expanded={sidebar}
              onClick={() => setSidebar(!sidebar)}
            >
              <List />
            </Button>
          )}
          <span className={styles.mark}>
            {project ? <Folder size={18} /> : <ChatCircle size={18} />}
          </span>
          <div>
            <h1 title={access.title}>{access.title}</h1>
            <p>
              Shared {project ? "project" : "chat"} ·{" "}
              <span>{canPrompt ? "Can send messages" : "View only"}</span>
            </p>
          </div>
        </div>
        <div className={styles.access}>
          <span
            className={styles.expiry}
            title={new Date(access.grant.expires_at).toLocaleString()}
          >
            Until{" "}
            {new Date(access.grant.expires_at).toLocaleDateString(undefined, {
              month: "short",
              day: "numeric",
            })}
          </span>
          <ParticipantAvatars
            participants={(live.presence?.participants ?? []).filter(
              (p) => p.participant_id !== live.presence?.participant_id,
            )}
            ownId={live.presence?.participant_id}
            threadTitle="this chat"
          />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Your display name"
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
            aria-label="Project chats"
          >
            <div className={styles.sidebarTop}>
              <h2>Chats</h2>
              {canPrompt && (
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="New chat"
                  loading={create.isPending}
                  onClick={() => create.mutate()}
                >
                  <Plus />
                </Button>
              )}
            </div>
            <Input
              type="search"
              aria-label="Search shared chats"
              placeholder="Search chats…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <ErrorNotice
              error={list.error || create.error}
              retry={() => void list.refetch()}
            />
            {create.isError && (
              <p className={styles.hint}>
                The chat may already exist. Check the list before trying again.
              </p>
            )}
            <nav className={`${styles.threadList} a13n-scrollbar`}>
              {list.data?.pages
                .flatMap((page) => page.threads)
                .map((thread) => (
                  <NavLink
                    key={thread.thread_id}
                    to={`/threads/${thread.thread_id}`}
                    className={({ isActive }) =>
                      isActive ? styles.activeThread : styles.thread
                    }
                  >
                    <span>{thread.title || "Untitled chat"}</span>
                    <small>
                      {thread.root_activity.state === "inactive"
                        ? new Date(
                            thread.touched_at ?? thread.created_at,
                          ).toLocaleDateString(undefined, {
                            month: "short",
                            day: "numeric",
                          })
                        : "Working…"}
                    </small>
                  </NavLink>
                ))}
              {list.isPending && <p className={styles.hint}>Loading chats…</p>}
              {list.data?.pages[0]?.total === 0 && (
                <p className={styles.hint}>
                  {search ? "No matching chats." : "No chats yet."}
                </p>
              )}
              {list.hasNextPage && (
                <Button
                  variant="ghost"
                  loading={list.isFetchingNextPage}
                  onClick={() => void list.fetchNextPage()}
                >
                  Show more
                </Button>
              )}
            </nav>
          </aside>
        )}
        <section className={styles.conversation}>
          <Routes>
            <Route
              path="/threads/:threadId"
              element={
                <ConversationPage
                  profile={profile}
                  unauthorized={refreshAccess}
                />
              }
            />
            <Route
              path="*"
              element={
                project ? (
                  <div className={styles.welcome}>
                    <Folder size={32} style={{ margin: "auto" }} />
                    <h2>Shared project</h2>
                    <p>
                      Choose a chat to catch up
                      {canPrompt
                        ? ", or start a new one with the project’s agent."
                        : " and follow along as the work unfolds."}
                    </p>
                    {canPrompt && (
                      <Button
                        loading={create.isPending}
                        onClick={() => create.mutate()}
                      >
                        <Plus size={16} />
                        New chat
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
        title="Your display name"
        description="Shown to people in this chat. Names are not verified."
        closeLabel="Close"
      >
        <form
          className={styles.panel}
          onSubmit={(e) => {
            e.preventDefault();
            const value = name.trim().slice(0, 80) || "Guest";
            setProfile({ ...profile, display_name: value });
            writePreference("guest-name", value);
            setProfileOpen(false);
          }}
        >
          <TextField label="Name" value={name} onChange={setName} />
          <Button type="submit">Save name</Button>
        </form>
      </ModalFrame>
    </main>
  );
}
