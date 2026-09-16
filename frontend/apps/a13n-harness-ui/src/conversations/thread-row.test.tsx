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
import { TransportContext } from "../transport/context";
import { createTransport, type Schema } from "../transport/client";
let requests: Request[];
let fail: boolean;
let queries: QueryClient;
beforeEach(() => {
  requests = [];
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
          <ThreadRow row={row} presence={null} />
          <Location />
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}
it("archives directly from the menu with its observed version and opens a blank local page", async () => {
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
    expect(screen.getByLabelText("Location").textContent).toMatch(
      /^\/new\/thread_[a-f0-9]{32}$/,
    ),
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

it("does not expose the disabled comments entry in conversation actions", async () => {
  mount();
  await userEvent.click(
    screen.getByRole("button", { name: "Actions for Example" }),
  );
  expect(
    await screen.findByRole("menuitem", { name: "Share conversation" }),
  ).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Comments" })).toBeNull();
});
