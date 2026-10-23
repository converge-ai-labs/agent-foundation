import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../../service-client";
import type { Schema } from "../../../shared/api";
import type { Resubmission } from "../resubmit";
import { fixtureRun, fixtureThread } from "./fixture";
import type { ComponentProps } from "react";
import { RunDock, ThreadDock } from "./run-dock";
import { useRun } from "../queries";

let client: Client;
let denied: string[] = [];
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/design",
    can: (action: string) => !denied.includes(action),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
beforeEach(() => {
  denied = [];
});
afterEach(() => {
  cleanup();
  client.close();
});
const run = fixtureRun({
  id: "run",
  session_id: "session",
  thread_id: "thread",
  agent_id: "agent",
});
const entry = (id: string, text: string, fields = {}) => ({
  id,
  thread_id: "thread",
  kind: "message",
  delivery: "next_run",
  status: "pending",
  position: 1,
  payload: { content: [{ type: "text", text }] },
  agent_id: "agent",
  agent_revision_id: null,
  assigned_run_id: null,
  child_run_id: null,
  origin_run_id: null,
  options: {},
  principal_id: "usr_1",
  failure: null,
  incorporated_checkpoint_seq: null,
  created_at: "2026-09-18T00:00:00Z",
  finished_at: null,
  ...fields,
});
function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

