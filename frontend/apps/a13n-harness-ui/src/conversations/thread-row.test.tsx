// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { ThreadRow } from "./thread-row";
import { NewConversationDrafts } from "./new-conversation";
import { NewDraftStore } from "./new-draft";
import { values } from "./draft";
import { TransportContext } from "../transport/context";
import { createTransport, type Schema } from "../transport/client";
let requests: Request[];
let fail: boolean;
let queries: QueryClient;
let newDrafts: NewDraftStore;
beforeEach(() => {
  requests = [];
  localStorage.clear();
  newDrafts = new NewDraftStore();
  fail = false;
  queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request.clone());
      return new Response(
        JSON.stringify(
          fail
            ? {
                error: {
                  message: "Chat changed. Try again after refreshing.",
                },
              }
            : {},
        ),
        {
          status: fail ? 409 : 200,
          headers: { "Content-Type": "application/json" },
        },
      );
    }),
  );
});
afterEach(() => {
  cleanup();
  queries.clear();
  newDrafts.dispose();
  vi.unstubAllGlobals();
});
function Location() {
  const location = useLocation();
  return (
    <>
      <output aria-label="Location">{location.pathname}</output>
      <output aria-label="Search">{location.search}</output>
    </>
  );
}
function mount({
  archived = false,
  running = false,
  selected = true,
  coordinator = false,
  live = false,
  starred = false,
  worker = false,
  waiting = false,
  failed = false,
  activeWorkerCount = 0,
} = {}) {
  const row = {
    pending_decision: waiting ? { decision_id: "decision-one" } : null,
    thread: {
      last_execution: failed
        ? {
            execution_id: "receipt-failed",
            status: "failed",
            submitted_at: "2026-01-01T00:00:00Z",
          }
        : null,
      thread_id: "thread-one",
      title: "Example",
      role: coordinator ? "coordinator" : worker ? "worker" : "ordinary",
      archived,
      starred,
      metadata_version: 3,
      configuration: {
        project_id: live ? null : "project-one",
        agent_source: {
          kind: live ? "live" : "configured",
          id: live ? "live" : "assistant",
        },
      },
      root_activity: { state: running ? "running" : "inactive" },
    },
  } as Schema<"ThreadActivityView">;
  const view = (current: typeof row, workers: number) => (
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("test", () => {})}>
        <MemoryRouter
          initialEntries={[selected ? "/threads/thread-one" : "/settings"]}
        >
          <NewConversationDrafts value={newDrafts}>
            <ThreadRow row={current} activeWorkerCount={workers} />
          </NewConversationDrafts>
          <Location />
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>
  );
  const rendered = render(view(row, activeWorkerCount));
  return {
    row,
    rerender: (current: typeof row, workers = activeWorkerCount) =>
      rendered.rerender(view(current, workers)),
  };
}
it("archives directly from the menu with its observed version and opens the New draft", async () => {
  mount();
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Archive chat" }),
  );
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].method).toBe("PATCH");
  expect(await requests[0].json()).toEqual({
    expected_version: 3,
    patch: { archived: true },
  });
  expect(screen.queryByRole("dialog")).toBeNull();
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe("/new"),
  );
});
it("keeps the persistent Live destination out of archive actions", async () => {
  mount({ live: true });
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await screen.findByRole("menu");
  expect(screen.queryByRole("menuitem", { name: "Archive chat" })).toBeNull();
  expect(requests).toHaveLength(0);
});
it("does not change the current page when archiving another chat", async () => {
  mount({ selected: false });
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Archive chat" }),
  );
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(screen.getByLabelText("Location").textContent).toBe("/settings");
});
it("restores an archived chat directly from the same menu", async () => {
  mount({ archived: true });
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Restore chat" }),
  );
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(await requests[0].json()).toEqual({
    expected_version: 3,
    patch: { archived: false },
  });
  expect(screen.getByLabelText("Location").textContent).toBe(
    "/threads/thread-one",
  );
});
it("does not archive an active operation or silently retry a metadata conflict", async () => {
  mount({ running: true });
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  const item = await screen.findByRole("menuitem", {
    name: "Archive chat",
  });
  expect(item.getAttribute("aria-disabled")).toBe("true");
  fireEvent.click(item);
  expect(requests).toHaveLength(0);
  cleanup();
  fail = true;
  mount();
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Archive chat" }),
  );
  await screen.findByText("Chat changed. Try again after refreshing.");
  expect(requests).toHaveLength(1);
  expect(screen.getByLabelText("Location").textContent).toBe(
    "/threads/thread-one",
  );
});

it.each([false, true])(
  "detaches the singleton only after successful archive (failure=%s)",
  async (failure) => {
    fail = failure;
    const draft = newDrafts.get(new Map());
    draft.threadId = "thread-one";
    draft.created = true;
    draft.attempted = true;
    draft.composer.doc.getText("text").insert(0, "Retained after rejection");
    mount();
    await userEvent.click(
      screen.getByRole("button", { name: "Actions for Example" }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", { name: "Archive chat" }),
    );
    if (failure) {
      await screen.findByText("Chat changed. Try again after refreshing.");
      expect(newDrafts.current).toBe(draft);
    } else {
      await waitFor(() =>
        expect(newDrafts.current!.threadId).not.toBe("thread-one"),
      );
      expect(values(newDrafts.current!.composer.doc).prompt).toBe(
        "Retained after rejection",
      );
      expect(newDrafts.current!.attempted).toBe(false);
    }
  },
);

it.each([
  { waiting: true, failed: false, label: "Needs your answer" },
  { waiting: false, failed: true, label: "Failed" },
])(
  "announces $label with a trailing status icon in a compact row",
  ({ waiting, failed, label }) => {
    mount({ waiting, failed });
    const state = screen.getByText(label);
    expect(state.className).toContain("srOnly");
    expect(state.parentElement?.querySelector("svg")).not.toBeNull();
    expect(
      screen.getByRole("link", { name: new RegExp(`Example.*${label}`) }),
    ).toBeTruthy();
  },
);

it.each([false, true])(
  "toggles a shared star while running without navigation (starred=%s)",
  async (starred) => {
    mount({ starred, running: true });
    expect(screen.queryByRole("button", { name: /Star chat:/ })).toBeNull();
    expect(!!screen.queryByRole("img", { name: "Starred chat: Example" })).toBe(
      starred,
    );
    expect(screen.getByText("Running")).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Actions for Example" }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", {
        name: `${starred ? "Unstar" : "Star"} chat`,
      }),
    );
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(await requests[0].json()).toEqual({
      expected_version: 3,
      patch: { starred: !starred },
    });
    expect(screen.getByLabelText("Location").textContent).toBe(
      "/threads/thread-one",
    );
  },
);

