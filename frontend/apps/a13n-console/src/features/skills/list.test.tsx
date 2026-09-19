import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { SkillsPage } from "./page";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => false }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("searches the server by name or key from the first page and links by key", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const user = userEvent.setup();
  const skill = (name: string, key: string) => ({
    id: `sk_${key}`,
    name,
    key,
    version: 1,
    default_version: 1,
    source_kind: "github",
    updated_at: "2026-09-09T00:00:00Z",
  });
  http.GET.mockImplementation(async (_path, options) => {
    const { q, cursor } = options.params.query;
    const response = q
      ? {
          items:
            q === "review" ? [skill("Document helper", "review-docs")] : [],
          next_cursor: null,
        }
      : cursor
        ? { items: [skill("Second skill", "second")], next_cursor: null }
        : { items: [skill("First skill", "first")], next_cursor: "page-two" };
    return { data: response, response: new Response() };
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/workspace/test/skills"]}>
        <Routes>
          <Route path="/workspace/test/skills" element={<SkillsPage />} />
          <Route
            path="/workspace/test/skills/review-docs"
            element={<p>Readable skill detail</p>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole("link", { name: /First skill/ });
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("link", { name: /Second skill/ });
  const search = screen.getByRole("searchbox", { name: "Search skills" });
  await user.type(search, "REVIEW");
  await user.click(screen.getByRole("combobox", { name: "Source" }));
  await user.click(await screen.findByRole("option", { name: "GitHub" }));
  await waitFor(() =>
    expect(http.GET).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        params: expect.objectContaining({
          query: expect.objectContaining({
            q: "review",
            source_kind: "github",
            cursor: undefined,
          }),
        }),
      }),
    ),
  );
  const found = await screen.findByRole("link", { name: /Document helper/ });
  expect(found.getAttribute("href")).toBe("/workspace/test/skills/review-docs");
  const matching = http.GET.mock.calls.filter(
    ([, options]) => options.params.query?.q === "review",
  );
  expect(matching.length).toBeGreaterThan(0);
  expect(
    matching.every(([, options]) => options.params.query.cursor === undefined),
  ).toBe(true);
  await user.clear(search);
  await user.type(search, "missing");
  await screen.findByText("No matching skills");
  await user.clear(search);
  await screen.findByRole("link", { name: /First skill/ });
  await user.type(search, "review");
  await user.click(
    await screen.findByRole("link", { name: /Document helper/ }),
  );
  await waitFor(() =>
    expect(screen.getByText("Readable skill detail")).toBeTruthy(),
  );
  cache.clear();
});
