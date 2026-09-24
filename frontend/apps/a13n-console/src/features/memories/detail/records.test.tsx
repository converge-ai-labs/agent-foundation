// @vitest-environment jsdom
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { createClient } from "../../../service-client";
import type { Schema } from "../../../shared/api";
import { MemoryDetail } from "../page";

vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    organization: { id: "org_1" },
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

beforeAll(() => {
  // jsdom lacks the pointer capture that menus and selects use.
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
});

afterEach(() => {
  client?.close();
  cache?.clear();
});

const AT = "2026-09-20T10:00:00Z";
const base = "/api/v1/workspaces/ws_1/memories/mem_1";

type Refusal = { status: number; code: string; details: object };

/** A record memory on a mem0 provider, behind the Service's record routes. */
function setup({
  allowed = ["read", "run", "write"],
  tab = "",
}: { allowed?: string[]; tab?: string } = {}) {
  permissions = allowed;
  // Record routes answer these methods with a refusal.
  const refusals = new Map<string, Refusal>();
  const memory: Schema["Memory"] = {
    id: "mem_1",
    organization_id: "org_1",
    workspace_id: "ws_1",
    key: "facts",
    name: "Team facts",
    description: null,
    kind: "record",
    type: "mem0",
    provider_id: "memprov_1",
    namespace: "user-42",
    guide: null,
    inherited_guide: "Keep each record to one fact.",
    always_load: [],
    labels: {},
    file_count: null,
    content_bytes: null,
    history_bytes: null,
    version: 1,
    created_by_id: "usr_1",
    updated_by_id: "usr_1",
    created_at: AT,
    updated_at: AT,
  };
  const records: Schema["MemoryRecordView"][] = [
    { id: "rec_1", text: "Prefers tea in the morning.", updated_at: AT },
    { id: "rec_2", text: "Ships on Fridays.", updated_at: AT },
    { id: "rec_3", text: "Coffee after lunch.", updated_at: null },
  ];
  const requests: { method: string; path: string; body: unknown }[] = [];
  const failed = (status: number, code: string, details: object = {}) =>
    Response.json({ error: { code, message: code, details } }, { status });
  const fetcher: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    const path = decodeURIComponent(url.pathname);
    const body = ["POST", "PUT"].includes(request.method)
      ? await request.json()
      : undefined;
    requests.push({ method: request.method, path, body });
    const route = `${request.method} ${path}`;
    if (route === `GET ${base}`)
      return Response.json(memory, { headers: { ETag: '"mem_1:1"' } });
    if (route === "GET /api/v1/organizations/org_1/memory-providers")
      return Response.json({
        items: [
          { id: "memprov_1", name: "Team mem0", type: "mem0", enabled: true },
        ],
        next_cursor: null,
      });
    if (route === "GET /api/v1/provider-types/memory")
      return Response.json({
        items: [{ type: "mem0", display_name: "Mem0 Platform" }],
      });
    if (!path.startsWith(`${base}/records`)) return failed(404, "not_found");
    const refusal = refusals.get(request.method);
    if (refusal) return failed(refusal.status, refusal.code, refusal.details);
    // Two records a page, with the provider's own cursor.
    if (route === `GET ${base}/records`) {
      const start = url.searchParams.get("cursor") === "page_2" ? 2 : 0;
      return Response.json({
        items: records.slice(start, start + 2),
        next_cursor: start ? null : "page_2",
      });
    }
    if (route === `POST ${base}/records/search`) {
      const { query } = body as Schema["MemoryRecordSearch"];
      return Response.json({
        items: records
          .filter((item) => item.text.toLowerCase().includes(query))
          .map((item) => ({ ...item, score: 0.912 })),
        next_cursor: null,
      });
    }
    if (route === `POST ${base}/records`) {
      const created = {
        id: `rec_${records.length + 1}`,
        text: (body as Schema["MemoryRecordText"]).text,
        updated_at: AT,
      };
      records.unshift(created);
      return Response.json(created, { status: 201 });
    }
    const record = records.find(
      (item) => item.id === path.slice(`${base}/records/`.length),
    );
    if (!record) return failed(404, "not_found");
    if (request.method === "DELETE") {
      records.splice(records.indexOf(record), 1);
      return new Response(null, { status: 204 });
    }
    record.text = (body as Schema["MemoryRecordText"]).text;
    return Response.json(record);
  };
  client = createClient({
    baseUrl: "http://localhost",
    auth: { type: "session", csrfToken: "test-csrf" },
    fetch: fetcher,
    maxReadRetries: 0,
  });
  cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[`/workspace/design/memories/mem_1${tab}`]}>
        <Routes>
          <Route
            path="/workspace/:workspaceKey/memories/:memoryId"
            element={<MemoryDetail />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const sent = (method: string, suffix: string) =>
    requests.filter(
      (item) => item.method === method && item.path === base + suffix,
    );
  const refuse = (method: string, refusal: Refusal) =>
    refusals.set(method, refusal);
  return { user: userEvent.setup(), sent, refuse };
}

const table = () => screen.getByRole("table", { name: "Records" });

async function openMenu(user: ReturnType<typeof userEvent.setup>, row: string) {
  const cell = (await screen.findByText(row)).closest("tr")!;
  await user.click(
    within(cell).getByRole("button", { name: "Record actions" }),
  );
}

