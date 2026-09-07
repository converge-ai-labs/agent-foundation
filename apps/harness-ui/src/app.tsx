import { useEffect, useRef, useState } from "react";
import {
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
  useRouterState,
  useBlocker,
} from "@tanstack/react-router";
import {
  QueryClient,
  QueryClientProvider,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  api,
  result,
  message,
  setKey,
  stream,
  ApiError,
  type Model,
} from "./client";
import { Setup } from "./setup";
import {
  emptyDraft,
  updateDrafts,
  type Draft,
  type Drafts,
  type DraftAction,
} from "./drafts";

type FocusFrame =
  Model<"FocusSnapshotFrame"> | Model<"FocusEventFrame"> | Model<"ResetFrame">;
type SummaryFrame =
  Model<"SummaryOpenFrame"> | Model<"SummaryEventFrame"> | Model<"ResetFrame">;
const queries = new QueryClient({
  defaultOptions: {
    queries: { retry: false, staleTime: 5000 },
    mutations: { retry: false },
  },
});
const rootRoute = createRootRoute({ component: App });
const router = createRouter({
  routeTree: rootRoute.addChildren([
    createRoute({ getParentRoute: () => rootRoute, path: "/" }),
    createRoute({ getParentRoute: () => rootRoute, path: "/threads/new" }),
    createRoute({
      getParentRoute: () => rootRoute,
      path: "/threads/$threadId",
    }),
    createRoute({ getParentRoute: () => rootRoute, path: "/setup" }),
    createRoute({ getParentRoute: () => rootRoute, path: "/settings" }),
  ]),
});
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
export function BrowserApp() {
  return (
    <QueryClientProvider client={queries}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
function openThread(id: string) {
  void router.navigate({ to: "/threads/$threadId", params: { threadId: id } });
}
function newThread() {
  void router.navigate({ to: "/threads/new" });
}

function App() {
  const cache = useQueryClient();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const [access, setAccess] = useState<Model<"ListenerStatus">>();
  const [accessError, setAccessError] = useState("");
  const [accessBusy, setAccessBusy] = useState(false);
  const [inputKey, setInputKey] = useState("");
  const [synchronized, setSynchronized] = useState(false);
  const [connection, setConnection] = useState("Connecting");
  const applying = useRef(false);
  const [publicationBusy, setPublicationBusy] = useState(false);
  function setApplying(value: boolean) {
    applying.current = value;
    setPublicationBusy(value);
  }
  useBlocker({
    shouldBlockFn: () => applying.current,
    enableBeforeUnload: publicationBusy,
  });
  const [picker, setPicker] = useState(false);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("");
  const [archived, setArchived] = useState(false);
  const [setupDismissed, setSetupDismissed] = useState(false);
  const [drafts, setDrafts] = useState<Drafts>({});
  const session = useRef(0);
  const generation = session.current;
  function dispatch(action: DraftAction) {
    if (generation === session.current)
      setDrafts((state) => updateDrafts(state, action));
  }
  const initialized = useRef(false);
  async function verify() {
    setAccessBusy(true);
    setAccessError("");
    try {
      const status = result(await api.GET("/api/status"));
      if (status.api_version !== "1")
        throw new ApiError(
          "The browser and server versions differ. Reload the matching package.",
          "protocol_mismatch",
        );
      setAccess(status);
    } catch (error) {
      setAccessError(message(error));
    } finally {
      setAccessBusy(false);
    }
  }
  useEffect(() => {
    void verify();
    const rejected = () => {
      setAccess(undefined);
      setSynchronized(false);
      cache.clear();
      session.current++;
      setDrafts({});
      initialized.current = false;
      setSetupDismissed(false);
      setPicker(false);
    };
    window.addEventListener("a13n-access-required", rejected);
    return () => window.removeEventListener("a13n-access-required", rejected);
  }, []);
  useEffect(() => {
    if (!access) return;
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    let after: string | undefined;
    const connect = () => {
      void stream<SummaryFrame>(
        `/api/events${after ? `?after=${encodeURIComponent(after)}` : ""}`,
        "#/paths/~1api~1events/get/responses/200/content/application~1json/schema",
        controller.signal,
        (frame) => {
          if (frame.kind === "reset") {
            after = undefined;
            throw new Error("Summary reset");
          }
          after = frame.resume_cursor;
          setConnection("Connected");
          attempts = 0;
          if (frame.kind === "open") {
            void cache.cancelQueries();
            void cache.invalidateQueries();
            setSynchronized(true);
          } else {
            void cache.invalidateQueries({
              predicate: (entry) => entry.queryKey[0] !== "thread-picker",
            });
          }
        },
      ).catch((error) => {
        if (controller.signal.aborted) return;
        setConnection("Reconnecting — retained data may be stale");
        if (error instanceof ApiError) {
          setAccessError(message(error));
          return;
        }
        retry = setTimeout(connect, Math.min(1000 * 2 ** attempts++, 15000));
      });
    };
    connect();
    return () => {
      controller.abort();
      clearTimeout(retry);
    };
  }, [access, cache]);
  const setup = useQuery({
    queryKey: ["setup"],
    queryFn: async ({ signal }) =>
      result(await api.GET("/api/setup", { signal })),
    enabled: !!access && synchronized,
  });
  async function reloadSetup(signal?: AbortSignal) {
    const value = result(
      await api.GET("/api/setup", {
        params: { query: { rediscover: true } },
        signal,
      }),
    );
    cache.setQueryData(["setup"], value);
    await cache.invalidateQueries({ queryKey: ["selectors"] });
    return value;
  }
  useEffect(() => {
    if (setup.data && !initialized.current) {
      setScope(setup.data.default_project ?? "");
      initialized.current = true;
    }
  }, [setup.data]);
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), 200);
    return () => clearTimeout(timer);
  }, [search]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "o") {
        event.preventDefault();
        setPicker((value) => !value);
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);
  const pages = useInfiniteQuery({
    queryKey: ["thread-picker", query, scope, archived],
    queryFn: async ({ pageParam, signal }) =>
      result(
        await api.GET("/api/threads", {
          params: {
            query: {
              query: query || undefined,
              project_id: scope || undefined,
              include_archived: archived,
              cursor: pageParam,
            },
          },
          signal,
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: picker && synchronized,
  });
  useEffect(() => {
    if (picker) void cache.resetQueries({ queryKey: ["thread-picker"] });
  }, [picker]);
  const selected =
    pathname.startsWith("/threads/") && pathname !== "/threads/new"
      ? decodeURIComponent(pathname.slice(9))
      : null;
  const showingSetup =
    pathname === "/setup" ||
    pathname === "/settings" ||
    (!!setup.data?.needed && !setupDismissed);
  if (!access)
    return (
      <div className="access">
        <div className="eyebrow">AGENT FOUNDATION</div>
        <h1>Connect to your workspace</h1>
        <p>
          This browser connects to the foreground Agent UI server. Enter the
          process-local key shown in its terminal.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (inputKey) setKey(inputKey);
            setInputKey("");
            void verify();
          }}
        >
          <label>
            API key
            <input
              type="password"
              value={inputKey}
              autoComplete="off"
              onChange={(e) => setInputKey(e.target.value)}
              autoFocus
            />
          </label>
          <button className="primary" disabled={accessBusy}>
            {accessBusy ? "Connecting…" : "Connect / Retry"}
          </button>
        </form>
        {accessError && (
          <p role="alert" className="error">
            {accessError}
          </p>
        )}
        <p className="muted">
          Keys stay in this tab. Closing it does not stop server-owned work.
        </p>
      </div>
    );
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Agent Foundation</span>
        <nav aria-label="Workspace">
          <button onClick={() => setPicker(true)}>
            Conversations <kbd>Ctrl O</kbd>
          </button>
          <button onClick={newThread}>New conversation</button>
          <button onClick={() => void router.navigate({ to: "/setup" })}>
            Setup
          </button>
        </nav>
        <span className="connection" role="status">
          {connection}
        </span>
        <button
          disabled={publicationBusy}
          onClick={() => {
            setKey("");
            setAccess(undefined);
            setSynchronized(false);
            cache.clear();
            session.current++;
            setDrafts({});
            initialized.current = false;
            setSetupDismissed(false);
            setPicker(false);
          }}
        >
          Disconnect
        </button>
      </header>
      {access.access === "dangerous_bypass" && (
        <div className="warning">
          API authentication is disabled for this listener. Every reachable
          client has full App access.
        </div>
      )}
      {accessError && (
        <p role="alert" className="error">
          {accessError}
        </p>
      )}
      {setup.error && (
        <p role="alert" className="error">
          {message(setup.error)}{" "}
          <button onClick={() => void setup.refetch()}>
            Retry setup discovery
          </button>
        </p>
      )}
      {!synchronized || setup.isPending ? (
        <p role="status" className="empty">
          Loading workspace…
        </p>
      ) : showingSetup && setup.data ? (
        <Setup
          status={setup.data}
          reload={reloadSetup}
          setApplying={setApplying}
          close={() => {
            setSetupDismissed(true);
            newThread();
          }}
        />
      ) : setup.data ? (
        <Conversation
          key={selected ?? "new"}
          id={selected}
          setup={setup.data}
          draftState={drafts[selected ?? "new"] ?? emptyDraft}
          dispatch={dispatch}
        />
      ) : null}
      <Dialog.Root open={picker} onOpenChange={setPicker}>
        <Dialog.Portal>
          <Dialog.Overlay className="overlay" />
          <Dialog.Content className="picker">
            <Dialog.Title>Switch conversation</Dialog.Title>
            <Dialog.Description>
              Navigation only. Switching does not stop other conversations or
              submit their drafts.
            </Dialog.Description>
            <label>
              Search
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Title or conversation ID"
              />
            </label>
            <div className="picker-filters">
              <label>
                Project
                <select
                  value={scope}
                  onChange={(e) => setScope(e.target.value)}
                >
                  <option value="">All projects</option>
                  {Object.entries(setup.data?.projects ?? {}).map(
                    ([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={archived}
                  onChange={(e) => setArchived(e.target.checked)}
                />
                Include archived
              </label>
            </div>
            {pages.error && (
              <p role="alert" className="error">
                {message(pages.error)}{" "}
                <button onClick={() => void pages.refetch()}>Retry</button>
              </p>
            )}
            <div className="thread-list">
              {pages.data?.pages
                .flatMap((page) => page.threads)
                .map((thread) => (
                  <button
                    className="thread-row"
                    key={thread.thread_id}
                    onClick={() => {
                      openThread(thread.thread_id);
                      setPicker(false);
                    }}
                  >
                    <span>
                      {thread.title ?? "Untitled conversation"}
                      <small>
                        {thread.configuration.project_id} · {thread.thread_id}
                      </small>
                    </span>
                    <span className="badge">
                      {thread.archived
                        ? "Archived"
                        : thread.root_activity.state}
                    </span>
                  </button>
                ))}
              {pages.isFetching && <p role="status">Loading…</p>}
              {pages.data?.pages[0]?.total === 0 && (
                <p className="empty">No matching conversations.</p>
              )}
            </div>
            <div className="actions">
              {pages.hasNextPage && (
                <button
                  disabled={pages.isFetching}
                  onClick={() => void pages.fetchNextPage()}
                >
                  Load more
                </button>
              )}
              <button
                onClick={() => {
                  newThread();
                  setPicker(false);
                }}
              >
                New conversation
              </button>
              <Dialog.Close>Close</Dialog.Close>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      components={{
        img: () => <span>[Image reference omitted]</span>,
        a: (props) => (
          <a href={props.href} target="_blank" rel="noreferrer">
            {props.children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

export function Conversation({
  id,
  setup,
  draftState,
  dispatch,
}: {
  id: string | null;
  setup: Model<"SetupStatus">;
  draftState: Draft;
  dispatch: (action: DraftAction) => void;
}) {
  const draft = draftState.text;
  const source = id ?? "new";
  const lastReceipt = draftState.receipt;
  const uncertain = draftState.uncertain;
  const setDraft = (text: string) =>
    dispatch({ kind: "edit", id: source, text });
  const cache = useQueryClient();
  const [snapshot, setSnapshot] = useState<Model<"ThreadFocusSnapshot">>();
  const [live, setLive] = useState<Model<"LiveEvent">[]>([]);
  const [streamStatus, setStreamStatus] = useState("Connecting live activity");
  const [localError, setError] = useState("");
  const error = draftState.error || localError;
  const [busy, setBusy] = useState(false);
  const [project, setProject] = useState(
    draftState.selection?.project_id ?? setup.default_project ?? "",
  );
  const [agent, setAgent] = useState(
    draftState.selection?.agent_id ?? setup.default_agent ?? "",
  );
  const [environment, setEnvironment] = useState(
    draftState.selection?.environment_profile_id ?? setup.environment_profile,
  );
  useEffect(() => {
    if (!id)
      dispatch({
        kind: "selection",
        id: "new",
        selection: {
          project_id: project,
          agent_id: agent,
          environment_profile_id: environment,
        },
      });
  }, [id, project, agent, environment]);
  const [checked, setChecked] = useState(false);
  const [readiness, setReadiness] = useState<Model<"EnvironmentReadiness">>();
  const [operation, setOperation] = useState<Model<"RootOperationView">>();
  const mounted = useRef(true);
  const pending = useRef<AbortController | null>(null);
  const timeline = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
      pending.current?.abort();
    },
    [],
  );
  useEffect(() => {
    setChecked(false);
    setReadiness(undefined);
    pending.current?.abort();
  }, [environment, project]);
  const selectors = useQuery({
    queryKey: ["selectors"],
    queryFn: async ({ signal }) =>
      result(await api.GET("/api/selectors", { signal })),
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: async ({ signal }) =>
      result(await api.GET("/api/projects", { signal })),
  });
  const detail = useQuery({
    queryKey: ["thread", id],
    queryFn: async ({ signal }) =>
      result(
        await api.GET("/api/threads/{thread_id}", {
          params: { path: { thread_id: id! } },
          signal,
        }),
      ),
    enabled: !!id && !!snapshot,
  });
  const current = detail.data ?? snapshot?.thread;
  const continuation = current?.continuation_id;
  const history = useInfiniteQuery({
    queryKey: ["transcript", id, continuation],
    queryFn: async ({ pageParam, signal }) =>
      result(
        await api.GET("/api/threads/{thread_id}/transcript", {
          params: {
            path: { thread_id: id! },
            query: {
              expected_continuation_id: continuation ?? undefined,
              cursor: pageParam,
            },
          },
          signal,
        }),
      ),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !!id && !!snapshot,
  });
  const decisions = useQuery({
    queryKey: ["decisions", id, continuation],
    queryFn: async ({ signal }) =>
      result(
        await api.GET("/api/threads/{thread_id}/decisions", {
          params: { path: { thread_id: id! } },
          signal,
        }),
      ),
    enabled: !!id && !!current?.deferred_requests?.length,
  });
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let epoch = "";
    let sequence = -1;
    let after: string | undefined;
    const connect = () =>
      void stream<FocusFrame>(
        `/api/threads/${encodeURIComponent(id)}/events${after ? `?after=${encodeURIComponent(after)}` : ""}`,
        "#/paths/~1api~1threads~1{thread_id}~1events/get/responses/200/content/application~1json/schema",
        controller.signal,
        (frame) => {
          if (frame.kind === "reset") {
            after = undefined;
            setLive([]);
            throw new Error("Live reset");
          }
          after = frame.resume_cursor;
          setStreamStatus("Live connected");
          if (frame.kind === "snapshot") {
            epoch = frame.snapshot.epoch;
            sequence = frame.snapshot.cutover_sequence;
            setSnapshot(frame.snapshot);
            setLive(frame.snapshot.recent_events ?? []);
            setOperation(frame.snapshot.root_operation ?? undefined);
            setStreamStatus("Live connected");
            void cache.invalidateQueries({ queryKey: ["thread", id] });
          } else {
            if (
              frame.event.epoch !== epoch ||
              frame.event.sequence < sequence
            ) {
              after = undefined;
              throw new Error("Live epoch or sequence changed");
            }
            if (frame.event.sequence === sequence) return;
            sequence = frame.event.sequence;
            setLive((events) => [...events, frame.event].slice(-500));
          }
        },
      ).catch((error) => {
        if (controller.signal.aborted) return;
        setStreamStatus(
          "Live disconnected — retained history is still available",
        );
        if (error instanceof ApiError) {
          setError(message(error));
          return;
        }
        timer = setTimeout(connect, 1500);
      });
    connect();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, cache]);
  useEffect(() => {
    if (follow.current)
      timeline.current?.scrollTo({ top: timeline.current.scrollHeight });
  }, [live, history.data]);
  const activity = current?.thread.root_activity;
  const receipt = activity?.receipt_id;
  useEffect(() => {
    if (!receipt) return;
    let valid = true;
    void api
      .GET("/api/operations/{receipt_id}", {
        params: { path: { receipt_id: receipt } },
      })
      .then(result)
      .then((value) => {
        if (valid) setOperation(value);
      })
      .catch(() => {});
    return () => {
      valid = false;
    };
  }, [receipt, activity?.state]);
  useEffect(() => {
    if (receipt || !operation?.receipt.receipt_id) return;
    let valid = true;
    void api
      .GET("/api/operations/{receipt_id}", {
        params: { path: { receipt_id: operation.receipt.receipt_id } },
      })
      .then(result)
      .then((value) => {
        if (valid) setOperation(value);
      })
      .catch(() => {});
    return () => {
      valid = false;
    };
  }, [receipt, continuation]);
  const canSteer = activity?.available_actions.includes("steer") ?? false;
  const selectedAgent = selectors.data?.agents?.find(
    (item) =>
      item.agent_id ===
      (id ? current?.thread.configuration.agent_source.id : agent),
  );
  const modelMissing = !!selectedAgent && !selectedAgent.model_id;
  const legalSubmit = id
    ? (current?.available_actions?.includes("run") || canSteer) &&
      !current?.thread.archived
    : !!project &&
      !!agent &&
      (environment !== "environment-sandbox" || checked);
  const canSubmit =
    legalSubmit &&
    (!modelMissing || canSteer) &&
    !uncertain &&
    !draftState.pending;
  useEffect(() => {
    if (!lastReceipt) return;
    const controller = new AbortController();
    void api
      .GET("/api/operations/{receipt_id}", {
        params: { path: { receipt_id: lastReceipt } },
        signal: controller.signal,
      })
      .then(result)
      .then(setOperation)
      .catch(() => {});
    return () => controller.abort();
  }, [lastReceipt, continuation, activity?.state]);
  async function submit() {
    if (!draft.trim() || busy || !canSubmit) return;
    const submission = { source, text: draft, revision: draftState.revision };
    dispatch({ kind: "begin", submission });
    setBusy(true);
    setError("");
    let target = id;
    let receiptId: string | undefined;
    let failure: string | undefined;
    let unknown = false;
    try {
      if (!target) {
        const created = result(
          await api.POST("/api/threads", {
            body: {
              defaults: {
                project_id: project,
                agent_id: agent,
                environment_profile_id: environment,
              },
              title: submission.text.trim().slice(0, 100),
            },
          }),
        );
        target = created.thread_id;
        dispatch({ kind: "created", submission, target });
      }
      const response =
        canSteer && receipt
          ? result(
              await api.POST("/api/operations/{receipt_id}/steer", {
                params: { path: { receipt_id: receipt } },
                body: { prompt: submission.text },
              }),
            )
          : result(
              await api.POST("/api/threads/{thread_id}/submit", {
                params: { path: { thread_id: target } },
                body: { prompt: submission.text },
              }),
            );
      if ("accepted" in response && !response.accepted)
        throw new ApiError(
          "Steering was not accepted. Your prompt is retained; review the current activity before submitting again.",
          "steer_rejected",
        );
      if ("receipt_id" in response) receiptId = response.receipt_id;
    } catch (error) {
      failure = message(error);
      unknown =
        !(error instanceof ApiError) ||
        error.code === "protocol_mismatch" ||
        error.code === "request_failed";
    } finally {
      dispatch({
        kind: "finish",
        submission,
        target: target ?? source,
        error: failure,
        uncertain: unknown,
        receipt: receiptId,
      });
      if (target && !id && mounted.current) openThread(target);
      await cache.invalidateQueries();
      if (mounted.current) setBusy(false);
    }
  }
  async function probe() {
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError("");
    try {
      const roots =
        projects.data?.find((value) => value.project_id === project)?.roots ??
        [];
      if (!roots.length)
        throw new ApiError(
          "Select a configured project with roots.",
          "project_required",
        );
      for (const root of roots) {
        const value = result(
          await api.POST("/api/environments/preflight", {
            body: { profile_id: "environment-sandbox", project_path: root },
            signal: controller.signal,
          }),
        );
        if (controller.signal.aborted) return;
        setReadiness(value);
        if (!value.ready) return;
      }
      setChecked(true);
    } catch (error) {
      if (!controller.signal.aborted) setError(message(error));
    } finally {
      if (mounted.current && pending.current === controller) {
        setBusy(false);
        pending.current = null;
      }
    }
  }
  async function cancel() {
    if (!receipt || busy) return;
    setBusy(true);
    try {
      result(
        await api.POST("/api/operations/{receipt_id}/cancel", {
          params: { path: { receipt_id: receipt } },
        }),
      );
    } catch (error) {
      setError(message(error));
    } finally {
      await cache.invalidateQueries();
      setBusy(false);
    }
  }
  async function archive() {
    if (!id || !current) return;
    setBusy(true);
    try {
      result(
        await api.PATCH("/api/threads/{thread_id}/metadata", {
          params: { path: { thread_id: id } },
          body: {
            expected_version: current.thread.metadata_version,
            patch: { archived: !current.thread.archived },
          },
        }),
      );
      await cache.invalidateQueries();
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }
  const retained =
    history.data?.pages
      .slice()
      .reverse()
      .flatMap((page) => page.entries) ?? [];
  // Stream output is provisional and bounded. Never merge it into retained history.
  const liveText = live
    .filter(
      (event) =>
        event.run_id === activity?.run_id &&
        event.event_type === "TEXT_MESSAGE_CONTENT" &&
        typeof event.payload?.delta === "string",
    )
    .map((event) => event.payload!.delta)
    .join("");
  return (
    <section className="conversation">
      <header className="conversation-header">
        <div>
          <h1>
            {current?.thread.title ??
              (id ? "Conversation" : "What would you like to work on?")}
          </h1>
          <p className="muted">
            {id
              ? `${current?.thread.configuration.project_id ?? id} · ${current?.thread.configuration.environment_profile_id ?? ""} · ${activity?.state ?? "Loading"}`
              : "One conversation at a time. Other server-owned work continues when you switch."}
          </p>
        </div>
        {current && (
          <button
            disabled={
              busy ||
              (!current.thread.archived &&
                !current.available_actions?.includes("archive"))
            }
            onClick={() => void archive()}
          >
            {current.thread.archived ? "Restore" : "Archive"}
          </button>
        )}
      </header>
      {!id && (
        <div className="draft-selectors">
          <label>
            Project
            <select
              value={project}
              onChange={(e) => setProject(e.target.value)}
            >
              <option value="" disabled>
                Select project
              </option>
              {Object.entries(setup.projects).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Agent
            <select value={agent} onChange={(e) => setAgent(e.target.value)}>
              <option value="" disabled>
                Select agent
              </option>
              {Object.entries(setup.agents).map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Environment
            <select
              value={environment}
              onChange={(e) => setEnvironment(e.target.value)}
            >
              {selectors.data?.environments.map((profile) => (
                <option key={profile.profile_id} value={profile.profile_id}>
                  {profile.name}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            {environment === "environment-native"
              ? "Full Control: no Sandbox. Commands run as your host account with ambient filesystem and network access."
              : selectors.data?.environments.find(
                  (value) => value.profile_id === environment,
                )?.description}
          </p>
          {environment === "environment-sandbox" && (
            <button disabled={busy} onClick={() => void probe()}>
              Check Sandbox / Retry
            </button>
          )}
          {readiness && (
            <p role="status" className={readiness.ready ? "notice" : "error"}>
              {readiness.message}
              {!readiness.ready && (
                <button onClick={() => setEnvironment("environment-native")}>
                  Choose Full Control (no Sandbox)
                </button>
              )}
            </p>
          )}
        </div>
      )}
      <div
        className="timeline"
        ref={timeline}
        onScroll={(event) => {
          const view = event.currentTarget;
          follow.current =
            view.scrollHeight - view.scrollTop - view.clientHeight < 80;
        }}
      >
        {history.hasNextPage && (
          <button
            disabled={history.isFetching}
            onClick={() => {
              follow.current = false;
              void history.fetchNextPage();
            }}
          >
            Load older messages
          </button>
        )}
        {!id && (
          <div className="empty">
            <h2>Start with a goal.</h2>
            <p>
              Ask the agent to inspect a project, make a focused change, or
              explain a design. Your first message creates the conversation.
            </p>
          </div>
        )}
        {retained.map((entry) => (
          <article
            className={`message ${entry.message_kind}`}
            key={entry.position}
          >
            {entry.parts.map((part, index) =>
              part.kind === "thinking" ||
              part.kind === "tool_call" ||
              part.kind === "tool_result" ? (
                <details key={index}>
                  <summary>{part.tool_name ?? part.kind}</summary>
                  <pre>
                    {part.text ?? JSON.stringify(part.value, null, 2)}
                    {part.value_omitted ? "\n[Omitted by App]" : ""}
                  </pre>
                </details>
              ) : (
                <div key={index}>
                  <span className="eyebrow">{part.kind}</span>
                  <Markdown text={part.text ?? "[Non-text content]"} />
                </div>
              ),
            )}
          </article>
        ))}
        {!!liveText && activity?.state !== "inactive" && (
          <article className="message provisional">
            <span className="eyebrow">LIVE · NOT YET RETAINED</span>
            <Markdown text={liveText.slice(-64 * 1024)} />
          </article>
        )}
        {!!live.length && (
          <details className="activity">
            <summary>
              Current-process activity ({live.length}
              {live.length === 500 ? "+" : ""} events retained in this view)
            </summary>
            {live.slice(-60).map((event) => (
              <details key={event.sequence}>
                <summary>
                  {event.event_type} · {event.thread_id}
                </summary>
                <pre>{JSON.stringify(event.payload, null, 2)}</pre>
              </details>
            ))}
          </details>
        )}
        {snapshot?.children.executions.map((child) => (
          <details key={child.execution_id}>
            <summary>
              {child.subagent_name} ·{" "}
              {child.local_status === "active"
                ? "Active in this server"
                : child.persisted_status}
            </summary>
            <pre>{child.activity.output_preview}</pre>
          </details>
        ))}
        {operation?.failure && (
          <p role="alert" className="error">
            {operation.failure.message}
          </p>
        )}
        {history.error && (
          <p role="alert" className="error">
            {message(history.error)}{" "}
            <button onClick={() => void history.refetch()}>
              Reload history
            </button>
          </p>
        )}
      </div>
      <footer className="composer">
        <p className="muted" role="status">
          {id ? streamStatus : "Draft only — no conversation has been created"}
          {busy || draftState.pending ? " · Request in progress" : ""}
        </p>
        {error && (
          <p role="alert" className="error">
            {error}{" "}
            <button onClick={() => void cache.invalidateQueries()}>
              Refresh authoritative state
            </button>
            {uncertain && (
              <button
                onClick={() => dispatch({ kind: "acknowledge", id: source })}
              >
                I reviewed the current state; enable another attempt
              </button>
            )}
          </p>
        )}
        {modelMissing && !canSteer && (
          <p role="status" className="notice">
            This Agent has no model yet. Open Setup to connect a model and
            select a configured Agent. Your draft is kept.
          </p>
        )}
        {detail.error && (
          <p role="alert" className="error">
            {message(detail.error)}
          </p>
        )}
        {decisions.data && id && current?.deferred_requests?.length ? (
          <Decisions
            key={decisions.data.continuation_id}
            batch={decisions.data}
            id={id}
            refresh={() => cache.invalidateQueries()}
          />
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <label className="sr-only" htmlFor="prompt">
              {canSteer ? "Steer current run" : "Message"}
            </label>
            <textarea
              id="prompt"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={
                canSteer
                  ? "Steer the current run…"
                  : "Describe what you want to do…"
              }
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  void submit();
                }
              }}
            />
            <div className="actions">
              <span className="muted">
                Shift Enter for a newline. No queued messages.
              </span>
              {activity?.available_actions.includes("cancel") && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void cancel()}
                >
                  Cancel run
                </button>
              )}
              <button
                className="primary"
                disabled={busy || !canSubmit || !draft.trim()}
              >
                {canSteer ? "Steer" : busy ? "Submitting…" : "Send"}
              </button>
            </div>
          </form>
        )}
      </footer>
    </section>
  );
}

function Decisions({
  batch,
  id,
  refresh,
}: {
  batch: Model<"DecisionBatchView">;
  id: string;
  refresh: () => Promise<void>;
}) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function send() {
    setBusy(true);
    setError("");
    try {
      const responses: Model<"DecisionResponseBatch">["responses"] =
        batch.requests.map((request) => {
          const value = values[request.request_id];
          if (!value?.trim())
            throw new ApiError(
              "Respond to every pending request before continuing.",
              "response_required",
            );
          if (request.kind === "approval")
            return {
              kind: "approval",
              request_id: request.request_id,
              approved: value === "approve",
              ...(value === "deny" ? { denial_message: "Denied by user" } : {}),
            };
          if (request.kind === "question")
            return {
              kind: "question",
              request_id: request.request_id,
              response: value,
            };
          return {
            kind: "external",
            request_id: request.request_id,
            denied: false,
            result: JSON.parse(value) as Model<"ExternalToolResult">["result"],
          };
        });
      result(
        await api.POST("/api/threads/{thread_id}/decisions", {
          params: { path: { thread_id: id } },
          body: { expected_continuation_id: batch.continuation_id, responses },
        }),
      );
      await refresh();
    } catch (error) {
      setError(
        error instanceof SyntaxError
          ? "External results must be valid JSON."
          : message(error),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void send();
      }}
    >
      <h2>Decision required</h2>
      <p>
        Respond to the complete request set. Nothing continues until this exact
        continuation accepts your response.
      </p>
      {batch.requests.map((request) => (
        <fieldset key={request.request_id} disabled={busy}>
          <legend>{request.tool_name}</legend>
          {request.kind === "question" ? (
            <>
              {request.questions.map((question) => (
                <div key={question.header}>
                  <strong>{question.question}</strong>
                  {question.options.map((option) => (
                    <p key={option.label}>
                      {option.label}: {option.description}
                    </p>
                  ))}
                </div>
              ))}
              <label>
                Your response
                <textarea
                  value={values[request.request_id] ?? ""}
                  onChange={(e) =>
                    setValues((current) => ({
                      ...current,
                      [request.request_id]: e.target.value,
                    }))
                  }
                  required
                />
              </label>
            </>
          ) : (
            <>
              <pre>{JSON.stringify(request.arguments, null, 2)}</pre>
              {request.kind === "approval" ? (
                <label>
                  Approval
                  <select
                    value={values[request.request_id] ?? ""}
                    onChange={(e) =>
                      setValues((current) => ({
                        ...current,
                        [request.request_id]: e.target.value,
                      }))
                    }
                    required
                  >
                    <option value="" disabled>
                      Choose explicitly
                    </option>
                    <option value="approve">Approve</option>
                    <option value="deny">Deny</option>
                  </select>
                </label>
              ) : (
                <label>
                  External result (JSON)
                  <textarea
                    value={values[request.request_id] ?? ""}
                    onChange={(e) =>
                      setValues((current) => ({
                        ...current,
                        [request.request_id]: e.target.value,
                      }))
                    }
                    required
                  />
                </label>
              )}
            </>
          )}
        </fieldset>
      ))}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button disabled={busy} className="primary">
        Submit all decisions
      </button>
    </form>
  );
}
