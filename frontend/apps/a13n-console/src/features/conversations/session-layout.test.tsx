// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { createClient, type Client } from "../../service-client";
import { SessionLayout, ThreadLayout } from "./page";
import {
  fixtureNavigationPage,
  fixtureRun,
  fixtureRunPage,
  fixtureSession,
  fixtureThread,
} from "./transcript/fixture";

let client: Client;
let cache: QueryClient;
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  revalidateSession: vi.fn(),
  useAuth: () => ({ data: undefined }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" }, basePath: "" }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("./session-header", () => ({
  SessionHeader: () => <header data-testid="session-header" />,
}));
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

const root = fixtureThread({ id: "thr_root", last_run_id: "run_root" });
const child = fixtureThread({
  id: "thr_child",
  origin: "child",
  last_run_id: "run_child",
});
const fork = fixtureThread({
  id: "thr_fork",
  origin: "fork",
  last_run_id: "run_fork",
});

function Location() {
  const { pathname, search } = useLocation();
  return <output>{pathname + search}</output>;
}

function show(
  threads: ReturnType<typeof fixtureThread>[],
  path: string,
  scheduleId: string | null = null,
  navigationFails = false,
) {
  const requested: string[] = [];
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      requested.push(url.pathname);
      if (url.pathname === "/api/v1/sessions/ses_1")
        return Response.json({ id: "ses_1", schedule_id: scheduleId });
      if (
        url.pathname === "/api/v1/sessions/ses_1/threads" ||
        url.pathname === "/api/v1/threads"
      ) {
        if (navigationFails)
          return Response.json(
            {
              error: {
                code: "service_unavailable",
                message: "Navigation unavailable",
              },
            },
            { status: 503 },
          );
        // Root filtering is global; the first chronological Thread may be a child.
        const page = fixtureNavigationPage(threads, url.searchParams);
        return Response.json(page);
      }
      return Response.json(threads.find((t) => url.pathname.endsWith(t.id)));
    },
  });
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/schedules/:scheduleId" element={<Location />} />
          <Route path="/sessions/:sessionId" element={<SessionLayout />}>
            <Route path="threads/:threadId" element={<ThreadLayout />}>
              <Route path="runs/:runId" element={<Location />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return requested;
}

it("continues the unique root in Chat even when child activity is listed first", async () => {
  show([child, fork, root], "/sessions/ses_1?view=chat");
  expect(
    await screen.findByText(
      "/sessions/ses_1/threads/thr_root/runs/run_root?view=chat&follow=latest",
    ),
  ).toBeTruthy();
});

it("asks which history to open when more than one root exists", async () => {
  const other = fixtureThread({ id: "thr_other", last_run_id: "run_other" });
  show([child, root, other], "/sessions/ses_1?view=chat");
  await screen.findByText("Choose a thread");
  await userEvent
    .setup()
    .click(await screen.findByRole("link", { name: /thr_other/ }));
  expect(
    await screen.findByText(
      "/sessions/ses_1/threads/thr_other/runs/run_other?view=chat&follow=latest",
    ),
  ).toBeTruthy();
});

it("offers explicit histories instead of guessing when no root is available", async () => {
  show([child, fork], "/sessions/ses_1?view=chat");
  await screen.findByText("Choose a thread");
  expect(
    (await screen.findByRole("link", { name: /thr_fork/ })).getAttribute(
      "href",
    ),
  ).toBe("/sessions/ses_1/threads/thr_fork?view=chat");
});

it("replaces pending Chat resolution with its retryable error", async () => {
  show([root], "/sessions/ses_1?view=chat", null, true);
  await screen.findByText("Navigation unavailable");
  expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
  expect(document.querySelector("[data-session-loading]")).toBeNull();
  expect(screen.queryByText("No threads yet")).toBeNull();
});

it.each([
  [
    "/sessions/ses_1?view=debug",
    "/sessions/ses_1/threads/thr_child/runs/run_child?view=debug&follow=latest",
  ],
  [
    "/sessions/ses_1/threads/thr_fork?view=chat",
    "/sessions/ses_1/threads/thr_fork/runs/run_fork?view=chat&follow=latest",
  ],
  [
    "/sessions/ses_1/threads/thr_root/runs/run_old?view=debug",
    "/sessions/ses_1/threads/thr_root/runs/run_old?view=debug",
  ],
])(
  "preserves the inspection or explicit target of %s",
  async (path, expected) => {
    show([child, fork, root], path);
    expect(await screen.findByText(expected)).toBeTruthy();
  },
);

it("opens the Schedule from its fixed Session without listing historical Threads", async () => {
  const requested = show([root], "/sessions/ses_1", "sch_1");
  expect(await screen.findByText("/schedules/sch_1")).toBeTruthy();
  expect(requested).not.toContain("/api/v1/sessions/ses_1/threads");
});

