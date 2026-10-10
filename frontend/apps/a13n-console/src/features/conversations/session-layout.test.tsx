// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { createClient, type Client } from "../../service-client";
import { SessionLayout, ThreadLayout } from "./page";
import { fixtureThread } from "./transcript/fixture";

let client: Client;
let cache: QueryClient;
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  useAuth: () => ({ data: undefined }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" } }),
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

function show(threads: ReturnType<typeof fixtureThread>[], path: string) {
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
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
          <Route path="/sessions/:sessionId" element={<SessionLayout />}>
            <Route path="threads/:threadId" element={<ThreadLayout />}>
              <Route path="runs/:runId" element={<Location />} />
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("continues the unique root in Chat even when child activity is listed first", async () => {
  show([child, fork, root], "/sessions/ses_1?view=chat");
  expect(
    await screen.findByText(
      "/sessions/ses_1/threads/thr_root/runs/run_root?view=chat",
    ),
  ).toBeTruthy();
});

it("asks which history to open when more than one root exists", async () => {
  const other = fixtureThread({ id: "thr_other", last_run_id: "run_other" });
  show([child, root, other], "/sessions/ses_1?view=chat");
  await screen.findByText("Choose a conversation");
  await userEvent
    .setup()
    .click(screen.getByRole("link", { name: /thr_other/ }));
  expect(
    await screen.findByText(
      "/sessions/ses_1/threads/thr_other/runs/run_other?view=chat",
    ),
  ).toBeTruthy();
});

it("offers explicit histories instead of guessing when no root is available", async () => {
  show([child, fork], "/sessions/ses_1?view=chat");
  await screen.findByText("Choose a conversation");
  expect(
    screen.getByRole("link", { name: /thr_fork/ }).getAttribute("href"),
  ).toBe("/sessions/ses_1/threads/thr_fork?view=chat");
});

it.each([
  [
    "/sessions/ses_1?view=debug",
    "/sessions/ses_1/threads/thr_child/runs/run_child?view=debug",
  ],
  [
    "/sessions/ses_1/threads/thr_fork?view=chat",
    "/sessions/ses_1/threads/thr_fork/runs/run_fork?view=chat",
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