it("replaces a racing user takeover panel with the Agent assistance card without stealing control", async () => {
  const mount = {
    name: "workspace",
    environment_id: "env",
    working_directory: null,
    permission_ceiling: { operations: ["environment.computer.observe"] },
  } satisfies Schema["RunView"]["environment_mounts"][number];
  let current = fixtureRun({
    ...run,
    status: "running",
    environment_mounts: [mount],
  });
  const assistance = {
    id: "agent-help",
    run_id: "run",
    tool_call_id: "help",
    cause: "agent",
    environment_id: "env",
    mount: "workspace",
    reason: "Please select the document",
    state: "awaiting_user",
    epoch: 0,
    controller_id: null,
    deadline: "2099-01-01",
    lease_expires_at: null,
    failure: null,
    outcome: null,
  };
  const posts: string[] = [];
  let waitingRun: typeof current | null = null;
  let refreshedBeforeSeal = false;
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const req = new Request(input, init),
        path = new URL(req.url).pathname;
      if (req.method === "POST") {
        posts.push(path);
        waitingRun = fixtureRun({
          ...current,
          status: "waiting",
          wait_reason: "call",
          pending: {
            desktop_control_id: null,
            approvals: [],
            calls: [
              {
                tool_call_id: "help",
                tool_name: "request_desktop_assistance",
                arguments: { mount: "workspace", reason: assistance.reason },
                presentation: null,
              },
            ],
          },
        });
        return Response.json(
          {
            error: {
              code: "conflict",
              message: "Run changed",
              details: { reason: "desktop_agent_assistance_pending" },
              request_id: null,
            },
          },
          { status: 409 },
        );
      }
      if (path.endsWith("/desktop-control"))
        return Response.json({
          ...assistance,
          id: "user-control",
          tool_call_id: null,
          cause: "user",
          state: "viewing",
        });
      if (path.endsWith("/desktop-assistance/help"))
        return Response.json(assistance);
      if (path.endsWith("/inbox"))
        return Response.json({ items: [], next_cursor: null });
      if (waitingRun && path.endsWith("/runs/run")) {
        if (refreshedBeforeSeal) current = waitingRun;
        refreshedBeforeSeal = true;
      }
      return Response.json(current);
    },
  });
  function LiveDock() {
    const live = useRun("run");
    if (!live.data) return null;
    return (
      <TestDock
        run={live.data}
        thread={fixtureThread({
          id: "thread",
          session_id: "session",
          current_run_id: live.data.status === "running" ? "run" : null,
          last_run_id: "run",
        })}
      />
    );
  }
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <LiveDock />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Get control" }));
  await screen.findByText(
    "The agent has requested desktop assistance. Switching to its assistance card…",
  );
  await screen.findByRole(
    "button",
    { name: "Take control" },
    { timeout: 6000 },
  );
  expect(screen.queryByRole("button", { name: "Get control" })).toBeNull();
  expect(screen.queryByText("Desktop control")).toBeNull();
  expect(screen.queryByText("Run changed")).toBeNull();
  expect(screen.getAllByRole("button", { name: "Take control" })).toHaveLength(
    1,
  );
  expect(posts).toEqual([
    "/api/v1/desktop-assistance/user-control/request-control",
  ]);
});
function mount({
  status = "completed",
  historical = false,
  origin = "new",
  reject = false,
  loseAcknowledgement = false,
  resubmit,
  question = false,
  search = "",
}: {
  status?: Schema["RunStatus"];
  historical?: boolean;
  origin?: Schema["ThreadView"]["origin"];
  reject?: boolean;
  loseAcknowledgement?: boolean;
  resubmit?: Resubmission;
  question?: boolean;
  search?: string;
} = {}) {
  const posts: { path: string; body: unknown; key: string | null }[] = [];
  const reads: string[] = [];
  const active = status === "running" || status === "accepted";
  const current = fixtureRun({
    ...run,
    status,
    ...(status === "waiting"
      ? {
          wait_reason: question ? "call" : "approval",
          pending: {
            approvals: question
              ? []
              : [
                  {
                    tool_call_id: "call",
                    tool_name: "shell",
                    arguments: { command: "ls" },
                    presentation: null,
                  },
                ],
            calls: question
              ? [
                  {
                    tool_call_id: "call",
                    tool_name: "ask_user_question",
                    arguments: { command: "ls" },
                    presentation: null,
                  },
                ]
              : [],
          },
        }
      : {}),
  });
  const thread = fixtureThread({
    id: "thread",
    session_id: "session",
    origin,
    current_run_id: active ? "run" : null,
    last_run_id: historical ? "later" : "run",
  });
  let steered = false;
  // A resumed Run is current, so a later message joins it instead.
  let resumed = false;
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const req = new Request(input, init),
        path = new URL(req.url).pathname;
      if (req.method === "POST") {
        const text = await req.text();
        posts.push({
          path,
          body: text ? JSON.parse(text) : null,
          key: req.headers.get("Idempotency-Key"),
        });
        if (loseAcknowledgement) throw new TypeError("Lost acknowledgement");
        if (reject)
          return Response.json(
            {
              error: {
                code: "thread_conflict",
                message: "Run changed. Try again.",
              },
            },
            { status: 409 },
          );
        if (path.endsWith("/resume")) {
          resumed = true;
          return Response.json(
            fixtureRun({ ...run, id: "resumed", status: "accepted" }),
            { status: 201 },
          );
        }
        if (path.endsWith("/inbox")) {
          steered = active;
          return Response.json(
            {
              thread,
              entry: entry("inb_new", "Sent", { delivery: "steer" }),
              run:
                active || resumed
                  ? null
                  : fixtureRun({ ...run, id: "next", status: "accepted" }),
            },
            { status: 201 },
          );
        }
        return Response.json(current);
      }
      reads.push(path);
      if (path.endsWith("/inbox"))
        return Response.json({
          items: [entry("inb_queued", "Queued test")],
          next_cursor: null,
        });
      // The Thread changed: its guidance entry reports where it went.
      if (path.endsWith("/inbox/inb_new"))
        return Response.json(
          entry("inb_new", "Sent", {
            delivery: "steer",
            ...(steered ? { status: "consumed", assigned_run_id: "run" } : {}),
          }),
        );
      return Response.json(current);
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[`/${search}`]}>
        <TestDock run={current} thread={thread} resubmit={resubmit} />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return Object.assign(posts, { reads });
}
it("continues the completed head with a next-run message and navigates to its Run", async () => {
  const posts = mount();
  await waitFor(() =>
    expect(
      screen.getByRole("textbox", { name: "Message" }).closest("fieldset")
        ?.disabled,
    ).toBe(false),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Next test" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(screen.getByTestId("location").textContent).toContain("/runs/next"),
  );
  expect(posts).toHaveLength(1);
  expect(posts[0]?.path).toBe("/api/v1/threads/thread/inbox");
  expect(posts[0]?.body).toEqual({
    kind: "message",
    delivery: "next_run",
    payload: { content: [{ type: "text", text: "Next test" }] },
    agent_id: "agent",
  });
});
it("steers the active Run and reports when it applied the guidance", async () => {
  const posts = mount({ status: "running" });
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Use the smaller scope" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add guidance" }));
  await screen.findByText("Guidance applied.");
  expect(posts.map((p) => p.path)).toEqual(["/api/v1/threads/thread/inbox"]);
  // The dock reads its own entry, never the whole inbox history.
  expect(posts.reads).toContain("/api/v1/threads/thread/inbox/inb_new");
  expect(posts[0]?.body).toMatchObject({
    delivery: "steer",
    agent_id: "agent",
  });
});
it("retains rejected guidance and never falls through to a new Run", async () => {
  const posts = mount({ status: "running", reject: true });
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Keep this input" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add guidance" }));
  await screen.findByRole("alert");
  expect(
    screen
      .getByRole("textbox", { name: "Message" })
      .getAttribute("placeholder"),
  ).toBe("Add guidance while it works");
  expect(screen.getByRole("textbox", { name: "Message" })).toHaveProperty(
    "value",
    "Keep this input",
  );
  fireEvent.click(screen.getByRole("button", { name: "Add guidance" }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1]).toEqual(posts[0]);
});
it.each(["completed", "failed", "waiting", "running"] as const)(
  "keeps a %s child Thread inspectable without mutation controls",
  async (status) => {
    mount({ origin: "child", status });
    await screen.findByText(
      "This thread is driven by the run that delegated to it. Continue the work from the parent thread.",
    );
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Continue without feedback" }),
    ).toBeNull();
    if (status === "waiting")
      await screen.findByText("Waiting for the application");
    if (status === "running")
      expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
    fireEvent.click(
      await screen.findByRole("button", { name: /Queued messages/ }),
    );
    await screen.findByText("Queued test");
    for (const name of [
      "Queued message actions",
      "Move message up",
      "Move message down",
    ])
      expect(screen.queryByRole("button", { name })).toBeNull();
  },
);
it("shows feedback instead of a general composer while waiting", async () => {
  mount({ status: "waiting" });
  await screen.findByText("Waiting for your response");
  expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Continue without feedback" }),
  ).toBeTruthy();
});
it("does not offer input or retry on historical Runs", () => {
  mount({ status: "failed", historical: true });
  expect(
    screen.getByRole("link", { name: "Return to latest messages" }),
  ).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
});
it("keeps guidance and stopping from a reader without run permission", () => {
  denied = ["run"];
  mount({ status: "running" });
  expect(
    screen.getByRole("textbox", { name: "Message" }).closest("fieldset")
      ?.disabled,
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
});

it("offers Stop in place of Send while a run is active and the draft is empty", async () => {
  const posts = mount({ status: "running" });
  expect(screen.queryByRole("button", { name: "Add guidance" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    path: "/api/v1/runs/run/interrupt",
    body: null,
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Keep going, but smaller" },
  });
  expect(screen.getByRole("button", { name: "Add guidance" })).toBeTruthy();
});

it("leaves retrying a stopped run to the run itself", async () => {
  cleanup();
  mount({ status: "failed" });
  expect(
    await screen.findByRole("button", { name: "Send message" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
});

it("prefills a stopped Run's message and resubmits it with the options it ran with", async () => {
  const posts = mount({
    status: "failed",
    resubmit: {
      payload: {
        content: [
          { type: "text", text: "Run the checks" },
          { type: "url", url: "https://example.com/report" },
        ],
      },
      agent_revision_id: "rev_1",
      options: { max_usage: { requests: 3 } },
    },
  });
  const message = await screen.findByRole("textbox", { name: "Message" });
  expect(message).toHaveProperty("value", "Run the checks");
  expect(screen.getByText("https://example.com/report")).toBeTruthy();
  await waitFor(() =>
    expect(message.closest("fieldset")?.disabled).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(screen.getByTestId("location").textContent).toContain("/runs/next"),
  );
  expect(posts[0]?.body).toEqual({
    kind: "message",
    delivery: "next_run",
    payload: {
      content: [
        { type: "text", text: "Run the checks" },
        { type: "url", url: "https://example.com/report" },
      ],
    },
    agent_id: "agent",
    agent_revision_id: "rev_1",
    options: { max_usage: { requests: 3 } },
  });
});

it.each([false, true])(
  "resumes a waiting Run with default answers before sending the new message (question: %s)",
  async (question) => {
    const posts = mount({ status: "waiting", question });
    fireEvent.click(
      await screen.findByRole("button", { name: "Continue without feedback" }),
    );
    fireEvent.click(await screen.findByRole("switch"));
    fireEvent.change(await screen.findByRole("textbox", { name: "Message" }), {
      target: { value: "Try another way" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Resolve and continue" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain(
        "/runs/resumed",
      ),
    );
    expect(posts.map(({ path, body }) => [path, body])).toEqual([
      [
        "/api/v1/runs/run/resume",
        question
          ? {
              approvals: {},
              calls: {
                call: { status: "failed", message: "No response was given" },
              },
            }
          : {
              approvals: {
                call: { action: "deny", reason: "No decision was given" },
              },
              calls: {},
            },
      ],
      [
        "/api/v1/threads/thread/inbox",
        {
          kind: "message",
          delivery: "steer",
          payload: { content: [{ type: "text", text: "Try another way" }] },
          agent_id: "agent",
        },
      ],
    ]);
    expect(posts[0]?.key).toBe(`${posts[1]?.key}:resume`);
  },
);

it.each(["chat", "debug"])(
  "keeps %s selected when returning to the latest messages",
  async (view) => {
    mount({ historical: true, search: `?view=${view}` });
    expect(
      screen
        .getByRole("link", { name: "Return to latest messages" })
        .getAttribute("href"),
    ).toBe(
      `/workspace/design/sessions/session/threads/thread/runs/later?view=${view}&follow=latest`,
    );
  },
);

it("keeps retry identity until the message or execution policy changes", async () => {
  const posts = mount({ loseAcknowledgement: true });
  const user = userEvent.setup();
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Retry this message" },
  });
  await user.click(screen.getByRole("button", { name: "Send message" }));
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[0].key).toBeTruthy();
  expect(posts[1]).toEqual(posts[0]);

  await user.click(screen.getByRole("button", { name: "Execution policy" }));
  fireEvent.change(
    screen.getByRole("spinbutton", { name: "Foreground wait (seconds)" }),
    {
      target: { value: "2" },
    },
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await user.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(posts).toHaveLength(3));
  expect(posts[2].key).not.toBe(posts[1].key);
  expect(posts[2].body).toMatchObject({
    options: { execution: { tools: { foreground_wait_seconds: 2 } } },
  });
  await user.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(posts).toHaveLength(4));
  expect(posts[3]).toEqual(posts[2]);
});

it("sends execution overrides once with next_run and returns the next message to inheritance", async () => {
  const posts = mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Execution policy" }));
  screen.getByRole("combobox", { name: "Tool execution" }).focus();
  await user.keyboard("[ArrowDown]");
  await user.click(
    await screen.findByRole("option", { name: "Inline — wait for tools" }),
  );
  await user.click(screen.getByRole("combobox", { name: "Steering" }));
  await user.click(
    await screen.findByRole("option", { name: "At the next boundary" }),
  );
  await user.click(screen.getByRole("button", { name: "Apply" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "One run" },
  });
  await user.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toMatchObject({
    delivery: "next_run",
    options: { execution: { tools: { mode: "inline" }, steering: "boundary" } },
  });
  await user.click(screen.getByRole("button", { name: "Execution policy" }));
  expect(
    screen.getByRole("combobox", { name: "Tool execution" }).textContent,
  ).toBe("Inherit");
  expect(screen.getByRole("combobox", { name: "Steering" }).textContent).toBe(
    "Inherit",
  );
});

function TestDock(props: ComponentProps<typeof RunDock>) {
  return (
    <ThreadDock thread={props.thread}>
      <RunDock {...props} />
    </ThreadDock>
  );
}
