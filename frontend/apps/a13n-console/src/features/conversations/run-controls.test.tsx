import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { RunControls } from "./run-controls";

let client: Client;
let denied: string[] = [];
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
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
const now = "2026-09-18T00:00:00Z";
const run: Schema["RunResource"] = {
  id: "run",
  version: 3,
  session_id: "session",
  thread_id: "thread",
  labels: {},
  parent_run_id: null,
  retry_of_run_id: null,
  lineage_kind: "root",
  trigger_type: "user_input",
  agent_id: "agent",
  agent_revision_id: "revision",
  effective_agent_config_digest: "digest",
  environment_id: null,

  status: "completed",
  wait_reason: null,
  input_kind: "input",
  input: null,
  input_text: "Test",
  output: null,
  output_text: "Done",
  failure: null,
  pending: null,
  created_at: now,
  updated_at: now,
  started_at: now,
  waiting_at: null,
  completed_at: now,
  sealed_at: now,
  sealed_state_digest_sha256: "digest",
};
const thread: Schema["ThreadResource"] = {
  id: "thread",
  session_id: "session",
  session_purpose: "debug",
  role: "root",
  version: 7,
  queue_version: 1,
  origin_kind: "new",
  origin_thread_id: null,
  origin_run_id: null,
  head_run_id: "run",
  current_run_id: "run",
  default_environment_id: null,
  labels: {},
  created_at: now,
  updated_at: now,
};
function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}
function mount({
  purpose = "debug",
  status = "completed",
  historical = false,
  role = "root",
  reject = false,
}: {
  purpose?: Schema["SessionPurpose"];
  status?: Schema["RunStatus"];
  historical?: boolean;
  role?: string;
  reject?: boolean;
} = {}) {
  const posts: { path: string; body: unknown; key: string | null }[] = [];
  const current = { ...run, status };
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const req = new Request(input, init),
        path = new URL(req.url).pathname;
      if (req.method === "POST") {
        posts.push({
          path,
          body: await req.json(),
          key: req.headers.get("Idempotency-Key"),
        });
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
        return Response.json(
          path.endsWith("/steer")
            ? {
                run_id: "run",
                thread_id: "thread",
                session_id: "session",
                steer_id: "steer",
                delivery_sequence: 1,
                accepted_at: now,
              }
            : { run_id: "next", thread_id: "thread", session_id: "session" },
          { status: 202 },
        );
      }
      if (path.endsWith("/pending-actions"))
        return Response.json({
          items: [
            {
              call_id: "call",
              kind: "approval",
              tool_name: "shell",
              provider_type: null,
              presentation: null,
            },
          ],
        });
      if (path.includes("/steers/"))
        return Response.json({ status: "consumed" });
      if (path.endsWith("/queued-submissions"))
        return Response.json({
          items: [
            {
              queued_submission_id: "queued",
              version: 1,
              state: "queued",
              created_at: now,
              submission: {
                input: {
                  schema_version: "2",
                  content: [{ type: "text", text: "Queued test" }],
                },
              },
            },
          ],
          next_cursor: null,
        });
      return Response.json(current);
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <RunControls
          run={current}
          thread={{
            ...thread,
            session_purpose: purpose,
            role,
            current_run_id: historical ? "later" : "run",
          }}
        />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return posts;
}
it("continues the completed head with version evidence and navigates to the accepted Run", async () => {
  const posts = mount();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Run next step" }).closest("fieldset")
        ?.disabled,
    ).toBe(false),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Next test" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Run next step" }));
  await waitFor(() =>
    expect(screen.getByTestId("location").textContent).toContain("/runs/next"),
  );
  expect(posts).toHaveLength(1);
  expect(posts[0]?.path).toBe("/api/v1/runs/run/continue");
  expect(posts[0]?.body).toMatchObject({
    expected_thread_version: 7,
    input: { content: [{ type: "text", text: "Next test" }] },
  });
});
it("steers the exact active Run and reports consumption", async () => {
  const posts = mount({ status: "running" });
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Use the smaller scope" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send guidance" }));
  await screen.findByText("Guidance applied to the run.");
  expect(posts.map((p) => p.path)).toEqual(["/api/v1/runs/run/steer"]);
});
it("retains rejected guidance and never falls through to a new Run", async () => {
  const posts = mount({ status: "running", reject: true });
  fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
    target: { value: "Keep this input" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send guidance" }));
  await screen.findByRole("alert");
  expect(
    screen
      .getByRole("textbox", { name: "Message" })
      .getAttribute("placeholder"),
  ).toBe("Add guidance to the current run…");
  expect(screen.getByRole("textbox", { name: "Message" })).toHaveProperty(
    "value",
    "Keep this input",
  );
  fireEvent.click(screen.getByRole("button", { name: "Send guidance" }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1]).toEqual(posts[0]);
});
it.each(["completed", "failed", "waiting", "running"] as const)(
  "keeps external %s execution inspectable without mutation controls",
  async (status) => {
    mount({ purpose: "execution", status });
    await screen.findByText(
      "This session is controlled by its originating application. Use Try agent to start a separate debug session.",
    );
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Continue without feedback" }),
    ).toBeNull();
    if (status === "waiting") await screen.findByText("Waiting for a response");
    if (status === "running")
      expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Thread queue/ }));
    await screen.findByText("Queued test");
    for (const name of [
      "Delete",
      "Run next message",
      "Move message up",
      "Move message down",
    ])
      expect(screen.queryByRole("button", { name })).toBeNull();
  },
);
it("shows feedback instead of a general composer while waiting in a debug Session", async () => {
  mount({ status: "waiting" });
  await screen.findByText("Your response is needed");
  expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Continue without feedback" }),
  ).toBeTruthy();
});
it("does not offer input or retry on historical Runs", () => {
  mount({ status: "failed", historical: true });
  expect(screen.getByRole("link", { name: "Open current run" })).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
});
it("does not give independently interactive controls to child Threads", () => {
  mount({ role: "child" });
  expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
});
it("keeps steer disabled without its permission", () => {
  denied = ["run.steer"];
  mount({ status: "running" });
  expect(
    screen.getByRole("button", { name: "Send guidance" }).closest("fieldset")
      ?.disabled,
  ).toBe(true);
});
