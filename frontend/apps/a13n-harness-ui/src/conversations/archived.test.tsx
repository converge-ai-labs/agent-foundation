// @vitest-environment jsdom
import { i18n } from "../i18n";
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { ArchivedPage } from "./archived";
import userEvent from "@testing-library/user-event";

const clients: QueryClient[] = [];
afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
  clients.forEach((client) => client.clear());
  vi.unstubAllGlobals();
});
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <TransportContext value={createTransport("test", () => {})}>
        <MemoryRouter>
          <ArchivedPage />
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const row = (id: string, archived = true) => ({
  thread: {
    thread_id: id,
    title: id,
    archived,
    metadata_version: 3,
    configuration: {
      project_id: "project-one",
      agent_source: { kind: "configured", id: "assistant" },
    },
    root_activity: { state: "inactive" },
  },
  project_name: "One",
});

it("queries only archived chats with independent search and pagination, then restores without navigating", async () => {
  const urls: URL[] = [];
  let restored = false;
  let mutation: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (request.method === "PATCH") {
        mutation = await request.json();
        restored = true;
        return json({});
      }
      urls.push(url);
      const searching = !!url.searchParams.get("query");
      return json({
        rows: searching
          ? []
          : url.searchParams.has("cursor")
            ? [row("Older")]
            : restored
              ? []
              : [row("Stored"), row("Active", false)],
        next_cursor:
          !searching && !restored && !url.searchParams.has("cursor")
            ? "older"
            : null,
      });
    }),
  );
  mount();
  await screen.findByRole("link", { name: /Stored.*Archived/ });
  expect(screen.queryByRole("link", { name: "Active" })).toBeNull();
  expect(urls[0].searchParams.get("archived_only")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Load more" }));
  await screen.findByRole("link", { name: /Older.*Archived/ });
  expect(urls.at(-1)?.searchParams.get("cursor")).toBe("older");
  fireEvent.click(screen.getByRole("button", { name: "Restore Stored" }));
  await waitFor(() =>
    expect(screen.queryByRole("link", { name: /Stored.*Archived/ })).toBeNull(),
  );
  expect(mutation).toEqual({ expected_version: 3, patch: { archived: false } });
  expect(screen.getByRole("heading", { name: "Archived chats" })).toBeTruthy();
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "missing" },
  });
  await screen.findByText("No matching chats");
  expect(urls.at(-1)?.searchParams.has("cursor")).toBe(false);
});

it("shows a failed list as an error rather than an empty archive", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => json({ error: { message: "Archive unavailable" } }, 503)),
  );
  mount();
  await screen.findByText("Archive unavailable");
  expect(screen.queryByText("No archived chats")).toBeNull();
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
});

it("preserves archive filters and pending restore across language changes without replaying writes", async () => {
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const writes: unknown[] = [];
  const reads: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "PATCH") {
        writes.push(await request.json());
        await pending;
        return json({ error: { message: "Restore rejected by host" } }, 409);
      }
      reads.push(new URL(request.url));
      return json({
        rows: [
          row("Untitled chat"),
          {
            ...row("empty-title"),
            thread: { ...row("empty-title").thread, title: "" },
          },
        ],
        next_cursor: null,
      });
    }),
  );
  mount();
  const user = userEvent.setup();
  await screen.findAllByRole("button", { name: "Restore Untitled chat" });
  const search = screen.getByRole("searchbox", {
    name: "Search archived chats",
  });
  await user.type(search, "exact source");
  await waitFor(() =>
    expect(reads.at(-1)?.searchParams.get("query")).toBe("exact source"),
  );
  await waitFor(() => expect(clients.at(-1)!.isFetching()).toBe(0));
  const calls = vi.mocked(fetch).mock.calls.length;
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByRole("searchbox", { name: "搜索已归档聊天" })).toBe(
    search,
  );
  expect((search as HTMLInputElement).value).toBe("exact source");
  expect(document.activeElement).toBe(search);
  expect(
    screen.getByRole("button", { name: "恢复 Untitled chat" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "恢复 未命名聊天" })).toBeTruthy();
  expect(vi.mocked(fetch).mock.calls).toHaveLength(calls);
  await user.click(
    screen.getByRole("button", { name: "Untitled chat 的操作" }),
  );
  await user.click(await screen.findByRole("menuitem", { name: "删除聊天" }));
  expect(
    screen.getByRole("dialog", { name: "删除聊天？" }).textContent,
  ).toContain("“Untitled chat”及其子代理历史记录");
  await act(() => i18n.changeLanguage("en"));
  expect(screen.getByRole("dialog", { name: "Delete chat?" })).toBeTruthy();
  expect(writes).toEqual([]);
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(
    screen.getAllByRole("button", { name: "Restore Untitled chat" })[0],
  );
  await waitFor(() =>
    expect(writes).toEqual([
      { expected_version: 3, patch: { archived: false } },
    ]),
  );
  await act(() => i18n.changeLanguage("zh-CN"));
  await user.click(screen.getByRole("button", { name: "恢复 Untitled chat" }));
  expect(writes).toHaveLength(1);
  await act(async () => {
    finish();
    await pending;
  });
  await screen.findByText("Restore rejected by host");
  expect(writes).toHaveLength(1);
  expect((search as HTMLInputElement).value).toBe("exact source");
});
