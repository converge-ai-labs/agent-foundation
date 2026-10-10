import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { conversationKeys, type ViewLevel } from "./api";
import { ForkRun } from "./fork-run";
import {
  fixtureForkThread,
  fixtureRun,
  fixtureThread,
  fixtureTimeline,
} from "./transcript/fixture";
import { RunBlock } from "./transcript/run-block";
import { DebugRunSection } from "./transcript/debug/run-section";

let client: Client;
let cache: QueryClient;
let allowed = true;
let rejection = false;
let queued = false;
const posts: {
  path: string;
  body: Schema["Fork"];
  key: string | null;
  workspace: string | null;
}[] = [];
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_1" },
    basePath: "/workspace/ws_1",
    can: () => allowed,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(options?.[name] ?? "")),
    i18n: { resolvedLanguage: "en" },
  }),
}));

const origin = fixtureRun({ options: { max_usage: { requests: 4 } } });
const next = fixtureRun({
  id: "run_fork",
  thread_id: "thr_fork",
  parent_run_id: origin.id,
  lineage: "fork",
  status: "accepted",
  sealed_at: null,
});
beforeEach(() => {
  allowed = true;
  rejection = false;
  queued = false;
  posts.length = 0;
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  cache.setQueryData(conversationKeys("ws_1").threads("ses_1"), [
    fixtureThread(),
  ]);
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      const path = new URL(request.url).pathname;
      if (request.method === "POST") {
        posts.push({
          path,
          body: await request.json(),
          key: request.headers.get("Idempotency-Key"),
          workspace: request.headers.get("X-Workspace-ID"),
        });
        if (rejection)
          return Response.json(
            {
              error: {
                code: "unavailable",
                message: "Acknowledgement unavailable. Try again.",
              },
            },
            { status: 503 },
          );
        return Response.json(
          {
            thread: fixtureForkThread,
            run: queued ? null : next,
            entry: { id: "inb_fork", status: "assigned" },
          },
          { status: 201 },
        );
      }
      if (path.includes("/agents/"))
        return Response.json({ id: "agt_1", name: "Researcher" });
      if (path.endsWith("/threads"))
        return Response.json({
          items:
            posts.length && !rejection
              ? [fixtureThread(), fixtureForkThread]
              : [fixtureThread()],
          next_cursor: null,
        });
      throw new Error(`Unexpected request: ${path}`);
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

function Location() {
  const { pathname, search, state } = useLocation();
  return (
    <output data-testid="location">
      {pathname}
      {search}
      {JSON.stringify(state)}
    </output>
  );
}
function show(run = origin, level: ViewLevel = "chat", integrated = false) {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[`/original?view=${level}`]}>
        {integrated ? (
          level === "chat" ? (
            <RunBlock
              run={run}
              thread={fixtureThread()}
              timeline={fixtureTimeline({ run })}
            />
          ) : (
            <DebugRunSection
              run={run}
              thread={fixtureThread()}
              timeline={fixtureTimeline({ run })}
              index={2}
            />
          )
        ) : (
          <ForkRun run={run} thread={fixtureThread()} level={level} />
        )}
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
async function draft(text = "Explore another option") {
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: /^Create branch(?: from here)?$/ }),
  );
  await user.type(
    screen.getByRole("textbox", {
      name: "What would you like to continue in the new branch?",
    }),
    text,
  );
  return user;
}

