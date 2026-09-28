import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../../service-client";
import { fixtureRun, fixtureThread } from "./fixture";
import { ThreadInbox } from "./inbox";

let client: Client;
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  client.close();
});

const entry = (id: string, text: string) => ({
  id,
  thread_id: "thr_1",
  kind: "message",
  delivery: "next_run",
  status: "pending",
  position: 1,
  payload: { content: [{ type: "text", text }] },
  agent_id: "agt_1",
  agent_revision_id: null,
  assigned_run_id: null,
  child_run_id: null,
  origin_run_id: null,
  options: {},
  principal_id: "usr_1",
  failure: null,
  incorporated_checkpoint_seq: null,
  created_at: "2026-09-01T00:00:00Z",
  finished_at: null,
});

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function mount(
  requests: Request[],
  respond: (request: Request) => Response,
  canRunNext = false,
) {
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf-fixture" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      return respond(request);
    },
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <MemoryRouter>
        <ThreadInbox thread={fixtureThread()} canRunNext={canRunNext} />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("withdraws a pending message against the Thread it was read with", async () => {
  const requests: Request[] = [];
  let withdrawn = false;
  mount(requests, (request) => {
    if (request.method === "DELETE") {
      withdrawn = true;
      return Response.json({
        thread: fixtureThread({ version: 5 }),
        entry: { ...entry("inb_1", "Queued input"), status: "withdrawn" },
        run: null,
      });
    }
    return Response.json({
      items: withdrawn ? [] : [entry("inb_1", "Queued input")],
      next_cursor: null,
    });
  });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Queued messages/ }));
  expect(await screen.findByText("Queued input")).toBeTruthy();
  const list = requests.find((request) => request.method === "GET")!;
  expect(new URL(list.url).searchParams.getAll("status")).toEqual(["pending"]);
  await user.click(
    await screen.findByRole("button", { name: "Queued message actions" }),
  );
  await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
  await user.click(
    await screen.findByRole("button", { name: "Delete queued message" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    await screen.findByText("No messages in this inbox state."),
  ).toBeTruthy();
  const removal = requests.find((request) => request.method === "DELETE")!;
  expect(new URL(removal.url).pathname).toBe(
    "/api/v1/threads/thr_1/inbox/inb_1",
  );
  expect(removal.headers.get("If-Match")).toBe('"thr_1:4"');
});

it("reorders pending messages as one order against the Thread", async () => {
  const requests: Request[] = [];
  mount(requests, (request) =>
    request.method === "PUT"
      ? Response.json(fixtureThread({ version: 5 }))
      : Response.json({
          items: [entry("inb_1", "First"), entry("inb_2", "Second")],
          next_cursor: null,
        }),
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Queued messages/ }));
  await screen.findByText("Second");
  await user.click(
    screen.getAllByRole("button", { name: "Move message up" })[1]!,
  );
  await waitFor(() =>
    expect(requests.some((request) => request.method === "PUT")).toBe(true),
  );
  const order = requests.find((request) => request.method === "PUT")!;
  expect(new URL(order.url).pathname).toBe("/api/v1/threads/thr_1/inbox/order");
  expect(order.headers.get("If-Match")).toBe('"thr_1:4"');
  expect(await order.json()).toEqual({ entry_ids: ["inb_2", "inb_1"] });
});

it("runs the next message of a paused Thread by withdrawing and resubmitting it", async () => {
  const requests: Request[] = [];
  const queued = {
    ...entry("inb_1", "Try again"),
    agent_revision_id: "rev_1",
    options: { labels: { purpose: "retry" }, max_usage: { requests: 2 } },
  };
  mount(
    requests,
    (request) => {
      if (request.method === "DELETE")
        return Response.json({
          thread: fixtureThread({ version: 5 }),
          entry: { ...queued, status: "withdrawn" },
          run: null,
        });
      if (request.method === "POST")
        return Response.json(
          {
            thread: fixtureThread({ version: 6 }),
            entry: { ...queued, id: "inb_2", status: "assigned" },
            run: fixtureRun({ id: "run_3", status: "accepted" }),
          },
          { status: 201 },
        );
      return Response.json({
        items: [entry("inb_0", "Child result"), queued].map((item, index) =>
          index === 0
            ? { ...item, kind: "child_result", agent_id: null }
            : item,
        ),
        next_cursor: null,
      });
    },
    true,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Queued messages/ }));
  await screen.findByText("Try again");
  expect(
    screen.queryByText(
      "Queued messages can run after the current run finishes and pending feedback is resolved.",
    ),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Run next message" }));
  await waitFor(() =>
    expect(screen.getByTestId("location").textContent).toBe(
      "/workspace/sessions/ses_1/threads/thr_1/runs/run_3",
    ),
  );
  const [withdrawal, submission] = requests.filter(
    (request) => request.method !== "GET",
  );
  // A child result is never resubmitted: the first message is.
  expect(withdrawal?.method).toBe("DELETE");
  expect(new URL(withdrawal!.url).pathname).toBe(
    "/api/v1/threads/thr_1/inbox/inb_1",
  );
  expect(withdrawal?.headers.get("If-Match")).toBe('"thr_1:4"');
  expect(new URL(submission!.url).pathname).toBe("/api/v1/threads/thr_1/inbox");
  expect(submission?.headers.get("Idempotency-Key")).toBe("resubmit:inb_1");
  expect(await submission!.json()).toEqual({
    kind: "message",
    payload: { content: [{ type: "text", text: "Try again" }] },
    agent_id: "agt_1",
    agent_revision_id: "rev_1",
    delivery: "next_run",
    options: { labels: { purpose: "retry" }, max_usage: { requests: 2 } },
  });
});

it("says when queued messages run while the Thread advances on its own", async () => {
  const requests: Request[] = [];
  mount(requests, () =>
    Response.json({
      items: [entry("inb_1", "Later")],
      next_cursor: null,
    }),
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Queued messages/ }));
  await screen.findByText("Later");
  expect(
    screen.getByText(
      "Queued messages can run after the current run finishes and pending feedback is resolved.",
    ),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Run next message" })
      .hasAttribute("disabled"),
  ).toBe(true);
});
