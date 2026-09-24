// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { createClient } from "../../service-client";
import { MemoriesPage } from "./page";

vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "ws_1" },
    can: (verb: string) => permissions.includes(verb),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values?.[name])),
    i18n: { resolvedLanguage: "en" },
  }),
}));

let permissions: string[];
let client: ReturnType<typeof createClient>;
let cache: QueryClient;

afterEach(() => {
  client?.close();
  cache?.clear();
});

const handbook = {
  id: "mem_1",
  key: "handbook",
  name: "Team handbook",
  description: null,
  labels: { team: "docs" },
  file_count: 4,
  content_bytes: 1500,
  history_bytes: 600,
  updated_at: "2026-09-20T10:00:00Z",
};

function setup(memories = [handbook], allowed = ["read", "run", "write"]) {
  permissions = allowed;
  const requests: { method: string; url: URL; body: unknown }[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    const body = request.method === "POST" ? await request.json() : undefined;
    requests.push({ method: request.method, url, body });
    if (request.method === "POST")
      return Response.json(
        { ...handbook, ...(body as object), id: "mem_new" },
        { status: 201 },
      );
    return Response.json({
      items: url.searchParams.getAll("label").includes("team:ops")
        ? []
        : memories,
      next_cursor: null,
    });
  };
  client = createClient({
    baseUrl: "http://localhost",
    auth: { type: "session", csrfToken: "test-csrf" },
    fetch: fetcher,
    maxReadRetries: 0,
  });
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/workspace/design/memories"]}>
        <Routes>
          <Route
            path="/workspace/:workspaceKey/memories"
            element={<MemoriesPage />}
          />
          <Route
            path="/workspace/:workspaceKey/memories/:memoryId"
            element={<p>Memory detail</p>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), requests };
}

it("lists memories with their size and filters them by label", async () => {
  const { user, requests } = setup();
  const row = (await screen.findByText("Team handbook")).closest("tr")!;
  expect(within(row).getByText("4 files")).toBeTruthy();
  expect(within(row).getByText("3 KB")).toBeTruthy();
  expect(within(row).getByText("team: docs")).toBeTruthy();
  const filter = screen.getByRole("searchbox", { name: "Filter by label" });
  const read = requests.length;
  await user.type(filter, "team");
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Write each label as key:value.",
  );
  // A malformed filter sends nothing.
  expect(requests).toHaveLength(read);
  await user.type(filter, ":ops");
  expect(await screen.findByText("No matching memories")).toBeTruthy();
  expect(requests.at(-1)?.url.searchParams.getAll("label")).toEqual([
    "team:ops",
  ]);
});

it("offers creation from the empty state only to people who may configure memories", async () => {
  setup([], ["read", "run"]);
  expect(await screen.findByText("No memories yet")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Create memory" })).toBeNull();
});

it("creates a memory with a key suggested from its name", async () => {
  const { user, requests } = setup([]);
  await screen.findByText("No memories yet");
  await user.click(
    screen.getAllByRole("button", { name: "Create memory" })[0]!,
  );
  const dialog = await screen.findByRole("dialog");
  await user.type(
    within(dialog).getByRole("textbox", { name: "Name" }),
    "Team Handbook",
  );
  expect(
    (within(dialog).getByRole("textbox", { name: "Key" }) as HTMLInputElement)
      .value,
  ).toBe("team-handbook");
  await user.type(
    within(dialog).getByRole("textbox", { name: "Labels" }),
    "team:docs",
  );
  await user.click(within(dialog).getByRole("button", { name: "Custom" }));
  await user.type(
    within(dialog).getByRole("textbox", { name: "Guide" }),
    "One topic per file.",
  );
  await user.type(
    within(dialog).getByRole("combobox", { name: "Path to always load" }),
    "README.md{Enter}",
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Create memory" }),
  );
  await screen.findByText("Memory detail");
  expect(requests.find((item) => item.method === "POST")?.body).toEqual({
    key: "team-handbook",
    type: "postgres",
    name: "Team Handbook",
    description: null,
    labels: { team: "docs" },
    guide: "One topic per file.",
    always_load: ["README.md"],
  });
});
