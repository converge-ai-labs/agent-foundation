// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
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
  return <output aria-label="Location">{location.pathname}</output>;
}
function mount({ archived = false, running = false, selected = true } = {}) {
  const row = {
    thread: {
      thread_id: "thread-one",
      title: "Example",
      archived,
      metadata_version: 3,
      configuration: { project_id: "project-one" },
      root_activity: { state: running ? "running" : "inactive" },
    },
  } as Schema<"ThreadActivityView">;
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("test", () => {})}>
        <MemoryRouter
          initialEntries={[selected ? "/threads/thread-one" : "/settings"]}
        >
          <NewConversationDrafts value={newDrafts}>
            <ThreadRow row={row} presence={null} />
          </NewConversationDrafts>
          <Location />
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
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
