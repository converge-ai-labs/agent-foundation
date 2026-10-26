// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { createClient, type Client } from "../../service-client";
import { SessionLayout, ThreadLayout } from "./page";
import {
  fixtureRun,
  fixtureRunPage,
  fixtureThread,
} from "./transcript/fixture";

let client: Client;
let cache: QueryClient;
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  useAuth: () => ({ data: undefined }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" }, basePath: "" }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("./session-header", () => ({ SessionHeader: () => null }));
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
      if (url.pathname === "/api/v1/threads") {
        // The root can be on a later page than recent child activity.
        const next = url.searchParams.has("cursor");
        return Response.json({
          items: next ? threads.slice(1) : threads.slice(0, 1),
          next_cursor: next ? null : "next",
        });
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
    .click(screen.getByRole("link", { name: /thr_other/ }));
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
    screen.getByRole("link", { name: /thr_fork/ }).getAttribute("href"),
  ).toBe("/sessions/ses_1/threads/thr_fork?view=chat");
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
  expect(requested).not.toContain("/api/v1/threads");
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
  expect(requested).not.toContain("/api/v1/threads");
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
