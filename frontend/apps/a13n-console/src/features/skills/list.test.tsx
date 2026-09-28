import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { SkillsPage } from "./page";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => false }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      options?.count === undefined
        ? key
        : key.replace("{{count}}", String(options.count)),
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const skill = (name: string, id: string) => ({
  id,
  workspace_id: "ws_test",
  name,
  description: "",
  version: 1,
  default_revision_id: `skr_${id}`,
  default_revision: {
    id: `skr_${id}`,
    number: 3,
    source: { kind: "github", repository: "example/skills" },
  },
  updated_at: "2026-09-09T00:00:00Z",
});

function renderList() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/workspace/test/skills"]}>
        <Routes>
          <Route path="/workspace/test/skills" element={<SkillsPage />} />
          <Route
            path="/workspace/test/skills/sk_review"
            element={<p>Readable skill detail</p>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { cache, user: userEvent.setup() };
}

it("searches the server from the first page and links by ID", async () => {
  http.GET.mockImplementation(async (path: string, options) => {
    const { q, cursor } = options.params.query;
    const response = path.endsWith("/agents")
      ? { items: [], next_cursor: null }
      : q
        ? {
            items:
              q === "review" ? [skill("Document helper", "sk_review")] : [],
            next_cursor: null,
          }
        : cursor
          ? { items: [skill("Second skill", "sk_second")], next_cursor: null }
          : {
              items: [skill("First skill", "sk_first")],
              next_cursor: "page-two",
            };
    return { data: response, response: new Response() };
  });
  const { cache, user } = renderList();
  const first = await screen.findByRole("link", { name: /First skill/ });
  const row = first.closest("tr")!;
  expect(within(row).getByText("GitHub")).toBeTruthy();
  expect(within(row).getByText("v3")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("link", { name: /Second skill/ });
  const search = screen.getByRole("searchbox", { name: "Search skills" });
  await user.click(search);
  await user.paste("REVIEW");
  await user.click(screen.getByRole("combobox", { name: "Source" }));
  await user.click(await screen.findByRole("option", { name: "GitHub" }));
  await waitFor(() =>
    expect(http.GET).toHaveBeenCalledWith(
      "/api/v1/skills",
      expect.objectContaining({
        params: {
          query: {
            q: "review",
            source: "github",
            archived: false,
            cursor: undefined,
          },
        },
      }),
    ),
  );
  const found = await screen.findByRole("link", { name: /Document helper/ });
  expect(found.getAttribute("href")).toBe("/workspace/test/skills/sk_review");
  const matching = http.GET.mock.calls.filter(
    ([, options]) => options.params.query?.q === "review",
  );
  expect(matching.length).toBeGreaterThan(0);
  expect(
    matching.every(([, options]) => options.params.query.cursor === undefined),
  ).toBe(true);
  await user.clear(search);
  await user.paste("missing");
  await screen.findByText("No matching skills");
  await user.clear(search);
  await screen.findByRole("link", { name: /First skill/ });
  await user.paste("review");
  await user.click(
    await screen.findByRole("link", { name: /Document helper/ }),
  );
  await waitFor(() =>
    expect(screen.getByText("Readable skill detail")).toBeTruthy(),
  );
  cache.clear();
});

it("counts the unarchived agents using each skill and adds archived skills on request", async () => {
  http.GET.mockImplementation(async (path: string, options) => {
    const query = options.params.query;
    const response = path.endsWith("/agents")
      ? query.skill_id === "sk_used"
        ? { items: [{ id: "ap_one" }, { id: "ap_two" }], next_cursor: "more" }
        : { items: [], next_cursor: null }
      : {
          items: [
            skill("Used skill", "sk_used"),
            skill("Idle skill", "sk_idle"),
            ...(query.archived === false ? [] : [skill("Old skill", "sk_old")]),
          ],
          next_cursor: null,
        };
    return { data: response, response: new Response() };
  });
  const { cache, user } = renderList();
  const used = (
    await screen.findByRole("link", { name: /Used skill/ })
  ).closest("tr")!;
  expect(await within(used).findByText("2+ agents")).toBeTruthy();
  const idle = screen.getByRole("link", { name: /Idle skill/ }).closest("tr")!;
  expect(await within(idle).findByText("Unused")).toBeTruthy();
  expect(http.GET).toHaveBeenCalledWith(
    "/api/v1/agents",
    expect.objectContaining({
      params: {
        query: { skill_id: "sk_used", archived: false, cursor: undefined },
      },
    }),
  );
  const archived = screen.getByRole("button", { name: "Archived" });
  await user.click(archived);
  expect(archived.getAttribute("aria-pressed")).toBe("true");
  await screen.findByRole("link", { name: /Old skill/ });
  expect(screen.getByRole("link", { name: /Used skill/ })).toBeTruthy();
  cache.clear();
});