it("pages a record memory's records and searches them by meaning", async () => {
  const { user, sent } = setup();
  expect(await screen.findByText("Prefers tea in the morning.")).toBeTruthy();
  // Records live with the provider, so there are no files or history.
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
    "Records",
    "Configuration",
  ]);
  expect(screen.getByText("2 records on this page")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("Coffee after lunch.")).toBeTruthy();
  expect(within(table()).queryByText("Ships on Fridays.")).toBeNull();

  const search = screen.getByRole("searchbox", { name: "Search records" });
  await user.type(search, "coffee{Enter}");
  await waitFor(() =>
    expect(sent("POST", "/records/search").at(-1)?.body).toEqual({
      query: "coffee",
    }),
  );
  expect(await screen.findByText("0.91")).toBeTruthy();
  expect(screen.getByText("1 closest records")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
  await user.clear(search);
  expect(await screen.findByText("Prefers tea in the morning.")).toBeTruthy();
  expect(within(table()).queryByText("Score")).toBeNull();
});

it("adds and edits records, counting characters up to the contract's bound", async () => {
  const { user, sent } = setup();
  await screen.findByText("Prefers tea in the morning.");
  await user.click(screen.getByRole("button", { name: "Add record" }));
  let dialog = await screen.findByRole("dialog");
  const text = within(dialog).getByRole("textbox", { name: "Text" });
  // Characters, not UTF-16 units, as the Service counts them.
  await user.type(text, "Likes 🍵");
  expect(within(dialog).getByText("7 of 8000 characters")).toBeTruthy();
  await user.click(within(dialog).getByRole("button", { name: "Add record" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(sent("POST", "/records")[0]?.body).toEqual({ text: "Likes 🍵" });
  expect(await screen.findByText("Likes 🍵")).toBeTruthy();

  await openMenu(user, "Prefers tea in the morning.");
  await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
  dialog = await screen.findByRole("dialog", { name: "Edit record" });
  const save = within(dialog).getByRole("button", { name: "Save record" });
  expect((save as HTMLButtonElement).disabled).toBe(true);
  const edit = within(dialog).getByRole("textbox", { name: "Text" });
  await user.click(edit);
  await user.paste("x".repeat(8000));
  expect(
    within(dialog).getByText("A record holds at most 8000 characters."),
  ).toBeTruthy();
  expect((save as HTMLButtonElement).disabled).toBe(true);
  await user.clear(edit);
  await user.type(edit, "Prefers green tea.");
  await user.click(save);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(sent("PUT", "/records/rec_1")[0]?.body).toEqual({
    text: "Prefers green tea.",
  });
  expect(await screen.findByText("Prefers green tea.")).toBeTruthy();
});

it("deletes a record after confirming", async () => {
  const { user, sent } = setup();
  await openMenu(user, "Ships on Fridays.");
  await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
  const dialog = await screen.findByRole("dialog", { name: "Delete record" });
  expect(within(dialog).getByText("Ships on Fridays.")).toBeTruthy();
  await user.click(
    within(dialog).getByRole("button", { name: "Delete record" }),
  );
  await waitFor(() =>
    expect(screen.queryByText("Ships on Fridays.")).toBeNull(),
  );
  expect(sent("DELETE", "/records/rec_2")).toHaveLength(1);
});

it("asks to reload when the provider could not confirm a change", async () => {
  const { user, sent, refuse } = setup();
  await screen.findByText("Prefers tea in the morning.");
  await user.click(screen.getByRole("button", { name: "Add record" }));
  const dialog = await screen.findByRole("dialog");
  await user.type(
    within(dialog).getByRole("textbox", { name: "Text" }),
    "Maybe saved.",
  );
  refuse("POST", {
    status: 409,
    code: "conflict",
    details: { reason: "write_unconfirmed" },
  });
  await user.click(within(dialog).getByRole("button", { name: "Add record" }));
  expect(
    await within(dialog).findByText(
      "The memory's provider did not confirm this change, so it may or may not have happened. Reload to check before trying again.",
    ),
  ).toBeTruthy();
  // Saving again could add the record twice.
  expect(
    (
      within(dialog).getByRole("button", {
        name: "Add record",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  const reads = sent("GET", "/records").length;
  await user.click(within(dialog).getByRole("button", { name: "Reload" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() =>
    expect(sent("GET", "/records").length).toBeGreaterThan(reads),
  );
});

it("says when the memory's provider is unavailable", async () => {
  const { refuse } = setup();
  refuse("GET", {
    status: 503,
    code: "unavailable",
    details: { dependency: "memory:mem0" },
  });
  expect(
    await screen.findByText(
      "The memory's provider is unavailable, so its records cannot be read or changed now. Try again later.",
    ),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
});

it("shows records read-only to readers", async () => {
  const { user } = setup({ allowed: ["read"] });
  await screen.findByText("Prefers tea in the morning.");
  expect(screen.queryByRole("button", { name: "Add record" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Record actions" })).toBeNull();
  await user.type(
    screen.getByRole("searchbox", { name: "Search records" }),
    "tea{Enter}",
  );
  expect(await screen.findByText("0.91")).toBeTruthy();
});

it("shows where a record memory keeps its records and its inherited guide", async () => {
  setup({ tab: "?tab=configuration" });
  const storage = (await screen.findByText("Storage")).closest("section")!;
  expect(within(storage).getByText("Record")).toBeTruthy();
  expect(await within(storage).findByText("Mem0 Platform")).toBeTruthy();
  expect(await within(storage).findByText("Team mem0")).toBeTruthy();
  expect(within(storage).getByText("user-42")).toBeTruthy();
  expect(screen.getByText("Keep each record to one fact.")).toBeTruthy();
  expect(screen.getByText(/for record memories/)).toBeTruthy();
  // Records have no files to load.
  expect(screen.queryByText("Always loaded files")).toBeNull();
});
