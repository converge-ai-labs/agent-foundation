import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router";
import { Suspense } from "react";
import { createClient, type Client } from "../../../service-client";
import {
  fixtureRun,
  fixtureRunPage,
  fixtureThread,
  textInput,
} from "./fixture";
import { RunPage } from "./run";
import { conversationKeys } from "../api";

let client: Client;
let cache: QueryClient;
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/demo",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
vi.mock("../../agents/queries", () => ({
  useAgent: () => ({ data: null }),
  agentQuery: (_client: unknown, _workspace: string, id: string) => ({
    queryKey: ["agent", id],
    queryFn: async () => ({ name: "Agent" }),
  }),
}));
vi.mock("./inbox", () => ({ ThreadInbox: () => null }));
vi.mock("../run-display", () => ({
  useRunDisplay: () => ({
    first: 1,
    items: [],
    earlier: { items: [], more: false, loading: false, error: null },
    attempts: [],
    state: "connected",
    execution: {
      steps: [],
      observations: [],
      retries: [],
      usage: { model: [], provider: [], recordIds: [] },
      contextTokens: {},
      coverage: "complete",
    },
  }),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const path = (id: string) =>
  `/workspace/demo/sessions/session/threads/thread/runs/${id}`;
function Location() {
  const location = useLocation();
  return (
    <>
      <output data-testid="location">
        {location.pathname}
        {location.search}
      </output>
      <Link to={path("run")}>Earlier run</Link>
      <Link to={`${location.pathname}${location.search}&follow=latest`}>
        Follow latest
      </Link>
    </>
  );
}
function show(
  level: "chat" | "debug" = "chat",
  delayNavigation = false,
  follow = false,
  skipped = false,
) {
  const navigation = deferred<void>();
  function Destination() {
    const { runId } = useParams();
    if (runId === "next" && delayNavigation) throw navigation.promise;
    return <RunPage />;
  }
  const submitted = deferred<Response>();
  const refreshed = deferred<Response>();
  const initial = fixtureRun({
    id: "run",
    thread_id: "thread",
    session_id: "session",
    parent_run_id: null,
    input: textInput("First request"),
    output: "First reply",
  });
  const next = fixtureRun({
    ...initial,
    id: "next",
    parent_run_id: skipped ? "middle" : "run",
    status: "accepted",
    sealed_at: null,
    input: textInput("Next request"),
    output: null,
  });
  const middle = fixtureRun({
    ...initial,
    id: "middle",
    parent_run_id: "run",
    input: textInput("Intervening request"),
    output: "Intervening reply",
  });
  let thread = fixtureThread({
    id: "thread",
    session_id: "session",
    last_run_id: "run",
  });
  const queries: string[] = [];
  const posts: Request[] = [];
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      const url = new URL(request.url);
      if (request.method === "POST") {
        posts.push(request);
        return submitted.promise;
      }
      queries.push(url.pathname);
      if (url.pathname.endsWith("/runs/next")) return refreshed.promise;
      if (url.pathname.endsWith("/runs/run")) return Response.json(initial);
      if (url.pathname.endsWith("/runs/middle")) return Response.json(middle);
      if (url.pathname.endsWith("/runs/next/lineage"))
        return Response.json(
          url.searchParams.has("cursor")
            ? { items: [middle, initial], next_cursor: null }
            : { items: [next], next_cursor: "older" },
        );
      if (url.pathname.endsWith("/threads/thread"))
        return Response.json(thread);
      if (url.pathname.endsWith("/sessions/session"))
        return Response.json({
          id: "session",
          labels: { "a13n.console": "debug" },
        });
      if (url.pathname.endsWith("/lineage"))
        return Response.json({ items: [initial], next_cursor: null });
      if (url.pathname.endsWith("/runs"))
        return Response.json(fixtureRunPage([next, initial]));
      if (url.pathname.endsWith("/threads"))
        return Response.json({ items: [thread], next_cursor: null });
      throw new Error(`Unexpected request: ${url.pathname}`);
    },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[
          `${path("run")}?view=${level}${follow ? "&follow=latest" : ""}`,
        ]}
      >
        <div data-session-stage>
          <Suspense fallback={<p>Opening run</p>}>
            <Routes>
              <Route
                path="/workspace/demo/sessions/:sessionId/threads/:threadId/runs/:runId"
                element={<Destination />}
              />
            </Routes>
          </Suspense>
        </div>
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  Object.assign(view.container.querySelector("[data-session-stage]")!, {
    scrollTo: vi.fn(),
  });
  return {
    ...view,
    posts,
    queries,
    next,
    submitted,
    refreshed,
    finishNavigation() {
      delayNavigation = false;
      navigation.resolve();
    },
    automaticSuccessor() {
      thread = {
        ...thread,
        current_run_id: "next",
        version: thread.version + 1,
      };
      cache.setQueryData(
        conversationKeys("workspace").thread("thread"),
        thread,
      );
      refreshed.resolve(Response.json(next));
    },
    accept() {
      thread = {
        ...thread,
        current_run_id: "next",
        version: thread.version + 1,
      };
      submitted.resolve(
        Response.json({ run: next, thread, entry: { id: "entry" } }),
      );
    },
  };
}
afterEach(() => {
  cleanup();
  cache?.clear();
  client?.close();
  vi.unstubAllGlobals();
});

it("keeps the accepted optimistic message and composer visible until navigation commits", async () => {
  const view = show("chat", true);
  await screen.findByText("First request", { selector: "div" });
  const composer = screen.getByRole("textbox", { name: "Message" });
  fireEvent.change(composer, { target: { value: "Next request" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await screen.findByText("Next request", { selector: "div" });
  await act(async () => view.accept());
  expect(screen.getByText("Next request", { selector: "div" })).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "Message" })).toBe(composer);
  expect(screen.queryByText("You are viewing earlier work.")).toBeNull();
  await act(async () => view.finishNavigation());
  await waitFor(() =>
    expect(screen.getByTestId("location").textContent).toBe(
      `${path("next")}?view=chat`,
    ),
  );
  expect(screen.getAllByText("Next request", { selector: "div" })).toHaveLength(
    1,
  );
  await act(async () => view.refreshed.resolve(Response.json(view.next)));
});

it.each(["chat", "debug"] as const)(
  "appends the accepted message in %s without replacing the transcript or composer",
  async (level) => {
    const view = show(level);
    const first = await screen.findByText("First request", { selector: "div" });
    const composer = screen.getByRole("textbox", { name: "Message" });
    // Watch the whole transition, including transient loading states.
    let removed = false;
    const observer = new MutationObserver((records) => {
      for (const record of records)
        for (const node of record.removedNodes) {
          if (node === first || node.contains(first)) removed = true;
        }
    });
    observer.observe(view.container, { childList: true, subtree: true });
    fireEvent.change(composer, { target: { value: "Next request" } });
    fireEvent.click(
      screen.getByRole("button", {
        name: level === "debug" ? "Start run" : "Send message",
      }),
    );
    expect(
      await screen.findByText("Next request", { selector: "div" }),
    ).toBeTruthy();
    expect(screen.getByText("First request", { selector: "div" })).toBe(first);
    await waitFor(() => expect(view.posts).toHaveLength(1));
    await act(async () => view.accept());
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(
        `${path("next")}?view=${level}`,
      ),
    );
    expect(screen.getByText("First request", { selector: "div" })).toBe(first);
    expect(
      screen.getAllByText("Next request", { selector: "div" }),
    ).toHaveLength(1);
    expect(screen.getByRole("textbox", { name: "Message" })).toBe(composer);
    await waitFor(() => expect(composer).toHaveProperty("value", ""));
    expect(view.container.querySelector('[data-slot="skeleton"]')).toBeNull();
    expect(removed).toBe(false);
    await act(async () => view.refreshed.resolve(Response.json(view.next)));
    // Explicit history navigation remains an inspection of that run alone.
    fireEvent.click(screen.getByRole("link", { name: "Earlier run" }));
    await waitFor(() =>
      expect(
        screen.queryByText("Next request", { selector: "div" }),
      ).toBeNull(),
    );
    observer.disconnect();
  },
);

