// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceFiles } from "./page";

const mock = vi.hoisted(() => ({
  GET: vi.fn(),
  DELETE: vi.fn(),
  write: false,
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ workspace: () => mock }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: () => mock.write,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name) => values?.[name] ?? ""),
    i18n: { resolvedLanguage: "en" },
  }),
}));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  mock.write = false;
});

const asset = {
  id: "ast_report",
  name: "report.txt",
  workspace_id: "ws_test",
  content_type: "text/plain",
  size: 5,
  creator_name: "Ada",
  created_by_id: "usr_ada",
  thread_id: "thd_source",
  session_id: "ses_source",
  source: { run_id: "run_source" },
  retired_at: null,
  version: 1,
  created_at: "2026-11-25T10:00:00Z",
};
const totals = {
  total_bytes: 8,
  active_bytes: 5,
  retired_bytes: 0,
  staged_upload_bytes: 3,
};
const response = (data: unknown) => ({ data, response: new Response() });

function showFiles() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <WorkspaceFiles />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

it("uses a server query on the first cursor after searching from a later page", async () => {
  mock.GET.mockImplementation(
    async (
      path: string,
      options: { params?: { query: { query?: string; cursor?: string } } },
    ) => {
      if (path === "/api/v1/storage")
        return response({ ...totals, by_creator: [] });
      const query = options.params?.query;
      if (query?.query === "report")
        return response({ items: [asset], total_count: 1, next_cursor: null });
      return response({
        items: [
          { ...asset, name: query?.cursor ? "page-two.txt" : "page-one.txt" },
        ],
        total_count: 2,
        next_cursor: query?.cursor ? null : "next-page",
      });
    },
  );
  const user = showFiles();
  await user.click(await screen.findByRole("button", { name: "Next" }));
  await screen.findByText("page-two.txt");
  await user.type(screen.getByRole("searchbox"), "report");
  await screen.findByText("report.txt");
  expect(mock.GET).toHaveBeenLastCalledWith("/api/v1/assets", {
    params: {
      query: {
        query: "report",
        order: "newest",
        source_kind: undefined,
        created_by_id: undefined,
        status: "all",
        cursor: undefined,
        limit: 25,
      },
    },
    signal: expect.any(AbortSignal),
  });
  expect(
    screen.getByRole("link", { name: "Agent run" }).getAttribute("href"),
  ).toBe("/workspace/ws_test/sessions/ses_source/threads/thd_source");
  expect(
    screen.getByRole<HTMLButtonElement>("button", {
      name: "Download report.txt",
    }).disabled,
  ).toBe(false);
  expect(screen.queryByRole("button", { name: "Actions" })).toBeNull();
});

it("confirms retirement with the row's version and refreshes storage and the files list", async () => {
  mock.write = true;
  let retired = false;
  mock.GET.mockImplementation(async (path: string) =>
    path === "/api/v1/storage"
      ? response({ ...totals, by_creator: [], retired_bytes: retired ? 5 : 0 })
      : response({
          items: [{ ...asset, retired_at: retired ? asset.created_at : null }],
          total_count: 1,
          next_cursor: null,
        }),
  );
  mock.DELETE.mockImplementation(async () => {
    retired = true;
    return response(asset);
  });
  const user = showFiles();
  await user.click(await screen.findByRole("button", { name: "Actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Retire" }));
  expect(mock.DELETE).not.toHaveBeenCalled();
  const dialog = await screen.findByRole("dialog", { name: "Retire file" });
  await user.click(within(dialog).getByRole("button", { name: "Retire" }));
  expect(mock.DELETE).toHaveBeenCalledWith("/api/v1/assets/{asset_id}", {
    params: {
      path: { asset_id: asset.id },
      header: { "If-Match": '"ast_report:1"' },
    },
  });
  await within(screen.getByRole("table")).findByText("Retired");
  expect(
    mock.GET.mock.calls.filter(([path]) => path === "/api/v1/storage"),
  ).toHaveLength(2);
});
