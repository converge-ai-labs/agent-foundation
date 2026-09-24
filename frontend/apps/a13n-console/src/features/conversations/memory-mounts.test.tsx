import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { MemoryMountRows } from "../memories/mounts";
import { ThreadMemoryMounts } from "./memory-mounts";
import { fixtureRun, fixtureThread } from "./transcript/fixture";

const mocks = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  DELETE: vi.fn(),
  canRun: true,
  thread: {} as Schema["ThreadView"],
  mounts: [] as Schema["MemoryMount"][],
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http: mocks }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/design",
    can: () => mocks.canRun,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string>) =>
      Object.entries(values ?? {}).reduce(
        (text, [name, value]) => text.replaceAll(`{{${name}}}`, value),
        key,
      ),
  }),
}));
const run = fixtureRun({ status: "completed" });

beforeEach(() => {
  mocks.canRun = true;
  mocks.thread = fixtureThread();
  mocks.mounts = [{ name: "handbook", memory_id: "mem_book", access: "read" }];
  mocks.POST.mockReset().mockResolvedValue({ data: {} });
  mocks.DELETE.mockReset().mockResolvedValue({});
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/threads/{thread_id}")
      ? mocks.thread
      : path.endsWith("/threads/{thread_id}/memories")
        ? { items: mocks.mounts }
        : {
            items: [
              { id: "mem_book", key: "handbook", name: "Handbook" },
              { id: "mem_prefs", key: "user-prefs", name: "Preferences" },
            ],
            next_cursor: null,
          },
  }));
});

function show(children = <ThreadMemoryMounts run={run} />) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
  return cache;
}

it("adds a memory to the Thread it was read with", async () => {
  const cache = show();
  const user = userEvent.setup();
  expect(await screen.findByRole("link", { name: "Handbook" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Add memory" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("combobox", { name: "Memory" }));
  // A memory the thread already mounts is not offered.
  expect(
    screen.queryByRole("option", { name: "Handbook (handbook)" }),
  ).toBeNull();
  await user.click(
    await screen.findByRole("option", { name: "Preferences (user-prefs)" }),
  );
  const name = within(dialog).getByRole("textbox", { name: "Mount name" });
  await user.clear(name);
  await user.type(name, "handbook");
  expect(
    within(dialog).getByText("A memory is already mounted under this name."),
  ).toBeTruthy();
  await user.clear(name);
  await user.type(name, "prefs");
  await user.click(within(dialog).getByRole("button", { name: "Add memory" }));
  await waitFor(() => expect(mocks.POST).toHaveBeenCalledOnce());
  expect(mocks.POST.mock.calls[0]).toEqual([
    "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories",
    {
      params: { path: { workspace_id: "ws_test", thread_id: "thr_1" } },
      headers: { "If-Match": '"thr_1:4"' },
      body: { name: "prefs", memory_id: "mem_prefs", access: "write" },
    },
  ]);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  cache.clear();
});

it("removes a mount under the Thread's ETag", async () => {
  const cache = show();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Remove handbook" }),
  );
  await waitFor(() => expect(mocks.DELETE).toHaveBeenCalledOnce());
  expect(mocks.DELETE.mock.calls[0]).toEqual([
    "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories/{name}",
    {
      params: {
        path: { workspace_id: "ws_test", thread_id: "thr_1", name: "handbook" },
      },
      headers: { "If-Match": '"thr_1:4"' },
    },
  ]);
  cache.clear();
});

it("offers no changes without run permission or on a child Thread", async () => {
  mocks.canRun = false;
  const cache = show();
  await screen.findByRole("link", { name: "Handbook" });
  expect(screen.queryByRole("button", { name: "Add memory" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove handbook" })).toBeNull();
  cache.clear();
  mocks.canRun = true;
  mocks.thread = fixtureThread({ origin: "child" });
  const again = show();
  await screen.findAllByRole("link", { name: "Handbook" });
  expect(screen.queryByRole("button", { name: "Add memory" })).toBeNull();
  again.clear();
});

it("links a run's frozen mounts to the changes that run made", async () => {
  const cache = show(
    <MemoryMountRows
      mounts={[
        { name: "handbook", memory_id: "mem_book", access: "read" },
        { name: "gone", memory_id: "mem_gone", access: "write" },
      ]}
      empty="None"
      runId="run_2"
    />,
  );
  expect(
    (await screen.findByRole("link", { name: "Handbook" })).getAttribute(
      "href",
    ),
  ).toBe("/workspace/design/memories/mem_book");
  expect(
    screen
      .getByRole("link", { name: "Changes by this run to handbook" })
      .getAttribute("href"),
  ).toBe("/workspace/design/memories/mem_book?tab=history&run=run_2");
  expect(screen.getByText("Memory unavailable · mem_gone")).toBeTruthy();
  expect(screen.getByText("Read")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
  cache.clear();
});
