// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { createClient, type Client } from "../../service-client";
import { SessionHeader } from "./session-header";
import { RunCollapseProvider } from "./transcript/debug/collapse";
import { DebugRunSection } from "./transcript/debug/run-section";
import {
  fixtureRun,
  fixtureThread,
  fixtureTimeline,
} from "./transcript/fixture";

let client: Client;
let cache: QueryClient;
/** Sessions the Console starts carry its label; applications' do not. */
let labels: Record<string, string>;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/design",
    can: () => true,
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
vi.mock("../agents/queries", () => ({
  useAgent: () => ({
    data: { id: "agt_1", key: "release-bot", name: "Release Bot" },
  }),
}));

const child = fixtureThread({
  id: "thr_child",
  origin: "child",
  origin_thread_id: "thr_1",
});

beforeEach(() => {
  labels = { "a13n.console": "debug" };
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      if (url.pathname.endsWith("/runs"))
        return Response.json({
          items: [fixtureRun({ id: "run_1" }), fixtureRun()],
          next_cursor: null,
        });
      if (url.pathname.endsWith("/threads"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.endsWith("/sessions/ses_1"))
        return Response.json({
          id: "ses_1",
          workspace_id: "workspace",
          labels,
          created_by_id: "usr_1",
          last_run_id: "run_2",
          version: 1,
          created_at: "2026-09-20T10:00:00.000Z",
          updated_at: "2026-09-20T10:00:12.000Z",
        });
      return Response.json(fixtureRun({ status: "running" }));
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

function Location() {
  const { search } = useLocation();
  return <p>{`level:${search}`}</p>;
}

function show(
  threads: ReturnType<typeof fixtureThread>[],
  path = "/sessions/ses_1/threads/thr_1/runs/run_2",
  section = false,
) {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/sessions/:sessionId/threads/:threadId/runs/:runId"
            element={
              <RunCollapseProvider>
                <SessionHeader threads={threads} />
                <Location />
                {section && (
                  <DebugRunSection
                    run={fixtureRun()}
                    thread={fixtureThread()}
                    timeline={fixtureTimeline()}
                    index={1}
                  />
                )}
              </RunCollapseProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("identifies the session and switches the level in the URL", async () => {
  show([fixtureThread()]);
  const user = userEvent.setup();
  expect(screen.getByRole("link", { name: /Release Bot/ })).toBeTruthy();
  // The identifier stays one action away instead of leading the header.
  expect(screen.queryByText("ses_1")).toBeNull();
  expect(await screen.findByText("Debug session · 2 runs")).toBeTruthy();
  // The header reports the followed run's state; stopping belongs to the dock.
  expect(await screen.findByText("state.running")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  expect(screen.getByText("level:")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Debug" }));
  expect(await screen.findByText("level:?view=debug")).toBeTruthy();
  // Chat is written out too: the level a reader chose outlives a reload.
  await user.click(screen.getByRole("button", { name: "Chat" }));
  expect(await screen.findByText("level:?view=chat")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Session actions" }));
  expect(
    await screen.findByRole("menuitem", { name: "Copy session ID" }),
  ).toBeTruthy();
});

it("opens an application's session in Debug and still offers Chat", async () => {
  labels = {};
  show([fixtureThread()]);
  const user = userEvent.setup();
  expect(await screen.findByText("Started by trigger.input")).toBeTruthy();
  expect(screen.getByText("level:")).toBeTruthy();
  // Only the Debug level collapses runs, so its menu says which level is open.
  await user.click(screen.getByRole("button", { name: "Session actions" }));
  expect(
    await screen.findByRole("menuitem", { name: "Collapse all runs" }),
  ).toBeTruthy();
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "Chat" }));
  expect(await screen.findByText("level:?view=chat")).toBeTruthy();
});

it("opens a child thread in Debug and still offers Chat", async () => {
  show(
    [fixtureThread(), child],
    "/sessions/ses_1/threads/thr_child/runs/run_2",
  );
  const user = userEvent.setup();
  expect(screen.getByText("level:")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Chat" }));
  expect(await screen.findByText("level:?view=chat")).toBeTruthy();
});

it("keeps the switch beside the breadcrumb inside a child thread", () => {
  show(
    [fixtureThread(), child],
    "/sessions/ses_1/threads/thr_child/runs/run_2",
  );
  expect(screen.getByRole("button", { name: "Chat" })).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Root thread" }).getAttribute("href"),
  ).toBe("/workspace/design/sessions/ses_1/threads/thr_1?view=debug");
});

it("collapses and expands every loaded run from the header menu", async () => {
  show(
    [fixtureThread()],
    "/sessions/ses_1/threads/thr_1/runs/run_2?view=debug",
    true,
  );
  const user = userEvent.setup();
  const heading = await screen.findByRole("button", { name: /Run 1/ });
  expect(heading.getAttribute("aria-expanded")).toBe("true");
  await user.click(screen.getByRole("button", { name: "Session actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Collapse all runs" }),
  );
  expect(
    screen.getByRole("button", { name: /Run 1/ }).getAttribute("aria-expanded"),
  ).toBe("false");
  await user.click(screen.getByRole("button", { name: "Session actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Expand all runs" }),
  );
  expect(
    screen.getByRole("button", { name: /Run 1/ }).getAttribute("aria-expanded"),
  ).toBe("true");
});
