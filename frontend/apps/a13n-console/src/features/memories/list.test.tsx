// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { createClient } from "../../service-client";
import { MemoriesPage } from "./page";

vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/ws_1",
    organization: { id: "org_1" },
    workspace: { id: "ws_1" },
    can: (verb: string) => permissions.includes(verb),
  }),
  useAccess: () => ({ workspace: { id: "ws_1" } }),
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

  name: "Team handbook",
  description: null,
  kind: "file",
  type: "postgres",
  labels: { team: "docs" },
  file_count: 4,
  content_bytes: 1500,
  history_bytes: 600,
  updated_at: "2026-09-20T10:00:00Z",
};
const facts = {
  ...handbook,
  id: "mem_2",

  name: "Team facts",
  kind: "record",
  type: "mem0_oss",
  labels: {},
  file_count: null,
  content_bytes: null,
  history_bytes: null,
};
const provider = (id: string, name: string, enabled = true) => ({
  id,
  name,
  enabled,
  type: "mem0_oss",
  workspace_id: "ws_1",
});

function setup(
  memories = [handbook, facts],
  allowed = ["read", "run", "write"],
  providers = [
    provider("memprov_1", "Team mem0"),
    provider("memprov_2", "Old mem0", false),
  ],
) {
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
    if (url.pathname === "/api/v1/provider-types/memory")
      return Response.json({
        items: [{ type: "mem0_oss", display_name: "Mem0 (self-hosted)" }],
      });
    if (url.pathname === "/api/v1/memory-providers")
      return Response.json({ items: providers, next_cursor: null });
    const { searchParams } = url;
    return Response.json({
      items: memories.filter(
        (memory) =>
          !searchParams.get("kind") || memory.kind === searchParams.get("kind"),
      ),
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
      <MemoryRouter initialEntries={["/workspace/ws_1/memories"]}>
        <Routes>
          <Route
            path="/workspace/:workspaceId/memories"
            element={<MemoriesPage />}
          />
          <Route
            path="/workspace/:workspaceId/memories/:memoryId"
            element={<p>Memory detail</p>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), requests };
}

it("lists memories with their kind and a file memory's size, and filters them", async () => {
  const { user, requests } = setup();
  const row = (await screen.findByText("Team handbook")).closest("tr")!;
  expect(within(row).getByText("File")).toBeTruthy();
  expect(within(row).getByText("PostgreSQL")).toBeTruthy();
  expect(within(row).getByText("4 files")).toBeTruthy();
  expect(within(row).getByText("3 KB")).toBeTruthy();
  // The provider keeps a record memory's records, so it has no counts.
  const record = screen.getByText("Team facts").closest("tr")!;
  expect(within(record).getByText("Record")).toBeTruthy();
  expect(await within(record).findByText("Mem0 (self-hosted)")).toBeTruthy();
  expect(within(record).queryByText(/files?$/)).toBeNull();
  expect(within(record).queryByText(/ KB$| B$/)).toBeNull();
  await user.click(screen.getByRole("combobox", { name: "Kind" }));
  await user.click(await screen.findByRole("option", { name: "Record" }));
  await waitFor(() => expect(screen.queryByText("Team handbook")).toBeNull());
  expect(requests.at(-1)?.url.searchParams.get("kind")).toBe("record");
  // A record memory's types are its providers', not PostgreSQL.
  await user.click(screen.getByRole("combobox", { name: "Type" }));
  expect(await screen.findByRole("option", { name: "Mem0 (self-hosted)" }));
  expect(screen.queryByRole("option", { name: "PostgreSQL" })).toBeNull();
  await user.keyboard("{Escape}");
});

it("says when no memory matches the filters", async () => {
  const { user } = setup([handbook]);
  await screen.findByText("Team handbook");
  await user.click(screen.getByRole("combobox", { name: "Kind" }));
  await user.click(await screen.findByRole("option", { name: "Record" }));
  expect(await screen.findByText("No matching memories")).toBeTruthy();
  expect(
    screen.getByText("Change or clear the search and filters."),
  ).toBeTruthy();
});

it("offers creation from the empty state only to people who may configure memories", async () => {
  setup([], ["read", "run"]);
  expect(await screen.findByText("No memories yet")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Create memory" })).toBeNull();
});

it("creates a memory identified by its ID", async () => {
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
  expect(within(dialog).queryByRole("textbox", { name: "Key" })).toBeNull();
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
    type: "postgres",
    name: "Team Handbook",
    description: null,
    guide: "One topic per file.",
    always_load: ["README.md"],
  });
});

it("creates a record memory on an enabled provider, adopting a namespace", async () => {
  const { user, requests } = setup([]);
  await screen.findByText("No memories yet");
  await user.click(
    screen.getAllByRole("button", { name: "Create memory" })[0]!,
  );
  const dialog = await screen.findByRole("dialog");
  await user.click(
    within(dialog).getByRole("button", { name: "Record memory" }),
  );
  // Records have no always-loaded files.
  expect(
    within(dialog).queryByRole("combobox", { name: "Path to always load" }),
  ).toBeNull();
  await user.type(
    within(dialog).getByRole("textbox", { name: "Name" }),
    "Team facts",
  );
  await user.click(
    within(dialog).getByRole("combobox", { name: "Memory provider" }),
  );
  // A disabled provider keeps no new records.
  expect(screen.queryByRole("option", { name: /Old mem0/ })).toBeNull();
  await user.click(
    await screen.findByRole("option", {
      name: "Team mem0 · Mem0 (self-hosted)",
    }),
  );
  await user.click(
    within(dialog).getByRole("button", { name: /Existing namespace/ }),
  );
  await user.type(
    within(dialog).getByRole("textbox", { name: "Namespace" }),
    "user-42",
  );
  expect(
    within(dialog).getByText(
      "Deleting this memory deletes every record in user-42 from the provider, including the records it adopts.",
    ),
  ).toBeTruthy();
  await user.click(
    within(dialog).getByRole("button", { name: "Create memory" }),
  );
  await screen.findByText("Memory detail");
  expect(requests.find((item) => item.method === "POST")?.body).toEqual({
    type: "mem0_oss",
    provider_id: "memprov_1",
    namespace: "user-42",
    name: "Team facts",
    description: null,
    guide: null,
  });
});

it("points to provider settings when no memory provider is enabled", async () => {
  const { user } = setup([], undefined, [provider("memprov_2", "Old", false)]);
  await screen.findByText("No memories yet");
  await user.click(
    screen.getAllByRole("button", { name: "Create memory" })[0]!,
  );
  const dialog = await screen.findByRole("dialog");
  await user.click(
    within(dialog).getByRole("button", { name: "Record memory" }),
  );
  expect(
    await within(dialog).findByText(
      "No memory provider is enabled for this workspace. Add one in provider settings first.",
    ),
  ).toBeTruthy();
  expect(
    within(dialog)
      .getByRole("link", { name: "Manage providers" })
      .getAttribute("href"),
  ).toBe("/workspace/ws_1/settings/providers?category=memory");
  expect(
    (
      within(dialog).getByRole("button", {
        name: "Create memory",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
