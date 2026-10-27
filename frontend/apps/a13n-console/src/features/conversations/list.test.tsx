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
    "Continue thread",
    "/workspace/test/sessions/sess_first?view=chat",
  ],
  [
    "?q=thread_0123456789abcdef0123456789abcdef",
    true,
    "Continue thread",
    "/workspace/test/sessions/sess_first/threads/thread_0123456789abcdef0123456789abcdef?view=chat",
  ],
  [
    "?q=thread_0123456789abcdef0123456789abcdef",
    false,
    "Inspect execution",
    "/workspace/test/sessions/sess_first/threads/thread_0123456789abcdef0123456789abcdef?view=debug",
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
        Response.json({
          items: [{ ...session("First"), labels: { "a13n.console": "debug" } }],
          next_cursor: null,
        }),
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
    await screen.findByText("First");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Session actions" }));
    const link = await screen.findByRole("menuitem", { name: action });
    if (!permission)
      expect(
        screen.queryByRole("menuitem", { name: "Continue thread" }),
      ).toBeNull();
    await userEvent.setup().click(link);
    expect(await screen.findByText(expected)).toBeTruthy();
    cache.clear();
  },
);

it("finds readable identity and narrows by source and labels without navigating to a Thread", async () => {
  const value = {
    ...session("Review #42"),
    origin: {
      application: "review-bot",
      name: "GitHub · acme/widgets #42",
      external_id: "main_42",
    },
    labels: { project: "Widgets", role: "main" },
    preview: {
      input_text: "<system_reminder>Hidden transport wrapper</system_reminder>",
      output_text: "Ready for review",
      agent_name: "Reviewer",
      status: "completed",
      trigger: "input",
    },
  };
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      requests.push(new URL(new Request(input, init).url));
      return Response.json({ items: [value], next_cursor: null });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <SessionList />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await screen.findByText("Review #42");
  expect(screen.queryByText(/Hidden transport wrapper/)).toBeNull();
  // The source is display text, never a link out of the Console.
  expect(screen.getByText("GitHub · acme/widgets #42").closest("a")).toBeNull();
  await user.click(screen.getByRole("button", { name: "review-bot" }));
  await waitFor(() =>
    expect(requests.at(-1)?.searchParams.get("application")).toBe("review-bot"),
  );
  await user.click(
    await screen.findByRole("button", {
      name: "Remove filter Source: review-bot",
    }),
  );
  await waitFor(() =>
    expect(requests.at(-1)?.searchParams.has("application")).toBe(false),
  );
  await user.click(
    screen.getByRole("button", { name: "Filter by project: Widgets" }),
  );
  await waitFor(() =>
    expect(requests.at(-1)?.searchParams.getAll("label")).toEqual([
      "project:Widgets",
    ]),
  );
  await user.type(screen.getByRole("searchbox"), "deploy fix");
  await waitFor(() =>
    expect(requests.at(-1)?.searchParams.get("q")).toBe("deploy fix"),
  );
  expect(
    screen.getByRole("link", { name: "Review #42" }).getAttribute("href"),
  ).toBe("/workspace/test/sessions/sess_review #42?view=debug");
  await user.click(screen.getByRole("button", { name: "Session actions" }));
  expect(
    screen.queryByRole("menuitem", { name: "Edit session details" }),
  ).toBeNull();
  cache.clear();
});