it.each(["chat", "debug"] as const)(
  "forks the exact historical Run and opens its new Thread in %s",
  async (level) => {
    show(origin, level, true);
    const user = await draft();
    await user.click(
      screen.getByRole("button", { name: "Create branch and send" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe(
        `/workspace/ws_1/sessions/ses_1/threads/thr_fork/runs/run_fork?view=${level}null`,
      ),
    );
    expect(posts).toHaveLength(1);
    expect(posts[0]).toMatchObject({
      path: "/api/v1/runs/run_2/fork",
      workspace: "ws_1",
      body: {
        kind: "message",
        delivery: "next_run",
        agent_id: "agt_1",
        agent_revision_id: "rev_1",
        options: origin.options,
        fresh_environments: false,
        payload: {
          content: [{ type: "text", text: "Explore another option" }],
        },
      },
    });
    expect(posts[0]!.key).toBeTruthy();
    expect(
      cache.getQueryData(conversationKeys("ws_1").run("run_fork")),
    ).toEqual(next);
    expect(
      cache.getQueryData(conversationKeys("ws_1").threads("ses_1")),
    ).toContainEqual(fixtureForkThread);
  },
);

it("keeps the message and command key after an uncertain response, including closing and reopening", async () => {
  rejection = true;
  show();
  const user = await draft();
  await user.click(
    screen.getByRole("button", { name: "Create branch and send" }),
  );
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await user.click(
    screen.getByRole("button", { name: "Create branch from here" }),
  );
  expect(screen.getByRole("textbox")).toHaveProperty(
    "value",
    "Explore another option",
  );
  await user.click(
    screen.getByRole("button", { name: "Create branch and send" }),
  );
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1]).toEqual(posts[0]);
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "Environment options" }));
  await user.click(
    screen.getByRole("switch", { name: "Use a new environment" }),
  );
  rejection = false;
  await user.click(
    screen.getByRole("button", { name: "Create branch and send" }),
  );
  await waitFor(() => expect(posts).toHaveLength(3));
  expect(posts[2]!.body.fresh_environments).toBe(true);
  expect(posts[2]!.key).not.toBe(posts[1]!.key);
});

it("forks a waiting Run without answering the original approvals", async () => {
  show(
    fixtureRun({
      status: "waiting",
      pending: {
        approvals: [
          {
            tool_call_id: "call_1",
            tool_name: "shell",
            arguments: {},
            presentation: null,
          },
        ],
        calls: [],
      },
    }),
  );
  const user = await draft();
  expect(
    screen.getByText(
      "Pending approvals will be denied and unanswered calls will fail in the new branch. The original conversation keeps waiting.",
    ),
  ).toBeTruthy();
  await user.click(
    screen.getByRole("button", { name: "Create branch and send" }),
  );
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]!.path).toBe("/api/v1/runs/run_2/fork");
  expect(posts[0]!.body).not.toHaveProperty("approvals");
});

it.each([
  { status: "accepted", sealed_at: null, allowed: true },
  { status: "running", sealed_at: null, allowed: true },
  { status: "completed", sealed_at: null, allowed: true },
  { status: "completed", sealed_at: origin.sealed_at, allowed: false },
] as const)(
  "prevents fork for $status / sealed=$sealed_at / allowed=$allowed",
  async (test) => {
    allowed = test.allowed;
    show(fixtureRun({ status: test.status, sealed_at: test.sealed_at }));
    const button = screen.getByRole("button", {
      name: "Create branch from here",
    });
    expect(button).toHaveProperty("disabled", true);
    await userEvent.click(button);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(posts).toEqual([]);
  },
);

it.each(["failed", "cancelled"] as const)(
  "explains the saved checkpoint for a %s Run and requires a message",
  async (status) => {
    show(fixtureRun({ status }));
    await userEvent.click(
      screen.getByRole("button", { name: "Create branch from here" }),
    );
    expect(
      screen.getByText(
        "This branch continues from the last saved checkpoint. Unsaved work may not be included.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Create branch and send" }),
    ).toHaveProperty("disabled", true);
    expect(posts).toEqual([]);
  },
);

it("opens the created Thread even when acceptance has not started its Run", async () => {
  queued = true;
  show();
  const user = await draft();
  await user.click(
    screen.getByRole("button", { name: "Create branch and send" }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("location").textContent).toBe(
      "/workspace/ws_1/sessions/ses_1/threads/thr_fork?view=chatnull",
    ),
  );
  expect(
    cache.getQueryData(conversationKeys("ws_1").thread("thr_fork")),
  ).toEqual(fixtureForkThread);
});
