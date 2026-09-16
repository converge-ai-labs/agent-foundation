// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
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

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
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
    configuration: { project_id: "project-one" },
    root_activity: { state: "inactive" },
  },
  project_name: "One",
});

it("queries only archived conversations with independent search and pagination, then restores without navigating", async () => {
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
  fireEvent.click(
    screen.getByRole("button", { name: "Show more archived conversations" }),
  );
  await screen.findByRole("link", { name: /Older.*Archived/ });
  expect(urls.at(-1)?.searchParams.get("cursor")).toBe("older");
  fireEvent.click(screen.getByRole("button", { name: "Restore Stored" }));
  await waitFor(() =>
    expect(screen.queryByRole("link", { name: /Stored.*Archived/ })).toBeNull(),
  );
  expect(mutation).toEqual({ expected_version: 3, patch: { archived: false } });
  expect(
    screen.getByRole("heading", { name: "Archived conversations" }),
  ).toBeTruthy();
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "missing" },
  });
  await screen.findByText("No matching conversations");
  expect(urls.at(-1)?.searchParams.has("cursor")).toBe(false);
});

it("shows a failed list as an error rather than an empty archive", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => json({ error: { message: "Archive unavailable" } }, 503)),
  );
  mount();
  await screen.findByText("Archive unavailable");
  expect(screen.queryByText("No archived conversations")).toBeNull();
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
});
