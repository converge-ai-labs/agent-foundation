import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createClient, type Client } from "../../service-client";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { BranchOrigin, ThreadBranches } from "./thread-branches";
import {
  fixtureNavigationPage,
  fixtureChildThread,
  fixtureForkThread,
  fixtureThread,
} from "./transcript/fixture";

vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_1" },
    basePath: "/workspace/ws_1",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(options?.[name] ?? "")),
  }),
}));
let cache: QueryClient;
let client: Client;
let requests: URL[];
vi.mock("../../auth/context", () => ({ useClient: () => client }));
beforeEach(() => {
  requests = [];
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 15_000 } },
  });
  client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      return Response.json(fixtureNavigationPage(threads, url.searchParams));
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={cache}>{children}</QueryClientProvider>;
}
function show(ui: ReactNode) {
  return render(ui, { wrapper: Wrapper });
}

const nested = fixtureThread({
  id: "thr_nested",
  origin: "fork",
  origin_thread_id: fixtureForkThread.id,
  origin_run_id: "run_fork",
  created_at: "2026-09-21T00:00:00Z",
  last_run_id: "run_nested",
});
const threads = [
  nested,
  fixtureChildThread,
  fixtureForkThread,
  fixtureThread(),
];

it.each(["chat", "debug"] as const)(
  "switches branches with stable names and preserves %s",
  async (level) => {
    const { rerender } = show(
      <MemoryRouter>
        <ThreadBranches thread={nested} level={level} />
      </MemoryRouter>,
    );
    await screen.findByText("Branch 2");
    expect(requests.every((url) => url.searchParams.has("thread_id"))).toBe(
      true,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Switch thread" }));
    const menu = await screen.findByRole("menu");
    const root = await within(menu).findByRole("menuitem", {
      name: "Main thread",
    });
    expect(root.getAttribute("href")).toBe(
      `/workspace/ws_1/sessions/ses_1/threads/thr_1?view=${level}`,
    );
    const branch = within(menu).getByRole("menuitem", {
      name: "Branch 1 From Main thread",
    });
    expect(branch.getAttribute("href")).toBe(
      `/workspace/ws_1/sessions/ses_1/threads/thr_fork?view=${level}`,
    );
    expect(
      within(menu)
        .getByRole("menuitem", { name: "Branch 2 From Branch 1" })
        .getAttribute("aria-current"),
    ).toBe("page");
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(3);
    await user.keyboard("{Escape}");
    rerender(
      <MemoryRouter>
        <ThreadBranches thread={nested} level={level} />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("button", { name: "Switch thread" }).textContent,
    ).toBe("Branch 2");
  },
);

it("links a nested fork to its immediate origin Run, not the session root or latest Run", async () => {
  show(
    <MemoryRouter>
      <BranchOrigin thread={nested} level="chat" />
    </MemoryRouter>,
  );
  const origin = await screen.findByRole("link", {
    name: "View origin in Branch 1",
  });
  expect(origin.getAttribute("href")).toBe(
    "/workspace/ws_1/sessions/ses_1/threads/thr_fork/runs/run_fork?view=chat",
  );
});

it("keeps a newly created branch identifiable while the list refreshes", () => {
  show(
    <MemoryRouter>
      <BranchOrigin thread={nested} level="debug" />
    </MemoryRouter>,
  );
  expect(screen.getByText("Branch")).toBeTruthy();
  expect(screen.queryByText("Branch 0")).toBeNull();
  expect(
    screen
      .getByRole("link", { name: "View original run" })
      .getAttribute("href"),
  ).toBe(
    "/workspace/ws_1/sessions/ses_1/threads/thr_fork/runs/run_fork?view=debug",
  );
});

it("retains the selected branch and global names across manually loaded pages", async () => {
  const many = [
    fixtureThread(),
    ...Array.from({ length: 55 }, (_, index) =>
      fixtureThread({
        id: `fork_${String(index + 1).padStart(2, "0")}`,
        origin: "fork",
        origin_thread_id: "thr_1",
        created_at: "2026-09-21T00:00:00Z",
      }),
    ),
  ];
  client.close();
  client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      return Response.json(fixtureNavigationPage(many, url.searchParams));
    },
  });
  show(
    <MemoryRouter>
      <ThreadBranches thread={many.at(-1)!} level="debug" />
    </MemoryRouter>,
  );
  await screen.findByText("Branch 55");
  expect(requests).toHaveLength(1);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Switch thread" }));
  const menu = await screen.findByRole("menu");
  await within(menu).findByRole("menuitem", {
    name: "Branch 49 From Main thread",
  });
  expect(
    within(menu)
      .getByRole("menuitem", { name: "Branch 55 From Main thread" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(requests).toHaveLength(2);
  await user.click(within(menu).getByRole("menuitem", { name: "Load more" }));
  await within(menu).findByRole("menuitem", {
    name: "Branch 54 From Main thread",
  });
  expect(within(menu).getAllByRole("menuitem")).toHaveLength(56);
  expect(requests).toHaveLength(3);
  expect(requests.at(-1)?.searchParams.get("cursor")).toBe("50");
});