it("keeps failed star changes explicit without showing an optimistic star", async () => {
  fail = true;
  mount();
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Star chat" }),
  );
  await screen.findByText("Chat changed. Try again after refreshing.");
  expect(requests).toHaveLength(1);
  expect(
    screen.queryByRole("img", { name: "Starred chat: Example" }),
  ).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  expect(
    await screen.findByRole("menuitem", { name: "Star chat" }),
  ).toBeTruthy();
});

it("does not offer stars for coordinator workers", async () => {
  mount({ worker: true });
  expect(screen.queryByRole("button", { name: /Star chat/ })).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  expect(screen.queryByRole("menuitem", { name: "Star chat" })).toBeNull();
});

it("opens a worker draft from a running Coordinator without sending it a message", async () => {
  mount({ coordinator: true, running: true });
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "New worker" }),
  );
  expect(screen.getByLabelText("Location").textContent).toBe("/new");
  expect(screen.getByLabelText("Search").textContent).toBe(
    "?project=project-one&coordinator=thread-one",
  );
  expect(requests).toHaveLength(0);
});

it("keeps Coordinator identity and trailing activity through state changes", () => {
  const { row, rerender } = mount({ coordinator: true, activeWorkerCount: 2 });
  const link = screen.getByRole("link", { name: /Example.*Coordinator/ });
  const summary = within(link)
    .getByLabelText("2 workers active")
    .closest("small")!;
  expect(link.querySelector("svg")).toBeNull();

  row.thread.root_activity.state = "preparing";
  rerender(row, 2);
  expect(summary.textContent).toBe("2 active");
  const status = within(link).getByTitle("Preparing");
  const spinner = status.querySelector("svg");
  expect(spinner).not.toBeNull();

  row.thread.root_activity.state = "running";
  rerender(row, 1);
  expect(summary.textContent).toBe("1 active");
  expect(within(link).getByTitle("Running").querySelector("svg")).toBe(spinner);

  row.pending_decision = { kind: "question", count: 1 };
  rerender(row, 1);
  expect(within(link).getByTitle("Needs your answer")).toBe(status);
  expect(link.querySelectorAll("svg")).toHaveLength(1);

  row.pending_decision = null;
  row.thread.root_activity.state = "inactive";
  row.thread.last_execution = {
    execution_id: "receipt-failed",
    status: "failed",
    submitted_at: "2026-01-01T00:00:00Z",
  };
  rerender(row, 0);
  expect(within(link).getByTitle("Failed")).toBe(status);

  row.thread.last_execution = null;
  rerender(row, 0);
  expect(link.querySelector("small")).toBeNull();
  expect(link.querySelector("svg")).toBeNull();
  expect(screen.getByRole("link", { name: /Example.*Coordinator/ })).toBe(link);
});

it("finishes Star on confirmation without waiting for list refresh or invalidating history", async () => {
  const { row } = mount({ running: true });
  const detailKey = ["thread", "thread-one", "detail"];
  const historyKey = ["thread", "thread-one", "history", "saved-source"];
  queries.setQueryData(detailKey, { thread: row.thread });
  queries.setQueryData(historyKey, { pages: [{ messages: ["retained"] }] });
  // An unrelated Project's pending pagination must not keep Star busy.
  const invalidate = vi
    .spyOn(queries, "invalidateQueries")
    .mockReturnValue(new Promise(() => {}));
  let confirm!: () => void;
  const pending = new Promise<void>((resolve) => {
    confirm = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      await pending;
      return Response.json({
        ...row.thread,
        starred: true,
        metadata_version: 4,
        root_activity: { state: "inactive" },
      });
    }),
  );
  const actions = screen.getByRole("button", { name: "Actions for Example" });
  await userEvent.click(actions);
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Star chat" }),
  );
  await waitFor(() => expect(actions.getAttribute("aria-busy")).toBe("true"));
  expect(
    queries.getQueryData<Schema<"ThreadDetail">>(detailKey)?.thread.starred,
  ).toBe(false);
  confirm();
  await waitFor(() => expect(actions.getAttribute("aria-busy")).toBe("false"));
  const saved = queries.getQueryData<Schema<"ThreadDetail">>(detailKey)!.thread;
  expect(saved.starred).toBe(true);
  expect(saved.metadata_version).toBe(4);
  expect(saved.root_activity.state).toBe("running");
  await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(1));
  expect(invalidate.mock.calls[0][0]?.queryKey).toEqual(detailKey);
  expect(queries.getQueryState(historyKey)?.isInvalidated).toBe(false);
  expect(document.activeElement).toBe(actions);
  invalidate.mockRestore();
});
