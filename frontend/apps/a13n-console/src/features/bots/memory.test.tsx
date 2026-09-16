import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import type { Schema } from "../../shared/api";
import { BotMemory } from "./memory";

const state = vi.hoisted(() => ({
  admin: true,
  http: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state.http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => state.admin,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const account = {
  id: "acct_test",
  workspace_id: "ws_test",
  provider_key: "slack",
  memory: { provider_id: "mp_test" },
} as Schema["Account"];
const scope = {
  id: "mscope_test",
  name: "Engineering",
  provider_id: "mp_test",
};
const entry = {
  id: "mdoc_test",
  title: "Release checklist",
  description: "Deployment steps",
  activity_date: "2026-09-16",
  state: "active",
  version: 1,
  timezone: "UTC",
  shared: false,
};
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});

function setup() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/?tab=memory&memory_scope=mscope_test"]}>
        <BotMemory account={account} reload={async () => {}} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return cache;
}

beforeEach(() => {
  vi.resetAllMocks();
  state.admin = true;
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("/memory-scopes"))
      return response({ items: [scope], next_cursor: null });
    if (path.endsWith("/index"))
      return response({
        text: "MEMORY.md",
        entries: [entry],
        next_cursor: null,
      });
    if (path.endsWith("/documents"))
      return response({ items: [entry], next_cursor: null });
    if (path.endsWith("/{document_id}"))
      return response({ ...entry, text: "Sensitive full document body" });
    throw new Error(`Unexpected request ${path}`);
  });
});
afterEach(() => cleanup());

it("shows navigation before fetching only the selected document body", async () => {
  setup();
  await screen.findByText("Deployment steps");
  expect(screen.queryByText("Sensitive full document body")).toBeNull();
  expect(
    state.http.GET.mock.calls.some(([path]) => path.endsWith("/{document_id}")),
  ).toBe(false);
  await userEvent.click(
    within(screen.getByRole("region", { name: "Memory details" })).getByRole(
      "button",
      { name: "Release checklist" },
    ),
  );
  expect(await screen.findByText("Sensitive full document body")).toBeTruthy();
  expect(
    state.http.GET.mock.calls.filter(([path]) =>
      path.endsWith("/{document_id}"),
    ),
  ).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

it("does not request private metadata or bodies for non-administrators", async () => {
  state.admin = false;
  setup();
  expect(screen.getByText("Administrator access required")).toBeTruthy();
  expect(state.http.GET).not.toHaveBeenCalled();
});

it("shows a revoked read failure without displaying an old body", async () => {
  setup();
  await screen.findByText("Deployment steps");
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("/{document_id}")) throw new Error("Memory not found.");
    return response({ items: [], next_cursor: null });
  });
  await userEvent.click(
    within(screen.getByRole("region", { name: "Memory details" })).getByRole(
      "button",
      { name: "Release checklist" },
    ),
  );
  expect(await screen.findByText("Memory not found.")).toBeTruthy();
  expect(screen.queryByText("Sensitive full document body")).toBeNull();
});