it("loads only the selected Schedule Thread when opening historical output", async () => {
  const requested = show(
    [root, child],
    "/sessions/ses_1/threads/thr_root",
    "sch_1",
  );
  expect(
    await screen.findByText(
      "/sessions/ses_1/threads/thr_root/runs/run_root?follow=latest",
    ),
  ).toBeTruthy();
  expect(requested).toContain("/api/v1/threads/thr_root");
  expect(requested).not.toContain("/api/v1/sessions/ses_1/threads");
});

it("starts known thread, run, page and display reads while the session read is still pending", async () => {
  let finish!: (response: Response) => void;
  const session = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const requested: string[] = [];
  const run = fixtureRun();
  client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (request) => {
      const path = new URL((request as Request).url).pathname;
      requested.push(path);
      if (path.endsWith("/sessions/ses_1")) return session;
      if (path.endsWith("/items"))
        return Response.json({ run, items: [], complete: true });
      if (path.endsWith("/threads/thr_1/runs"))
        return Response.json(fixtureRunPage([run]));
      if (path.endsWith("/threads/thr_1"))
        return Response.json(fixtureThread());
      if (path.endsWith("/threads"))
        return Response.json({ items: [fixtureThread()], next_cursor: null });
      return Response.json(run);
    },
  });
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={["/sessions/ses_1/threads/thr_1/runs/run_2"]}
      >
        <Routes>
          <Route
            path="/sessions/:sessionId/threads/:threadId/runs/:runId"
            element={<SessionLayout />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(requested).toEqual(
      expect.arrayContaining([
        "/api/v1/sessions/ses_1",
        "/api/v1/threads/thr_1",
        "/api/v1/threads/thr_1/runs",
        "/api/v1/runs/run_2",
        "/api/v1/runs/run_2/items",
      ]),
    ),
  );
  await act(async () => finish(Response.json({ id: "ses_1" })));
});

it("keeps one opening surface and opens Run reads from one bounded Thread page", async () => {
  let finishSession!: (response: Response) => void;
  let finishTargets!: (response: Response) => void;
  const sessionRead = new Promise<Response>((resolve) => {
    finishSession = resolve;
  });
  const targetsRead = new Promise<Response>((resolve) => {
    finishTargets = resolve;
  });
  const requested: URL[] = [];
  const thread = fixtureThread();
  const run = fixtureRun();
  client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requested.push(url);
      if (url.pathname === "/api/v1/sessions/ses_1") return sessionRead;
      if (url.pathname === "/api/v1/threads") return targetsRead;
      if (url.pathname.endsWith("/items"))
        return Response.json({ run, items: [], complete: true });
      if (url.pathname === "/api/v1/threads/thr_1/runs")
        return Response.json(fixtureRunPage([run]));
      if (url.pathname === "/api/v1/runs/run_2") return Response.json(run);
      return new Response("", {
        headers: { "content-type": "text/event-stream" },
      });
    },
  });
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 15_000 } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/sessions/ses_1?view=debug"]}>
        <Routes>
          <Route path="/sessions/:sessionId" element={<SessionLayout />}>
            <Route path="threads/:threadId" element={<ThreadLayout />}>
              <Route path="runs/:runId" element={<Location />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const header = screen.getByTestId("session-header");
  expect(
    view.container.querySelectorAll("[data-session-loading]"),
  ).toHaveLength(1);
  await act(async () => finishSession(Response.json(fixtureSession())));
  await waitFor(() =>
    expect(requested.some((url) => url.pathname === "/api/v1/threads")).toBe(
      true,
    ),
  );
  expect(screen.getByTestId("session-header")).toBe(header);
  expect(
    view.container.querySelectorAll("[data-session-loading]"),
  ).toHaveLength(1);
  await act(async () =>
    finishTargets(Response.json({ items: [thread], next_cursor: null })),
  );
  await waitFor(() =>
    expect(
      requested.filter((url) => url.pathname === "/api/v1/runs/run_2/items"),
    ).toHaveLength(1),
  );
  expect(screen.getByTestId("session-header")).toBe(header);
  expect(
    requested
      .filter((url) => url.pathname === "/api/v1/threads/thr_1/runs")
      .map((url) => url.searchParams.get("anchor")),
  ).toEqual(["run_2"]);
  expect(
    requested
      .filter((url) => url.pathname === "/api/v1/threads")
      .map((url) => url.search),
  ).toEqual(["?session_id=ses_1&limit=1"]);
  await screen.findByText(
    "/sessions/ses_1/threads/thr_1/runs/run_2?view=debug&follow=latest",
  );
  expect(screen.getByTestId("session-header")).toBe(header);
  expect(view.container.querySelector("[data-session-loading]")).toBeNull();
  expect(
    requested.filter((url) => url.pathname === "/api/v1/threads/thr_1"),
  ).toHaveLength(0);
});
