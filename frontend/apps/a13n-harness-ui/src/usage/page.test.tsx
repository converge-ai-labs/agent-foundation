// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { UsagePage } from "./page";

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  vi.unstubAllGlobals();
});
const totals = {
  model_requests: 3,
  input_tokens: 100,
  output_tokens: 20,
  total_tokens: 120,
  cache_read_tokens: 30,
  cache_write_tokens: 0,
  model_cost_usd: "0.125",
  unknown_model_costs: 1,
};
const payload = {
  as_of: "2026-09-08T10:00:00Z",
  start: "2026-09-07",
  end: "2026-09-09",
  first_observed_at: "2026-09-07T00:00:00Z",
  observed_through: "2026-09-08T10:00:00Z",
  summary: totals,
  daily: [{ day: "2026-09-07", totals }],
  groups: [
    {
      key: "p",
      label: "Example project",
      project_id: "p",
      thread_id: null,
      model: null,
      totals,
    },
  ],
  group_count: 26,
  projects: [{ key: "p", label: "Example project" }],
  threads: [{ key: "t", label: "Design session", project_id: "p" }],
  models: [{ key: '["provider","model"]', label: "provider/model" }],
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
function Location() {
  const location = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output data-testid="location">{location.search}</output>
      <button onClick={() => navigate(-1)}>Back</button>
    </>
  );
}
function mount(search = "range=custom&start=2026-09-07&end=2026-09-09") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <QueryClientProvider client={client}>
      <TransportContext value={createTransport("test", () => {})}>
        <MemoryRouter initialEntries={[`/usage?${search}`]}>
          <Location />
          <UsagePage />
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}
it("paginates without changing summary, narrows rows and restores URL filters on back", async () => {
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      urls.push(url);
      return json({
        ...payload,
        groups:
          url.searchParams.get("offset") === "25"
            ? [
                {
                  ...payload.groups[0],
                  key: "q",
                  label: "Next project",
                  project_id: "q",
                },
              ]
            : payload.groups,
      });
    }),
  );
  mount();
  await screen.findByRole("button", { name: "Example project" });
  expect(screen.getAllByText("$0.125+").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("button", { name: "Next project" });
  expect(urls.at(-1)?.searchParams.get("offset")).toBe("25");
  fireEvent.click(screen.getByRole("button", { name: "Next project" }));
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.get("project_id")).toBe("q"),
  );
  expect(urls.at(-1)?.searchParams.get("offset")).toBe("0");
  expect(urls.at(-1)?.searchParams.get("group_by")).toBe("thread");
  fireEvent.click(screen.getByRole("button", { name: "Back" }));
  await screen.findByRole("button", { name: "Next project" });
  expect(screen.getByTestId("location").textContent).toContain("offset=25");
});
it("retains results on refresh failure and recovers explicitly", async () => {
  let fail = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      fail
        ? json({ error: { message: "Usage unavailable" } }, 503)
        : json(payload),
    ),
  );
  mount();
  await screen.findByRole("button", { name: "Example project" });
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText("Usage unavailable");
  expect(screen.getByRole("button", { name: "Example project" })).toBeTruthy();
  expect(
    screen.getByText("Update failed · showing previous results"),
  ).toBeTruthy();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(screen.queryByText("Usage unavailable")).toBeNull(),
  );
});
it("shows invalid custom dates without sending a query", async () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  mount("range=custom&start=2025-01-01&end=2026-09-09");
  expect(screen.getByRole("alert").textContent).toContain("366");
  expect(fetcher).not.toHaveBeenCalled();
});

it("abbreviates large totals while keeping exact counts and costs accessible", async () => {
  const large = {
    ...totals,
    total_tokens: 11931980116,
    input_tokens: 11894595000,
    output_tokens: 37385116,
    model_requests: 123357,
    model_cost_usd: "17047.94",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json({
        ...payload,
        summary: large,
        groups: [{ ...payload.groups[0], totals: large }],
        daily: [{ day: "2026-09-07", totals: large }],
      }),
    ),
  );
  mount();
  await screen.findByRole("button", { name: "Example project" });
  expect(screen.getAllByText("11.93B")[0].getAttribute("title")).toBe(
    "11,931,980,116",
  );
  expect(screen.getAllByText("123.36K")[0].getAttribute("aria-label")).toBe(
    "123,357",
  );
  expect(screen.getAllByText("$17.05K+")[0].getAttribute("title")).toBe(
    "$17047.94+",
  );
  expect(
    screen.getByRole("button", { name: "2026-09-07 · 11,931,980,116 tokens" }),
  ).toBeTruthy();
});

it("reveals additional filters and token details without refetching or losing URL scope", async () => {
  const fetcher = vi.fn(async () => json(payload));
  vi.stubGlobal("fetch", fetcher);
  mount("range=30&project_id=p");
  await screen.findByRole("button", { name: "Example project" });
  expect(screen.queryByRole("combobox", { name: "Chat" })).toBeNull();
  expect(screen.queryByRole("columnheader", { name: "Cache read" })).toBeNull();
  const reads = fetcher.mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  expect(screen.getByRole("combobox", { name: "Chat" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Token details" }));
  expect(screen.getByRole("columnheader", { name: "Cache read" })).toBeTruthy();
  expect(screen.getByTestId("location").textContent).toContain("project_id=p");
  expect(fetcher.mock.calls.length).toBe(reads);
});

it("keeps daily bars in one tab stop while arrow keys and Home/End move between days", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json({
        ...payload,
        daily: [
          { day: "2026-09-06", totals },
          { day: "2026-09-07", totals },
          { day: "2026-09-08", totals },
        ],
      }),
    ),
  );
  mount();
  const last = await screen.findByRole("button", {
    name: "2026-09-08 · 120 tokens",
  });
  const first = screen.getByRole("button", { name: "2026-09-06 · 120 tokens" });
  const middle = screen.getByRole("button", {
    name: "2026-09-07 · 120 tokens",
  });
  expect(
    [first, middle, last].filter((button) => button.tabIndex === 0),
  ).toEqual([last]);
  last.focus();
  fireEvent.keyDown(last, { key: "Home" });
  expect(document.activeElement).toBe(first);
  expect(
    [first, middle, last].filter((button) => button.tabIndex === 0),
  ).toEqual([first]);
  fireEvent.keyDown(first, { key: "ArrowRight" });
  expect(document.activeElement).toBe(middle);
  fireEvent.keyDown(middle, { key: "End" });
  expect(document.activeElement).toBe(last);
});
