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
                  message: "Conversation changed. Try again after refreshing.",
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
  starred = false,
  worker = false,
  waiting = false,
  failed = false,
  activeWorkerCount = 0,
} = {}) {
  const row = {
    pending_decision: waiting ? { decision_id: "decision-one" } : null,
    latest_operation: failed ? { status: "failed" } : null,
    thread: {
      thread_id: "thread-one",
      title: "Example",
      role: coordinator ? "coordinator" : worker ? "worker" : "ordinary",
      archived,
      starred,
      metadata_version: 3,
      configuration: { project_id: "project-one" },
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
    await screen.findByRole("menuitem", { name: "Archive conversation" }),
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
it("does not change the current page when archiving another conversation", async () => {
  mount({ selected: false });
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Archive conversation" }),
  );
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(screen.getByLabelText("Location").textContent).toBe("/settings");
});
it("restores an archived conversation directly from the same menu", async () => {
  mount({ archived: true });
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Restore conversation" }),
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
    name: "Archive conversation",
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
    await screen.findByRole("menuitem", { name: "Archive conversation" }),
  );
  await screen.findByText("Conversation changed. Try again after refreshing.");
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
      await screen.findByRole("menuitem", { name: "Archive conversation" }),
    );
    if (failure) {
      await screen.findByText(
        "Conversation changed. Try again after refreshing.",
      );
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
  "retains an explicit $label state in a compact row",
  ({ waiting, failed, label }) => {
    mount({ waiting, failed });
    const state = screen.getByText(label);
    expect(state.className).toContain("attentionState");
    expect(
      screen.getByRole("link", { name: new RegExp(`Example.*${label}`) }),
    ).toBeTruthy();
  },
);

it.each([false, true])(
  "toggles a shared star while running without navigation (starred=%s)",
  async (starred) => {
    mount({ starred, running: true });
    expect(
      screen.queryByRole("button", { name: /Star conversation:/ }),
    ).toBeNull();
    expect(
      !!screen.queryByRole("img", { name: "Starred conversation: Example" }),
    ).toBe(starred);
    expect(screen.getByText("Running")).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: "Actions for Example" }),
    );
    await userEvent.click(
      await screen.findByRole("menuitem", {
        name: `${starred ? "Unstar" : "Star"} conversation`,
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
    await screen.findByRole("menuitem", { name: "Star conversation" }),
  );
  await screen.findByText("Conversation changed. Try again after refreshing.");
  expect(requests).toHaveLength(1);
  expect(
    screen.queryByRole("img", { name: "Starred conversation: Example" }),
  ).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  expect(
    await screen.findByRole("menuitem", { name: "Star conversation" }),
  ).toBeTruthy();
});

it("does not offer stars for coordinator workers", async () => {
  mount({ worker: true });
  expect(
    screen.queryByRole("button", { name: /Star conversation/ }),
  ).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  expect(
    screen.queryByRole("menuitem", { name: "Star conversation" }),
  ).toBeNull();
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

it("keeps Coordinator identity through compact activity and explicit attention states", () => {
  const { row, rerender } = mount({ coordinator: true, activeWorkerCount: 2 });
  const link = screen.getByRole("link", { name: /Example/ });
  const identity = link.querySelector("svg");
  const summary = within(link).getByText("2 workers active").closest("small")!;
  expect(summary.querySelector("svg")).toBeNull();

  row.thread.root_activity.state = "preparing";
  rerender(row, 2);
  expect(summary.textContent).toBe("Preparing · 2 workers active");
  const spinner = summary.querySelector("svg");
  expect(spinner).not.toBeNull();

  row.thread.root_activity.state = "running";
  rerender(row, 1);
  expect(summary.textContent).toBe("Running · 1 worker active");
  expect(summary.querySelector("svg")).toBe(spinner);

  row.pending_decision = { kind: "question", count: 1 };
  rerender(row, 1);
  expect(
    within(link).getByText("Needs your answer · 1 worker active"),
  ).toBeTruthy();
  expect(link.querySelectorAll("svg")).toHaveLength(1);

  row.pending_decision = null;
  row.thread.root_activity.state = "inactive";
  row.latest_operation = { status: "failed" } as NonNullable<
    typeof row.latest_operation
  >;
  rerender(row, 0);
  expect(within(link).getByText("Failed")).toBeTruthy();

  row.latest_operation = null;
  rerender(row, 0);
  expect(link.querySelector("small")).toBeNull();
  expect(link.querySelector("svg")).toBe(identity);
  expect(screen.getByRole("link", { name: /Example/ })).toBe(link);
});
