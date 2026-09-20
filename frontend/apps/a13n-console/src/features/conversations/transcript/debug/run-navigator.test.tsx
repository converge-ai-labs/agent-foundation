// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { createClient, type Client } from "../../../../service-client";
import { RunNavigator } from "./run-navigator";
import { fixtureRun, fixtureThread } from "../fixture";

let client: Client;
let cache: QueryClient;
vi.mock("../../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/design",
    can: () => true,
  }),
}));
vi.mock("../../../agents/queries", () => ({
  useAgent: (agentId?: string) => ({
    data: agentId
      ? { id: agentId, key: "researcher", name: "Researcher" }
      : null,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options
        ? key.replace(/{{(\w+)}}/g, (_, name) => String(options[name] ?? ""))
        : key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

const child = fixtureThread({
  id: "thr_child",
  role: "child",
  origin_thread_id: "thr_1",
  origin_run_id: "run_2",
  current_run_id: "run_child",
  head_run_id: "run_child",
});

beforeEach(() => {
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      if (url.pathname.endsWith("/sessions/ses_1/threads"))
        return Response.json({
          items: [fixtureThread(), child],
          next_cursor: null,
        });
      if (url.pathname.endsWith("/threads/thr_1/runs"))
        return Response.json({
          items: [
            fixtureRun({
              id: "run_1",
              input: null,
              input_text: "Review the release",
            }),
            fixtureRun({
              id: "run_2",
              input: null,
              input_text: "Write the marker",
              status: "waiting",
              completed_at: null,
            }),
          ],
          next_cursor: null,
        });
      if (url.pathname.endsWith("/threads/thr_child/runs"))
        return Response.json({
          items: [
            fixtureRun({
              id: "run_child",
              thread_id: "thr_child",
              agent_id: "agt_child",
              input: null,
              input_text: "Find prior incidents",
            }),
          ],
          next_cursor: null,
        });
      throw new Error(`Unexpected request: ${url.pathname}`);
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

function Location() {
  const { pathname, search } = useLocation();
  return <p>{`${pathname}${search}`}</p>;
}

function show() {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/start"]}>
        <RunNavigator thread={fixtureThread()} runId="run_2" />
        <Routes>
          <Route path="*" element={<Location />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("lists every run of the thread and of its child threads, and opens one", async () => {
  show();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Run 2 of 2/ }));
  const items = await screen.findAllByRole("menuitem");
  expect(items.map((item) => item.textContent)).toEqual([
    "Run 1Review the release12s",
    "Run 2Write the markerstate.waiting",
    "Run 1Find prior incidents12s",
  ]);
  // The child thread is named by the agent that answers it.
  expect(screen.getByText("Researcher")).toBeTruthy();
  expect(screen.queryByText("Child thread")).toBeNull();
  await user.click(items[2]!);
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_child/runs/run_child?view=debug",
    ),
  ).toBeTruthy();
});

it("steps to the previous run and keeps the debug level", async () => {
  show();
  const user = userEvent.setup();
  await screen.findByRole("button", { name: /Run 2 of 2/ });
  await user.click(screen.getByRole("button", { name: "Previous run" }));
  expect(
    await screen.findByText(
      "/workspace/design/sessions/ses_1/threads/thr_1/runs/run_1?view=debug",
    ),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Next run" }).hasAttribute("disabled"),
  ).toBe(true);
});
