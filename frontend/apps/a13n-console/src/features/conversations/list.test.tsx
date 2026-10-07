// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { createClient, type Client } from "../../service-client";
import { SessionList } from "./list";

let client: Client;
const requests: URL[] = [];
let canRun = false;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: (verb: string) => verb === "run" && canRun,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  client.close();
  requests.length = 0;
  canRun = false;
});

it.each([
  ["", true, "No sessions yet", false],
  ["?q=missing", true, "No matching sessions", true],
  ["?status=failed", true, "No matching sessions", true],
  ["", false, "No sessions yet", false],
] as const)(
  "offers session creation in the right place for %s (run permission: %s)",
  async (search, permission, title, inHeader) => {
    canRun = permission;
    client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session" },
      fetch: async () => Response.json({ items: [], next_cursor: null }),
    });
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: { queries: { retry: false } },
          })
        }
      >
        <MemoryRouter initialEntries={[`/workspace/test/sessions${search}`]}>
          <SessionList />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByText(title);
    if (!permission) {
      expect(screen.queryByRole("link", { name: "New session" })).toBeNull();
      return;
    }
    const create = screen.getByRole("link", { name: "New session" });
    expect(!!create.closest("header")).toBe(inHeader);
    expect(create.getAttribute("href")).toBe("/workspace/test/sessions/new");
  },
);

const session = (text: string) => ({
  id: `sess_${text.toLowerCase()}`,
  workspace_id: "ws_test",
  labels: {},
  created_by_id: "usr_test",
  last_run_id: null,
  run_count: 1,
  preview: null,
  version: 1,
  created_at: "2026-09-20T00:00:00Z",
  updated_at: "2026-09-20T00:00:00Z",
  title: text,
});

it("pages sessions and starts a changed search from its first page", async () => {
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      requests.push(url);
      const { searchParams } = url;
      const [items, next] = searchParams.has("q")
        ? [[session("Scout")], null]
        : searchParams.get("cursor") === "two"
          ? [[session("Second")], null]
          : [[session("First")], "two"];
      return Response.json({
        items: items.map((item) => ({
          ...item,
          preview: {
            input_text: item.title,
            agent_name: "Agent",
            status: "completed",
            trigger: "input",
          },
        })),
        next_cursor: next,
      });
    },
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <SessionList />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await screen.findByText("First");
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Second");
  await user.type(screen.getByRole("searchbox"), "scout");
  await screen.findByText("Scout");
  const searched = requests.filter((url) => url.searchParams.has("q"));
  expect(searched.length).toBeGreaterThan(0);
  // A cursor belongs to the query that issued it.
  for (const url of searched)
    expect(url.searchParams.has("cursor")).toBe(false);
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Previous" })).toBeNull(),
  );
});

function SelectedLocation() {
  const { pathname, search } = useLocation();
  return <output>{pathname + search}</output>;
}

it.each([
  [
    "",
    true,
    "Continue conversation",
    "/workspace/test/sessions/sess_first?view=chat",
  ],
  [
    "?q=thr_branch",
    true,
    "Continue conversation",
    "/workspace/test/sessions/sess_first/threads/thr_branch?view=chat",
  ],
  [
    "?q=thr_branch",
    false,
    "Inspect execution",
    "/workspace/test/sessions/sess_first/threads/thr_branch?view=debug",
  ],
  [
    "?q=sess_first",
    true,
    "Inspect execution",
    "/workspace/test/sessions/sess_first?view=debug",
  ],
])(
  "opens the requested view from %s (%s, %s)",
  async (search, permission, action, expected) => {
    canRun = permission;
    client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session" },
      fetch: async () =>
        Response.json({ items: [session("First")], next_cursor: null }),
    });
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={cache}>
        <MemoryRouter initialEntries={[`/workspace/test/sessions${search}`]}>
          <Routes>
            <Route path="/workspace/test/sessions" element={<SessionList />} />
            <Route
              path="/workspace/test/sessions/:sessionId/*"
              element={<SelectedLocation />}
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const link = await screen.findByRole("link", { name: action });
    if (!permission)
      expect(
        screen.queryByRole("link", { name: "Continue conversation" }),
      ).toBeNull();
    await userEvent.setup().click(link);
    expect(await screen.findByText(expected)).toBeTruthy();
    cache.clear();
  },
);