it("keeps the draft and existing transcript when sending fails", async () => {
  const view = show();
  const first = await screen.findByText("First reply");
  const composer = screen.getByRole("textbox", { name: "Message" });
  fireEvent.change(composer, { target: { value: "Next request" } });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await screen.findByText("Next request", { selector: "div" });
  await act(async () =>
    view.submitted.resolve(
      Response.json({ detail: "Temporarily unavailable" }, { status: 503 }),
    ),
  );
  await screen.findByRole("alert");
  expect(view.container.querySelector('[aria-busy="true"]')).toBeNull();
  expect(screen.getByText("First reply")).toBe(first);
  expect(composer).toHaveProperty("value", "Next request");
  expect(view.container.querySelector('[data-slot="skeleton"]')).toBeNull();
  expect(screen.getByTestId("location").textContent).toBe(
    `${path("run")}?view=chat`,
  );
});

it.each(["chat", "debug"] as const)(
  "follows an automatic successor in %s without losing a draft",
  async (level) => {
    const view = show(level, false, true);
    await screen.findByText("First request", { selector: "div" });
    const composer = screen.getByRole("textbox", { name: "Message" });
    fireEvent.change(composer, { target: { value: "Unsent draft" } });
    await act(async () => view.automaticSuccessor());
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(
        `${path("next")}?view=${level}&follow=latest`,
      ),
    );
    expect(screen.getByRole("textbox", { name: "Message" })).toBe(composer);
    expect((composer as HTMLTextAreaElement).value).toBe("Unsent draft");
    expect(screen.queryByText("You are viewing earlier work.")).toBeNull();
  },
);

it("leaves an explicitly inspected Run in place when a successor arrives", async () => {
  const view = show();
  await screen.findByText("First request", { selector: "div" });
  await act(async () => view.automaticSuccessor());
  expect(screen.getByTestId("location").textContent).toBe(
    `${path("run")}?view=chat`,
  );
  expect(await screen.findByText("You are viewing earlier work.")).toBeTruthy();
});

it.each([true, false])(
  "includes paginated intervening Runs when following (already enabled: %s)",
  async (following) => {
    const view = show("chat", false, following, true);
    const first = await screen.findByText("First request", { selector: "div" });
    const composer = screen.getByRole("textbox", { name: "Message" });
    fireEvent.change(composer, { target: { value: "Unsent draft" } });
    await act(async () => view.automaticSuccessor());
    if (!following)
      fireEvent.click(screen.getByRole("link", { name: "Follow latest" }));
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(
        `${path("next")}?view=chat&follow=latest`,
      ),
    );
    expect(
      await screen.findByText("Intervening request", { selector: "div" }),
    ).toBeTruthy();
    expect(screen.getByText("Next request", { selector: "div" })).toBeTruthy();
    expect(screen.getByText("First request", { selector: "div" })).toBe(first);
    if (following) {
      expect(screen.getByRole("textbox", { name: "Message" })).toBe(composer);
      expect(composer).toHaveProperty("value", "Unsent draft");
    }
    expect(
      view.queries.filter((url) => url.endsWith("/runs/next/lineage")),
    ).toHaveLength(2);
  },
);
