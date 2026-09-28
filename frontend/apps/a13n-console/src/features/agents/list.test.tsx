import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { Agents } from "./list";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: () => false,
  }),
}));
vi.mock("./queries", () => ({ useModelsByKey: () => new Map() }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const agent = (name: string, archived = false) => ({
  id: `ap_${name.toLowerCase()}`,
  name,
  description: "",
  source: "custom",
  image_url: null,
  default_revision_id: null,
  archived_at: archived ? "2026-09-20T00:00:00Z" : null,
  updated_at: "2026-09-20T00:00:00Z",
});

it("adds archived agents and searches on the Service, from the first page", async () => {
  http.GET.mockImplementation(async (_path: string, options) => {
    const { archived, q, cursor } = options.params.query;
    const items =
      archived === undefined
        ? [agent("First"), agent("Retired", true)]
        : q
          ? q === "scout"
            ? [agent("Scout")]
            : []
          : cursor
            ? [agent("Second")]
            : [agent("First")];
    return {
      data: {
        items,
        next_cursor: archived === undefined || q || cursor ? null : "two",
      },
      response: new Response(),
    };
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <Agents />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await screen.findByRole("link", { name: /First/ });
  await user.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("link", { name: /Second/ });
  await user.click(screen.getByRole("button", { name: "Archived" }));
  await screen.findByRole("link", { name: /Retired/ });
  expect(http.GET).toHaveBeenLastCalledWith(
    "/api/v1/agents",
    expect.objectContaining({
      params: {
        query: { limit: 30, cursor: undefined },
      },
    }),
  );
  await user.click(screen.getByRole("button", { name: "Archived" }));
  await user.type(screen.getByRole("searchbox"), "Scout");
  await screen.findByRole("link", { name: /Scout/ });
  expect(http.GET).toHaveBeenLastCalledWith(
    "/api/v1/agents",
    expect.objectContaining({
      params: {
        query: { limit: 30, cursor: undefined, archived: false, q: "scout" },
      },
    }),
  );
  await user.type(screen.getByRole("searchbox"), "s");
  await screen.findByText("No matching agents");
  await user.click(screen.getByRole("button", { name: "Clear" }));
  await waitFor(() =>
    expect(screen.getByRole("link", { name: /First/ })).toBeTruthy(),
  );
});
